from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
import os


def _getenv(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


def _getenv_int(name: str, default: int) -> int:
    v = _getenv(name)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session: str
    telegram_targets: List[str]
    telegram_limit: int

    http_timeout_seconds: float
    http_max_retries: int
    stream_sample_validate: int
    max_consecutive_fails: int

    cron_collect: str
    cron_revalidate: str
    cron_aggregate: str

    data_dir: Path
    db_json: Path
    agg_m3u: Path


def load_settings() -> Settings:
    load_dotenv()

    api_id = _getenv_int("TELEGRAM_API_ID", 0)
    api_hash = _getenv("TELEGRAM_API_HASH", "") or ""
    session = _getenv("TELEGRAM_SESSION", "bot_iptv") or "bot_iptv"

    targets = os.getenv("TELEGRAM_TARGETS", "").splitlines()
    targets = [t.strip() for t in targets if t.strip()]

    telegram_limit = _getenv_int("TELEGRAM_LIMIT", 200)

    http_timeout = float(_getenv("HTTP_TIMEOUT_SECONDS", "10") or "10")
    http_max_retries = _getenv_int("HTTP_MAX_RETRIES", 3)
    stream_sample_validate = _getenv_int("STREAM_SAMPLE_VALIDATE", 0)
    max_consecutive_fails = _getenv_int("MAX_CONSECUTIVE_FAILS", 3)

    cron_collect = _getenv("CRON_COLLECT", "0 */6 * * *") or "0 */6 * * *"
    cron_revalidate = _getenv("CRON_REVALIDATE", "30 */6 * * *") or "30 */6 * * *"
    cron_aggregate = _getenv("CRON_AGGREGATE", "45 */6 * * *") or "45 */6 * * *"

    data_dir = Path(_getenv("DATA_DIR", "./data") or "./data")
    db_json = Path(_getenv("DB_JSON", str(data_dir / "lists.json")) or str(data_dir / "lists.json"))
    agg_m3u = Path(_getenv("AGG_M3U", str(data_dir / "ssiptv_consolidated.m3u")) or str(data_dir / "ssiptv_consolidated.m3u"))

    return Settings(
        telegram_api_id=api_id,
        telegram_api_hash=api_hash,
        telegram_session=session,
        telegram_targets=targets,
        telegram_limit=telegram_limit,
        http_timeout_seconds=http_timeout,
        http_max_retries=http_max_retries,
        stream_sample_validate=stream_sample_validate,
        max_consecutive_fails=max_consecutive_fails,
        cron_collect=cron_collect,
        cron_revalidate=cron_revalidate,
        cron_aggregate=cron_aggregate,
        data_dir=data_dir,
        db_json=db_json,
        agg_m3u=agg_m3u,
    )

