from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class PlaylistRecord:
    url: str
    status: str  # valid|invalid|unknown
    last_checked: str
    source: str
    hash: str
    channels_count: int = 0
    fail_count: int = 0
    last_http_status: Optional[int] = None
    last_error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "PlaylistRecord":
        return PlaylistRecord(
            url=str(d.get("url", "")),
            status=str(d.get("status", "unknown")),
            last_checked=str(d.get("last_checked", "")),
            source=str(d.get("source", "unknown")),
            hash=str(d.get("hash", "")),
            channels_count=int(d.get("channels_count", 0) or 0),
            fail_count=int(d.get("fail_count", 0) or 0),
            last_http_status=d.get("last_http_status", None),
            last_error=d.get("last_error", None),
        )

