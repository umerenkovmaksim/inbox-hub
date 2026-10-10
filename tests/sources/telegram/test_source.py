from datetime import UTC, datetime, timedelta

from telethon.tl.types import Message, PeerChannel

from inbox_hub.enums import Source
from inbox_hub.sources.telegram.source import TelegramSource

DT = datetime(2026, 10, 1, 8, tzinfo=UTC)


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    async def get_entity(self, chat_id):
        return type("Chat", (), {"title": "ИСиТ", "id": chat_id})()

    async def iter_messages(self, entity, **kw):
        self.calls.append(kw)
        for m in self.messages:
            if "min_id" in kw and m.id <= kw["min_id"]:
                continue
            if "offset_date" in kw and m.date < kw["offset_date"]:
                continue
            yield m


def msg(i, text, at=DT, edit=None):
    return Message(id=i, peer_id=PeerChannel(1), date=at, message=text, edit_date=edit)


async def test_backfill_then_cursor(ingest, settings, clock):
    clock.now = DT + timedelta(days=1)
    client = FakeClient([msg(1, "a"), msg(2, "b")])
    src = TelegramSource(client, ingest, settings.telegram, clock=clock)
    report = await src.sync()
    assert report.new == 2
    assert client.calls[0] == {
        "offset_date": settings.telegram.backfill_from,
        "reverse": True,
    }
    assert await ingest.channel_cursor(Source.TELEGRAM, "-1001") == 2
    client.messages.append(msg(3, "c"))
    client.calls.clear()
    report = await src.sync()
    assert client.calls[0] == {"min_id": 2, "reverse": True}
    assert report.new == 1


async def test_recheck_picks_up_edits(ingest, settings, clock):
    clock.now = DT + timedelta(days=1)
    client = FakeClient([msg(1, "пара в 311")])
    src = TelegramSource(client, ingest, settings.telegram, clock=clock)
    await src.sync()
    client.messages[0] = msg(1, "пара в 317", edit=DT + timedelta(hours=2))
    report = await src.sync()
    assert report.changed == 1


class Event:
    def __init__(self, message, chat_id=-1001):
        self.message = message
        self.chat_id = chat_id

    async def get_chat(self):
        return type("Chat", (), {"title": "ИСиТ", "id": self.chat_id})()


async def test_live_event_does_not_move_cursor(ingest, settings, clock):
    src = TelegramSource(FakeClient([]), ingest, settings.telegram, clock=clock)
    await src.handle_event(Event(msg(600, "новое сообщение")))
    assert await ingest.channel_cursor(Source.TELEGRAM, "-1001") == 0


async def test_one_broken_chat_does_not_stop_the_others(ingest, settings, clock):
    from inbox_hub.settings import TelegramSettings

    class Picky(FakeClient):
        async def get_entity(self, chat_id):
            if chat_id == -1001:
                raise ValueError("left the group")
            return await super().get_entity(chat_id)

    clock.now = DT + timedelta(days=1)
    tg_settings = TelegramSettings(api_id=1, api_hash="h", source_ids=[-1001, -1002])
    client = Picky([msg(1, "привет")])
    report = await TelegramSource(client, ingest, tg_settings, clock=clock).sync()
    assert report.new == 1
    assert any("-1001" in w for w in report.warnings)
