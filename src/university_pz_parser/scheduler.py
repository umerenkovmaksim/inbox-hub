from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from university_pz_parser.container import Container


def build_scheduler(c: Container) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=c.settings.tz)
    common = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 600}
    if "sdo" in c.jobs:
        scheduler.add_job(
            c.runner.run,
            CronTrigger(hour="5-22/1", minute=20),
            args=[c.jobs["sdo"]],
            id="sdo",
            **common,
        )
    return scheduler
