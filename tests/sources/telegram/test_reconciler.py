import asyncio
from datetime import UTC, datetime

from telethon.errors import FloodWaitError
from telethon.tl.types import Message, PeerChannel

from inbox_hub.enums import Source
from inbox_hub.services.catalog import CatalogService
from inbox_hub.services.health import HealthService
from inbox_hub.settings import TelegramSettings
from inbox_hub.sources.telegram.collector import TelegramCollector, Upsert
from inbox_hub.sources.telegram.reconciler import Reconciler

DT = datetime(2026, 10, 1, 8, tzinfo=UTC)
A, B, FOREIGN = -1001, -1002, -1003


def msg(i, text="m"):
    return Message(id=i, peer_id=PeerChannel(1), date=DT, message=text)


class Dialog:
    def __init__(self, chat_id, top, name=None, group=False):
        self.id = chat_id
        self.name = name or f"chat {chat_id}"
        self.entity = f"entity {chat_id}"
        self.message = msg(top) if top else None
        self.is_group = group
        self.is_channel = chat_id < 0 and not group


class FakeClient:
    """iter_dialogs/iter_messages only: get_entity & co. do not exist here."""

    def __init__(self, dialogs, history):
        self.dialogs = dialogs
        self.history = history
        self.calls = []
        self.flood_once: set[str] = set()

    def add_event_handler(self, *a):
        pass

    async def iter_dialogs(self):
        for d in self.dialogs:
            yield d

    async def iter_messages(self, entity, **kw):
        self.calls.append((entity, kw))
        if entity in self.flood_once:
            self.flood_once.discard(entity)
            raise FloodWaitError(request=None, capture=900)
        for m in self.history[entity]:
            if "min_id" in kw and m.id <= kw["min_id"]:
                continue
            yield m


async def setup(ingest, sessions, client, ids=(A, B)):
    catalog = CatalogService(sessions)
    for chat_id in ids:
        await catalog.track_chat(chat_id)
    tg = TelegramSettings(api_id=1, api_hash="h")
    c = TelegramCollector(client, ingest, catalog, tg)
    slept = []

    async def sleep(seconds):
        slept.append(seconds)

    return c, Reconciler(client, c, HealthService(sessions), sleep=sleep), slept


async def run(c, r):
    consumer = asyncio.create_task(c.consume())
    try:
        return await r.run_once()
    finally:
        consumer.cancel()


async def test_backfill_then_only_chats_behind_are_read(ingest, sessions):
    client = FakeClient(
        [Dialog(A, 2), Dialog(B, 0), Dialog(FOREIGN, 9)],
        {"entity -1001": [msg(1), msg(2)]},
    )
    c, r, _ = await setup(ingest, sessions, client)
    report = await run(c, r)
    assert report.new == 2
    assert client.calls == [
        (
            "entity -1001",
            {"reverse": True, "wait_time": 1, "offset_date": c.settings.backfill_from},
        )
    ]
    assert await ingest.channel_cursor(Source.TELEGRAM, str(A)) == 2

    client.calls.clear()
    await run(c, r)
    assert client.calls == []  # nothing new: no history requests at all

    client.dialogs[0] = Dialog(A, 3)
    client.history["entity -1001"].append(msg(3))
    report = await run(c, r)
    assert client.calls == [
        ("entity -1001", {"reverse": True, "wait_time": 1, "min_id": 2})
    ]
    assert report.new == 1


async def test_cursor_jumps_to_top_even_if_top_is_skipped(ingest, sessions):
    client = FakeClient([Dialog(A, 10)], {"entity -1001": [msg(4)]})
    c, r, _ = await setup(ingest, sessions, client, ids=(A,))
    await run(c, r)
    assert await ingest.channel_cursor(Source.TELEGRAM, str(A)) == 10
    client.calls.clear()
    await run(c, r)
    assert client.calls == []


async def test_events_already_stored_mean_no_history_request(ingest, sessions):
    client = FakeClient([Dialog(A, 5)], {"entity -1001": []})
    c, r, _ = await setup(ingest, sessions, client, ids=(A,))
    await c.apply(Upsert(A, msg(5)))
    await run(c, r)
    assert client.calls == []


async def test_flood_wait_is_waited_out_then_retried_once(ingest, sessions):
    client = FakeClient([Dialog(A, 1)], {"entity -1001": [msg(1)]})
    client.flood_once.add("entity -1001")
    c, r, slept = await setup(ingest, sessions, client, ids=(A,))
    report = await run(c, r)
    assert slept == [900]
    assert len(client.calls) == 2
    assert report.new == 1
    assert any("FloodWait 900s" in w for w in report.warnings)


async def test_missing_whitelisted_chat_is_reported(ingest, sessions):
    client = FakeClient([Dialog(A, 0)], {})
    c, r, _ = await setup(ingest, sessions, client)
    report = await run(c, r)
    assert report.warnings == [f"chat {B} not found among dialogs"]
    health = {h.source: h for h in await HealthService(sessions).snapshot()}
    assert health["telegram"].last_error is None


async def test_dialog_titles_feed_the_collector(ingest, sessions):
    client = FakeClient([Dialog(A, 1, name="ИСиТ")], {"entity -1001": [msg(1)]})
    c, r, _ = await setup(ingest, sessions, client, ids=(A,))
    await run(c, r)
    assert c.titles[A] == "ИСиТ"
    assert (await ingest.channel_titles(Source.TELEGRAM)) == {str(A): "ИСиТ"}


async def test_dialog_snapshot_is_stored_for_list_chats(ingest, sessions):
    client = FakeClient(
        [Dialog(A, 0, name="ИСиТ"), Dialog(FOREIGN, 0, name="Флуд", group=True)], {}
    )
    c, r, _ = await setup(ingest, sessions, client, ids=(A,))
    await run(c, r)
    dialogs = await c.catalog.telegram_dialogs()
    assert [(d.chat_id, d.title, d.kind) for d in dialogs] == [
        (A, "ИСиТ", "channel"),
        (FOREIGN, "Флуд", "group"),
    ]


async def test_newly_tracked_chat_wakes_the_reconciler(ingest, sessions):
    client = FakeClient([Dialog(A, 0)], {})
    c, r, _ = await setup(ingest, sessions, client, ids=(A,))
    never = asyncio.Event()

    async def long_sleep(seconds):
        await never.wait()

    r.sleep = long_sleep
    pause = asyncio.create_task(r._pause(4 * 3600))
    await asyncio.sleep(0)
    assert not pause.done()
    await c.catalog.track_chat(B)
    await c.refresh_chats()
    await asyncio.wait_for(pause, 1)
