import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta

from telethon import events
from telethon.errors import FloodWaitError
from telethon.utils import get_display_name

from inbox_hub.enums import Source
from inbox_hub.services.catalog import CatalogService
from inbox_hub.services.ingest import IngestService, utcnow
from inbox_hub.services.jobs import SyncReport
from inbox_hub.settings import TelegramSettings
from inbox_hub.sources.telegram.mapping import to_incoming

log = logging.getLogger(__name__)

TRACKED_TTL_SECONDS = 60


class TelegramSource:
    name = "telegram"

    def __init__(
        self,
        client,
        ingest: IngestService,
        catalog: CatalogService,
        settings: TelegramSettings,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.client = client
        self.ingest = ingest
        self.catalog = catalog
        self.settings = settings
        self.clock = clock
        self._tracked: set[int] = set()
        self._tracked_at: float | None = None

    async def refresh_tracked(self) -> list[int]:
        chat_ids = await self.catalog.tracked_chat_ids()
        self._tracked = set(chat_ids)
        self._tracked_at = time.monotonic()
        return chat_ids

    async def _is_tracked(self, chat_id: int) -> bool:
        if (
            self._tracked_at is None
            or time.monotonic() - self._tracked_at > TRACKED_TTL_SECONDS
        ):
            await self.refresh_tracked()
        return chat_id in self._tracked

    async def sync(self) -> SyncReport:
        report = SyncReport(self.name)
        await self.catalog.seed_tracked_chats(self.settings.source_ids)
        for chat_id in await self.refresh_tracked():
            try:
                entity = await self.client.get_entity(chat_id)
                title = get_display_name(entity) or getattr(
                    entity, "title", str(chat_id)
                )
                await self._catch_up(chat_id, entity, title, report)
                await self._recheck(chat_id, entity, title, report)
            except FloodWaitError as exc:
                report.warnings.append(f"FloodWait {exc.seconds}s on {chat_id}")
                break
            except Exception as exc:
                log.exception("telegram chat %s failed", chat_id)
                report.warnings.append(f"chat {chat_id}: {exc!r}")
        return report

    async def _catch_up(
        self, chat_id: int, entity, title: str, report: SyncReport
    ) -> None:
        cursor = await self.ingest.channel_cursor(Source.TELEGRAM, str(chat_id))
        if cursor:
            kwargs = {"min_id": cursor, "reverse": True}
        else:
            kwargs = {"offset_date": self.settings.backfill_from, "reverse": True}
        async for m in self.client.iter_messages(entity, **kwargs):
            await self._ingest(chat_id, title, m, report)
            await self.ingest.advance_cursor(Source.TELEGRAM, str(chat_id), m.id)

    async def _recheck(
        self, chat_id: int, entity, title: str, report: SyncReport
    ) -> None:
        since = self.clock() - timedelta(days=self.settings.edit_recheck_days)
        async for m in self.client.iter_messages(
            entity, offset_date=since, reverse=True
        ):
            if m.edit_date:
                await self._ingest(chat_id, title, m, report)

    async def _ingest(self, chat_id: int, title: str, m, report: SyncReport) -> None:
        item = to_incoming(chat_id, title, m)
        if item is not None:
            report.count(await self.ingest.ingest(item))

    def register_handlers(self) -> None:
        # No `chats=` filter: tracked chats live in the DB and change at runtime,
        # so handle_event checks them itself.
        self.client.add_event_handler(self.handle_event, events.NewMessage())
        self.client.add_event_handler(self.handle_event, events.MessageEdited())

    async def handle_event(self, event) -> None:
        try:
            if not await self._is_tracked(event.chat_id):
                return
            chat = await event.get_chat()
            title = get_display_name(chat) or str(event.chat_id)
            item = to_incoming(event.chat_id, title, event.message)
            if item is not None:
                await self.ingest.ingest(item)
        except Exception:
            log.exception("live telegram event failed")
