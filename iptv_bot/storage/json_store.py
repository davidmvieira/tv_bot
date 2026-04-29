from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import orjson

from .models import PlaylistRecord, utc_now_iso


def url_hash(url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"sha256:{h}"


def url_cache_filename(url: str) -> str:
    h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"cached_{h}.m3u"


class JsonStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Dict[str, PlaylistRecord]:
        if not self.path.exists():
            return {}
        raw = self.path.read_bytes()
        if not raw:
            return {}
        data = orjson.loads(raw)
        if not isinstance(data, list):
            return {}
        out: Dict[str, PlaylistRecord] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            rec = PlaylistRecord.from_dict(item)
            if rec.hash:
                out[rec.hash] = rec
        return out

    def save(self, records: Iterable[PlaylistRecord]) -> None:
        arr = [r.to_dict() for r in records]
        self.path.write_bytes(orjson.dumps(arr, option=orjson.OPT_INDENT_2))

    def upsert_urls(self, urls: List[str], source: str = "telegram") -> List[PlaylistRecord]:
        db = self.load()
        added: List[PlaylistRecord] = []
        now = utc_now_iso()
        for u in urls:
            h = url_hash(u)
            if h in db:
                continue
            rec = PlaylistRecord(
                url=u,
                status="unknown",
                last_checked="",
                source=source,
                hash=h,
                channels_count=0,
                fail_count=0,
                last_http_status=None,
                last_error=None,
            )
            db[h] = rec
            added.append(rec)
        self.save(db.values())
        return added

    def update_record(
        self,
        url: str,
        *,
        status: str,
        channels_count: int = 0,
        http_status: Optional[int] = None,
        error: Optional[str] = None,
        fail_inc: bool = False,
    ) -> PlaylistRecord:
        db = self.load()
        h = url_hash(url)
        rec = db.get(
            h,
            PlaylistRecord(
                url=url,
                status="unknown",
                last_checked="",
                source="unknown",
                hash=h,
            ),
        )

        rec.status = status
        rec.last_checked = utc_now_iso()
        rec.channels_count = int(channels_count or 0)
        rec.last_http_status = http_status
        rec.last_error = error
        rec.fail_count = (rec.fail_count + 1) if fail_inc else 0

        db[h] = rec
        self.save(db.values())
        return rec

    def mark_invalid_after_failures(self, max_fails: int) -> int:
        db = self.load()
        changed = 0
        for rec in db.values():
            if rec.fail_count >= max_fails and rec.status != "invalid":
                rec.status = "invalid"
                changed += 1
        if changed:
            self.save(db.values())
        return changed

