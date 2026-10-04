import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from telethon import events
from telethon.errors import FloodWaitError
from telethon.utils import get_display_name

from university_pz_parser.enums import Source
from university_pz_parser.services.ingest import IngestService, utcnow
from university_pz_parser.services.jobs import SyncReport
from university_pz_parser.settings import TelegramSettings
from university_pz_parser.sources.telegram.mapping import to_incoming

log = logging.getLogger(__name__)


class TelegramSource:
    name = "telegram"

    def __init__(
        self,
        client,
        ingest: IngestService,
        settings: TelegramSettings,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.client = client
        self.ingest = ingest
        self.settings = settings
        self.clock = clock

    async def sync(self) -> SyncReport:
        report = SyncReport(self.name)
        for chat_id in self.settings.source_ids:
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
        chats = list(self.settings.source_ids)
        self.client.add_event_handler(self.handle_event, events.NewMessage(chats=chats))
        self.client.add_event_handler(
            self.handle_event, events.MessageEdited(chats=chats)
        )

    async def handle_event(self, event) -> None:
        try:
            chat = await event.get_chat()
            title = get_display_name(chat) or str(event.chat_id)
            item = to_incoming(event.chat_id, title, event.message)
            if item is not None:
                await self.ingest.ingest(item)
                if not event.message.edit_date:
                    await self.ingest.advance_cursor(
                        Source.TELEGRAM, str(event.chat_id), event.message.id
                    )
        except Exception:
            log.exception("live telegram event failed")
