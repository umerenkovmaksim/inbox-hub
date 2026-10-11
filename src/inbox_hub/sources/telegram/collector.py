import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from telethon import events
from telethon.utils import get_display_name

from inbox_hub.enums import Source
from inbox_hub.services.catalog import CatalogService
from inbox_hub.services.ingest import IngestService, IngestStatus
from inbox_hub.services.jobs import SyncReport
from inbox_hub.settings import TelegramSettings
from inbox_hub.sources.telegram.mapping import to_incoming

log = logging.getLogger(__name__)

# Marked ids of channels and supergroups. Messages there have per-chat ids;
# everywhere else (private chats, basic groups) ids are unique per account.
CHANNEL_PREFIX = "-100"

# How often the tracked-chat list is re-read from the database. CLI and API
# change it from other processes.
TRACKED_REFRESH_SECONDS = 60


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

    def __init__(
        self,
        client,
        ingest: IngestService,
        catalog: CatalogService,
        settings: TelegramSettings,
    ):
        self.client = client
        self.ingest = ingest
        self.catalog = catalog
        self.settings = settings
        self.chats: frozenset[int] = frozenset()
        self.chats_added = asyncio.Event()
        self.queue: asyncio.Queue[Upsert | Deleted] = asyncio.Queue()
        self.titles: dict[int, str] = {}

    async def refresh_chats(self) -> frozenset[int]:
        """Re-read tracked chats from the DB; returns the ones that are new."""
        chats = frozenset(await self.catalog.tracked_chat_ids())
        added = chats - self.chats
        self.chats = chats
        if added:
            self.chats_added.set()
        return added

    async def watch_chats(self, sleep=asyncio.sleep) -> None:
        while True:
            await sleep(TRACKED_REFRESH_SECONDS)
            try:
                added = await self.refresh_chats()
            except Exception:
                log.exception("tracked chats refresh failed")
                continue
            if added:
                log.info("new tracked chats: %s", sorted(added))

    async def load_titles(self) -> None:
        known = await self.ingest.channel_titles(Source.TELEGRAM)
        for external_id, title in known.items():
            self.titles.setdefault(int(external_id), title)

    def register_handlers(self) -> None:
        # No `chats=` filter: tracked chats change at runtime, so the handlers
        # check the in-memory set; MessageDeleted in private chats and basic
        # groups has no chat_id at all.
        self.client.add_event_handler(self.on_message, events.NewMessage())
        self.client.add_event_handler(self.on_message, events.MessageEdited())
        self.client.add_event_handler(self.on_deleted, events.MessageDeleted())

    async def on_message(self, event) -> None:
        if event.chat_id not in self.chats:
            return
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
            Source.TELEGRAM, str(op.chat_id), op.message.id
        )
        return status
