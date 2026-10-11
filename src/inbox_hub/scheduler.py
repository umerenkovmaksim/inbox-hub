from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from inbox_hub.container import Container


def build_scheduler(c: Container) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=c.settings.tz)
    common = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 600}
    if "telegram" in c.jobs:
        scheduler.add_job(
            c.runner.run,
            CronTrigger(minute=5),
            args=[c.jobs["telegram"]],
            id="telegram_hourly",
            **common,
        )
    if "sdo" in c.jobs:
        scheduler.add_job(
            c.runner.run,
            CronTrigger(hour="5-22/1", minute=20),
            args=[c.jobs["sdo"]],
            id="sdo",
            **common,
        )
    return scheduler
