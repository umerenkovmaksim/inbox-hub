from datetime import UTC, datetime

from sqlalchemy import func, select
from telethon import events
from telethon.tl.types import (
    Channel,
    ChatPhotoEmpty,
    Message,
    MessageActionPinMessage,
    MessageService,
    PeerChannel,
    PeerChat,
)

from inbox_hub.db.models import Item
from inbox_hub.enums import Source
from inbox_hub.services.catalog import CatalogService
from inbox_hub.services.ingest import IngestStatus
from inbox_hub.settings import TelegramSettings
from inbox_hub.sources.telegram.collector import (
    Deleted,
    TelegramCollector,
    Upsert,
)

DT = datetime(2026, 10, 1, 8, tzinfo=UTC)
CHANNEL = -1001
GROUP = -4000123


class StrictClient:
    """Only the methods the collector may touch; anything else raises."""

    def __init__(self):
        self.handlers = []

    def add_event_handler(self, callback, event):
        self.handlers.append((callback, event))


def msg(i, text, peer=None, edit=None):
    return Message(
        id=i,
        peer_id=peer or PeerChannel(1),
        date=DT,
        message=text,
        edit_date=edit,
    )


class Event:
    def __init__(self, message, chat_id=CHANNEL, chat=None):
        self.message = message
        self.chat_id = chat_id
        self.chat = chat

    def __getattr__(self, name):
        raise AssertionError(f"handler touched event.{name}")


class DeletedEvent:
    def __init__(self, ids, chat_id=None):
        self.deleted_ids = ids
        self.chat_id = chat_id


async def collector(ingest, ids=(CHANNEL,)):
    """Collector whose tracked chats come from the catalog, as in production."""
    catalog = CatalogService(ingest.sessions)
    for chat_id in ids:
        await catalog.track_chat(chat_id, title="ИСиТ")
    tg = TelegramSettings(api_id=1, api_hash="h")
    c = TelegramCollector(StrictClient(), ingest, catalog, tg)
    await c.refresh_chats()
    return c


async def drain(c):
    while not c.queue.empty():
        await c.apply(c.queue.get_nowait())
        c.queue.task_done()


async def rows(sessions):
    async with sessions() as s:
        return list((await s.scalars(select(Item).order_by(Item.id))).all())


async def test_handlers_registered_without_static_chat_filter(ingest):
    c = await collector(ingest)
    c.register_handlers()
    kinds = [type(e) for _, e in c.client.handlers]
    assert kinds == [events.NewMessage, events.MessageEdited, events.MessageDeleted]
    assert all(e.chats is None for _, e in c.client.handlers)


async def test_untracked_chat_events_are_dropped(ingest):
    c = await collector(ingest)
    await c.on_message(Event(msg(1, "личное"), chat_id=-1009999))
    assert c.queue.empty()


async def test_chat_tracked_at_runtime_is_picked_up(ingest):
    c = await collector(ingest)
    await c.catalog.track_chat(-1002)  # e.g. `inbox-hub chats add` from another process
    await c.on_message(Event(msg(1, "a"), chat_id=-1002))
    assert c.queue.empty()  # not yet: the set is refreshed periodically
    assert await c.refresh_chats() == {-1002}
    assert c.chats_added.is_set()  # wakes the reconciler for a backfill
    await c.on_message(Event(msg(2, "b"), chat_id=-1002))
    assert c.queue.qsize() == 1


async def test_handlers_only_enqueue(ingest, sessions):
    c = await collector(ingest)
    await c.on_message(Event(msg(1, "пара в 311")))
    assert c.queue.qsize() == 1
    assert await rows(sessions) == []
    await drain(c)
    assert [r.text for r in await rows(sessions)] == ["пара в 311"]


async def test_title_comes_from_update_entity_without_requests(ingest, sessions):
    c = await collector(ingest)
    chat = Channel(id=1, title="ИСиТ", photo=ChatPhotoEmpty(), date=DT)
    await c.on_message(Event(msg(1, "a"), chat=chat))
    await drain(c)
    assert (await rows(sessions))[0].channel.title == "ИСиТ"


async def test_edit_event_updates_item(ingest, sessions):
    c = await collector(ingest)
    await c.on_message(Event(msg(1, "пара в 311")))
    await c.on_message(Event(msg(1, "пара в 317", edit=DT)))
    await drain(c)
    [row] = await rows(sessions)
    assert row.text == "пара в 317" and row.edited


async def test_writer_is_idempotent_for_events_and_reconcile(ingest, sessions):
    """Same messages from an event, the reconciler and a replay -> no duplicates."""
    c = await collector(ingest)
    messages = [msg(i, f"сообщение {i}") for i in (1, 2, 3)]
    first = [await c.apply(Upsert(CHANNEL, m)) for m in messages]
    second = [await c.apply(Upsert(CHANNEL, m, "ИСиТ")) for m in messages]
    third = [await c.apply(Upsert(CHANNEL, m)) for m in messages]
    assert first == [IngestStatus.NEW] * 3
    assert second == third == [IngestStatus.SAME] * 3
    async with sessions() as s:
        assert await s.scalar(select(func.count()).select_from(Item)) == 3
    assert all(r.raw["_"] == "Message" for r in await rows(sessions))
    assert await ingest.channel_cursor(Source.TELEGRAM, str(CHANNEL)) == 3


async def test_cursor_moves_past_skipped_messages(ingest, sessions):
    c = await collector(ingest)
    pin = MessageService(
        id=7, peer_id=PeerChannel(1), date=DT, action=MessageActionPinMessage()
    )
    assert await c.apply(Upsert(CHANNEL, pin, "ИСиТ")) is None
    assert await rows(sessions) == []
    assert await ingest.channel_cursor(Source.TELEGRAM, str(CHANNEL)) == 7


async def test_delete_in_channel_is_soft(ingest, sessions):
    c = await collector(ingest)
    await c.apply(Upsert(CHANNEL, msg(5, "удалят")))
    await c.on_deleted(DeletedEvent([5], chat_id=CHANNEL))
    await drain(c)
    [row] = await rows(sessions)
    assert row.deleted_at is not None and row.text == "удалят"


async def test_delete_without_chat_id_matches_only_non_channel_chats(ingest, sessions):
    c = await collector(ingest, ids=(CHANNEL, GROUP))
    await c.apply(Upsert(CHANNEL, msg(5, "в канале")))
    await c.apply(Upsert(GROUP, msg(5, "в группе", peer=PeerChat(4000123))))
    await c.on_deleted(DeletedEvent([5], chat_id=None))
    assert c.queue.get_nowait() == Deleted(None, (5,))
    await c.apply(Deleted(None, (5,)))
    by_text = {r.text: r.deleted_at for r in await rows(sessions)}
    assert by_text["в группе"] is not None
    assert by_text["в канале"] is None


async def test_delete_in_foreign_channel_is_ignored(ingest):
    c = await collector(ingest)
    await c.on_deleted(DeletedEvent([5], chat_id=-1009999))
    assert c.queue.empty()


async def test_deleted_items_leave_the_feed(ingest, sessions, clock):
    from inbox_hub.services.access import Principal
    from inbox_hub.services.feed import FeedQuery, FeedService

    c = await collector(ingest)
    await c.apply(Upsert(CHANNEL, msg(1, "останется")))
    await c.apply(Upsert(CHANNEL, msg(2, "удалят")))
    await c.apply(Deleted(CHANNEL, (2,)))
    feed = FeedService(sessions, clock)
    items = await feed.list_items(
        FeedQuery(datetime(2026, 1, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
        Principal("admin", None, None),
    )
    assert [e.item.text for e in items] == ["останется"]
    deleted = await feed.get_item(items[0].item.id + 1, Principal("admin", None, None))
    assert deleted.item.deleted_at is not None  # still readable by id


async def test_titles_seeded_from_database(ingest):
    c = await collector(ingest)
    await c.apply(Upsert(CHANNEL, msg(1, "a"), "ИСиТ"))
    fresh = await collector(ingest)
    await fresh.load_titles()
    assert fresh.titles == {CHANNEL: "ИСиТ"}
