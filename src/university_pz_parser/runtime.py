import asyncio
from contextlib import asynccontextmanager

from university_pz_parser.container import Container
from university_pz_parser.scheduler import build_scheduler
from university_pz_parser.settings import get_settings


@asynccontextmanager
async def run_server_lifespan(app):
    c = await Container.build(get_settings())
    app.state.container = c
    scheduler = build_scheduler(c)
    scheduler.start()
    startup = [asyncio.create_task(c.runner.run(job)) for job in c.jobs.values()]
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        for task in startup:
            task.cancel()
        await c.stop()
