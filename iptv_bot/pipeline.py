from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List, Tuple

from .collector.telegram_collector import collect_recent_messages
from .logging_utils import get_logger
from .parser.url_extractor import extract_candidate_urls_from_messages
from .storage.json_store import JsonStore, url_cache_filename
from .validator.m3u_validator import validate_m3u_url
from .aggregator.m3u_aggregator import aggregate_m3u_from_urls
from .config import Settings


log = get_logger(__name__)


@dataclass(frozen=True)
class RunStats:
    messages: int
    extracted_urls: int
    newly_added: int
    validated: int
    valid: int
    invalid: int


async def collect_and_register(settings: Settings, store: JsonStore) -> Tuple[int, int]:
    if settings.telegram_api_id <= 0 or not settings.telegram_api_hash:
        raise RuntimeError("Telegram credentials missing: set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env")

    msgs = await collect_recent_messages(
        api_id=settings.telegram_api_id,
        api_hash=settings.telegram_api_hash,
        session_name=settings.telegram_session,
        targets=settings.telegram_targets,
        limit_per_target=settings.telegram_limit,
    )
    http_msgs = sum(1 for m in msgs if "http://" in m.lower() or "https://" in m.lower())
    urls = extract_candidate_urls_from_messages(msgs)
    added = store.upsert_urls(urls, source="telegram")
    log.info(
        "Collected %d messages (%d with http), extracted %d URLs, added %d new URLs",
        len(msgs),
        http_msgs,
        len(urls),
        len(added),
    )
    return len(msgs), len(added)


async def validate_all(settings: Settings, store: JsonStore) -> RunStats:
    db = store.load()
    rec_by_url = {rec.url: rec for rec in db.values()}
    urls = list(rec_by_url.keys())
    if not urls:
        return RunStats(messages=0, extracted_urls=0, newly_added=0, validated=0, valid=0, invalid=0)

    sem = asyncio.Semaphore(10)
    ok = 0
    bad = 0

    async def _validate_one(u: str) -> None:
        nonlocal ok, bad
        cache_path = settings.data_dir / "cached" / url_cache_filename(u)
        async with sem:
            res = await validate_m3u_url(
                u,
                timeout_seconds=settings.http_timeout_seconds,
                stream_sample_validate=settings.stream_sample_validate,
            )
            if res.ok:
                ok += 1
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                if res.content is not None:
                    cache_path.write_text(res.content, encoding="utf-8")
                store.update_record(
                    u,
                    status="valid",
                    channels_count=res.channels_count,
                    http_status=res.http_status,
                    error=None,
                    fail_inc=False,
                )
            else:
                bad += 1
                prev_fails = rec_by_url.get(u).fail_count if rec_by_url.get(u) else 0
                new_fails = prev_fails + 1
                status = "invalid" if new_fails >= settings.max_consecutive_fails else "unknown"
                if cache_path.exists():
                    cache_path.unlink()
                store.update_record(
                    u,
                    status=status,
                    channels_count=res.channels_count,
                    http_status=res.http_status,
                    error=res.error,
                    fail_inc=True,
                )

    await asyncio.gather(*[_validate_one(u) for u in urls])
    log.info("Validated %d playlists: %d valid, %d invalid", len(urls), ok, bad)
    return RunStats(messages=0, extracted_urls=0, newly_added=0, validated=len(urls), valid=ok, invalid=bad)


async def aggregate_valid(settings: Settings, store: JsonStore) -> int:
    db = store.load()
    urls = [rec.url for rec in db.values() if rec.status == "valid"]
    count = await aggregate_m3u_from_urls(urls, settings.agg_m3u, timeout_seconds=settings.http_timeout_seconds)
    log.info("Aggregated %d entries into %s", count, str(settings.agg_m3u))
    return count


async def run_once(settings: Settings) -> None:
    store = JsonStore(settings.db_json)
    await collect_and_register(settings, store)
    await validate_all(settings, store)
    await aggregate_valid(settings, store)

