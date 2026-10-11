import asyncio
import logging
from collections.abc import Awaitable, Callable

from telethon.errors import FloodWaitError

from inbox_hub.enums import Source
from inbox_hub.services.health import HealthService
from inbox_hub.services.jobs import SyncReport
from inbox_hub.sources.telegram.collector import TelegramCollector, Upsert

log = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]


class Reconciler:
    """Safety net for messages the update stream missed.

    One ``iter_dialogs`` pass compares each whitelisted chat's top message id
    with the stored cursor; history is read only for chats that are behind,
    one chat at a time, through the collector's queue.
    """

    def __init__(
        self,
        client,
        collector: TelegramCollector,
        health: HealthService,
        sleep: Sleep = asyncio.sleep,
    ):
        self.client = client
        self.collector = collector
        self.ingest = collector.ingest
        self.settings = collector.settings
        self.health = health
        self.sleep = sleep

    async def run_forever(self) -> None:
        interval = self.settings.reconcile_interval_hours * 3600
        while True:
            try:
                await self.run_once()
            except FloodWaitError as exc:
                log.warning("FloodWait %ss on iter_dialogs", exc.seconds)
                await self._record(SyncReport("telegram", error=f"FloodWait {exc}"))
                await self.sleep(exc.seconds)
            except Exception as exc:
                log.exception("telegram reconcile failed")
                await self._record(SyncReport("telegram", error=repr(exc)))
            await self._pause(interval)

    async def _pause(self, seconds: float) -> None:
        """Sleep until the next pass, or until a chat is added to the catalog."""
        timer = asyncio.ensure_future(self.sleep(seconds))
        woken = asyncio.ensure_future(self.collector.chats_added.wait())
        _, pending = await asyncio.wait(
            {timer, woken}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()

    async def run_once(self) -> SyncReport:
        report = SyncReport("telegram")
        await self.collector.refresh_chats()
        self.collector.chats_added.clear()  # this pass covers them
        behind = []
        seen: set[int] = set()
        dialogs: list[tuple[int, str, str]] = []
        async for dialog in self.client.iter_dialogs():
            if dialog.is_group or dialog.is_channel:
                kind = "group" if dialog.is_group else "channel"
                dialogs.append((dialog.id, dialog.name, kind))
            if dialog.id not in self.collector.chats:
                continue
            seen.add(dialog.id)
            self.collector.titles[dialog.id] = dialog.name
            top = dialog.message.id if dialog.message is not None else 0
            cursor = await self.ingest.channel_cursor(Source.TELEGRAM, str(dialog.id))
            if top > cursor:
                behind.append((dialog, top))
        await self.ingest.record_dialogs(dialogs)
        for chat_id in sorted(self.collector.chats - seen):
            report.warnings.append(f"chat {chat_id} not found among dialogs")
        for dialog, top in behind:
            await self._read_chat(dialog, top, report)
        log.info("telegram reconcile: %s", report.as_dict())
        await self._record(report)
        return report

    async def _read_chat(self, dialog, top: int, report: SyncReport) -> None:
        for attempt in (1, 2):
            try:
                await self._read_new(dialog, top, report)
                return
            except FloodWaitError as exc:
                report.warnings.append(f"FloodWait {exc.seconds}s on {dialog.id}")
                await self.collector.queue.join()
                if attempt == 2:
                    return
                await self.sleep(exc.seconds)
            except Exception as exc:
                log.exception("telegram chat %s failed", dialog.id)
                report.warnings.append(f"chat {dialog.id}: {exc!r}")
                await self.collector.queue.join()
                return

    async def _read_new(self, dialog, top: int, report: SyncReport) -> None:
        cursor = await self.ingest.channel_cursor(Source.TELEGRAM, str(dialog.id))
        if cursor:
            kwargs = {"min_id": cursor}
        else:
            kwargs = {"offset_date": self.settings.backfill_from}
        async for m in self.client.iter_messages(
            dialog.entity, reverse=True, wait_time=1, **kwargs
        ):
            self.collector.queue.put_nowait(Upsert(dialog.id, m, dialog.name, report))
        await self.collector.queue.join()
        # Everything up to the dialog's top message has been read, including
        # ids that were skipped or deleted, so the next pass starts after it.
        await self.ingest.advance_cursor(Source.TELEGRAM, str(dialog.id), top)

    async def _record(self, report: SyncReport) -> None:
        try:
            await self.health.record(report)
        except Exception:
            log.exception("health record failed")
