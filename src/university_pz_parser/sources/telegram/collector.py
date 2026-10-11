import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from telethon import events
from telethon.utils import get_display_name

from university_pz_parser.enums import Source
from university_pz_parser.services.ingest import IngestService, IngestStatus
from university_pz_parser.services.jobs import SyncReport
from university_pz_parser.settings import TelegramSettings
from university_pz_parser.sources.telegram.mapping import to_incoming

log = logging.getLogger(__name__)

# Marked ids of channels and supergroups. Messages there have per-chat ids;
# everywhere else (private chats, basic groups) ids are unique per account.
CHANNEL_PREFIX = "-100"


@dataclass(frozen=True)
class Upsert:
    chat_id: int
    message: Any
    title: str | None = None
    report: SyncReport | None = None


@dataclass(frozen=True)
class Deleted:
    chat_id: int | None
    msg_ids: tuple[int, ...]


class TelegramCollector:
    """Receives Telegram updates and hands them to the writer through one queue.

    Event handlers never call the API: they only enqueue. A single consumer
    task drains the queue, so downloads and writes happen strictly one by one.
    """

    def __init__(self, client, ingest: IngestService, settings: TelegramSettings):
        self.client = client
        self.ingest = ingest
        self.settings = settings
        self.chats = frozenset(settings.source_ids)
        self.queue: asyncio.Queue[Upsert | Deleted] = asyncio.Queue()
        self.titles: dict[int, str] = {}

    async def load_titles(self) -> None:
        known = await self.ingest.channel_titles(Source.TELEGRAM)
        for external_id, title in known.items():
            self.titles.setdefault(int(external_id), title)

    def register_handlers(self) -> None:
        chats = list(self.chats)
        self.client.add_event_handler(self.on_message, events.NewMessage(chats=chats))
        self.client.add_event_handler(
            self.on_message, events.MessageEdited(chats=chats)
        )
        # No chat filter: private chats and basic groups report chat_id=None.
        self.client.add_event_handler(self.on_deleted, events.MessageDeleted())

    async def on_message(self, event) -> None:
        # event.chat is only what the update itself carried; no request is made.
        title = get_display_name(event.chat) if event.chat is not None else None
        self.queue.put_nowait(Upsert(event.chat_id, event.message, title))

    async def on_deleted(self, event) -> None:
        chat_id = event.chat_id
        if chat_id is not None and chat_id not in self.chats:
            return
        self.queue.put_nowait(Deleted(chat_id, tuple(event.deleted_ids or ())))

    async def consume(self) -> None:
        while True:
            op = await self.queue.get()
            try:
                await self.apply(op)
            except Exception:
                log.exception("telegram write failed: %r", op)
            finally:
                self.queue.task_done()

    async def apply(self, op: Upsert | Deleted) -> IngestStatus | None:
        if isinstance(op, Deleted):
            await self.ingest.mark_deleted(
                Source.TELEGRAM,
                op.msg_ids,
                channel_external_id=None if op.chat_id is None else str(op.chat_id),
                exclude_prefix=CHANNEL_PREFIX if op.chat_id is None else None,
            )
            return None
        if op.title:
            self.titles[op.chat_id] = op.title
        title = self.titles.get(op.chat_id, str(op.chat_id))
        item = to_incoming(op.chat_id, title, op.message, self.settings.download_kinds)
        status = None
        if item is not None:
            status = await self.ingest.ingest(item)
            if op.report is not None:
                op.report.count(status)
        await self.ingest.advance_cursor(
            Source.TELEGRAM, str(op.chat_id), op.message.id, title
        )
        return status
