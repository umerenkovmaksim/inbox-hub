from inbox_hub.services.health import HealthService
from inbox_hub.services.jobs import JobRunner, SyncReport


class Boom:
    name = "telegram"

    async def sync(self):
        raise RuntimeError("down")


class Ok:
    name = "sdo"

    async def sync(self):
        return SyncReport("sdo", new=2)


async def test_runner_records_and_swallows(sessions, clock):
    health = HealthService(sessions, clock=clock)
    runner = JobRunner(health)
    bad = await runner.run(Boom())
    good = await runner.run(Ok())
    assert bad.error == "RuntimeError('down')"
    assert good.new == 2
    rows = {h.source: h for h in await health.snapshot()}
    assert rows["telegram"].last_ok_at is None and rows["telegram"].last_error
    assert rows["sdo"].last_ok_at == clock() and rows["sdo"].last_report["new"] == 2
