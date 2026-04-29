from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import Settings
from ..logging_utils import get_logger
from ..pipeline import aggregate_valid, collect_and_register, validate_all
from ..storage.json_store import JsonStore


log = get_logger(__name__)


def _cron(expr: str) -> CronTrigger:
    # 5 campos: min hour day month dow
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron expression (expected 5 fields): {expr!r}")
    minute, hour, day, month, dow = parts
    return CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)


async def run_scheduler(settings: Settings) -> None:
    store = JsonStore(settings.db_json)

    async def job_collect() -> None:
        try:
            await collect_and_register(settings, store)
        except Exception as e:  # noqa: BLE001
            log.exception("collect job failed: %s", e)

    async def job_revalidate() -> None:
        try:
            await validate_all(settings, store)
        except Exception as e:  # noqa: BLE001
            log.exception("revalidate job failed: %s", e)

    async def job_aggregate() -> None:
        try:
            await aggregate_valid(settings, store)
        except Exception as e:  # noqa: BLE001
            log.exception("aggregate job failed: %s", e)

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(job_collect, _cron(settings.cron_collect), name="collect")
    scheduler.add_job(job_revalidate, _cron(settings.cron_revalidate), name="revalidate")
    scheduler.add_job(job_aggregate, _cron(settings.cron_aggregate), name="aggregate")

    scheduler.start()
    log.info("Scheduler started (UTC). Press Ctrl+C to stop.")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        scheduler.shutdown(wait=False)

