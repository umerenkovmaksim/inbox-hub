import asyncio
import contextlib
import logging
import signal

from university_pz_parser.container import Container
from university_pz_parser.services.jobs import SyncReport
from university_pz_parser.settings import Settings
from university_pz_parser.sources.telegram.client import (
    NotAuthorizedError,
    SessionLock,
    TelegramClientManager,
)
from university_pz_parser.sources.telegram.collector import TelegramCollector
from university_pz_parser.sources.telegram.reconciler import Reconciler

log = logging.getLogger(__name__)

SHUTDOWN_DRAIN = 30


async def run_collector(settings: Settings) -> None:
    """The only long-lived process that talks to Telegram."""
    with SessionLock(settings.telegram.lock_path):
        c = await Container.build(settings, with_sources=False)
        tg = TelegramClientManager(settings.telegram)
        try:
            collector = TelegramCollector(tg.client, c.ingest, settings.telegram)
            await collector.load_titles()
            # Handlers go in before connecting so catch_up updates reach them.
            collector.register_handlers()
            try:
                await tg.start()
            except NotAuthorizedError as exc:
                await c.health.record(SyncReport("telegram", error=repr(exc)))
                raise

            async def shutdown() -> None:
                # Let queued writes (and their downloads) finish while still
                # connected; whatever is left is recovered by catch_up/reconcile.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(collector.queue.join(), SHUTDOWN_DRAIN)
                await tg.client.disconnect()

            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.ensure_future(shutdown()))
            reconciler = Reconciler(tg.client, collector, c.health)
            tasks = [
                asyncio.create_task(collector.consume()),
                asyncio.create_task(reconciler.run_forever()),
            ]
            try:
                await tg.client.run_until_disconnected()
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with contextlib.suppress(asyncio.CancelledError):
                        await task
        finally:
            await tg.stop()
            await c.stop()
    log.info("telegram collector stopped")
