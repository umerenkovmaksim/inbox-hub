from inbox_hub.container import Container
from inbox_hub.scheduler import build_scheduler


async def test_only_polled_sources_are_scheduled(settings, engine):
    c = await Container.build(settings, with_sources=False)
    c.jobs["sdo"] = object()
    sched = build_scheduler(c)
    assert {j.id for j in sched.get_jobs()} == {"sdo"}
    await c.stop()


async def test_serve_has_no_telegram_job(settings, engine):
    c = await Container.build(settings)
    assert set(c.jobs) == {"sdo"}
    await c.stop()
