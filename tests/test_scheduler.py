from inbox_hub.container import Container
from inbox_hub.scheduler import build_scheduler


async def test_jobs_registered(settings, engine):
    c = await Container.build(settings, with_sources=False)
    c.jobs["telegram"] = object()
    c.jobs["sdo"] = object()
    sched = build_scheduler(c)
    assert {j.id for j in sched.get_jobs()} == {"telegram_hourly", "sdo"}
    await c.stop()
