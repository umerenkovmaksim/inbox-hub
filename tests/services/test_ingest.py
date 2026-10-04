from datetime import UTC, datetime

from sqlalchemy import func, select

from university_pz_parser.db.models import File, Item
from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.enums import Source
from university_pz_parser.extractors.registry import ExtractorRegistry
from university_pz_parser.services.ingest import IngestService, IngestStatus


def fetcher(content: bytes, name: str, calls: list | None = None):
    async def fetch(tmp):
        if calls is not None:
            calls.append(name)
        p = tmp / name
        p.write_bytes(content)
        return p

    return fetch


def failing_fetch():
    async def fetch(tmp):
        raise ConnectionError("boom")

    return fetch


def tg(msg_id=1, text="Сдать ПЗ1 до пятницы", files=(), chat="-1001", **kw):
    return IncomingItem(
        source=Source.TELEGRAM,
        external_key=f"{chat}:{msg_id}",
        channel_external_id=chat,
        channel_title="ИСиТ",
        kind="document" if files else "text",
        text=text,
        published_at=datetime(2026, 10, 1, 8, msg_id % 60, tzinfo=UTC),
        files=tuple(files),
        **kw,
    )


async def one(sessions, key):
    async with sessions() as s:
        return (
            await s.execute(select(Item).where(Item.external_key == key))
        ).scalar_one()


async def count(sessions, model):
    async with sessions() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_new_then_same_does_not_refetch(ingest, sessions):
    calls = []
    f = IncomingFile("tg:doc:1", "pz1.txt", fetch=fetcher(b"task", "pz1.txt", calls))
    assert await ingest.ingest(tg(files=[f])) is IngestStatus.NEW
    assert await ingest.ingest(tg(files=[f])) is IngestStatus.SAME
    assert calls == ["pz1.txt"]
    item = await one(sessions, "-1001:1")
    assert item.links[0].file.text == "task"
    assert item.links[0].file.text_status == "ok"


async def test_same_file_in_two_sources_is_one_file(ingest, sessions):
    a = IncomingFile("tg:doc:1", "a.txt", fetch=fetcher(b"same", "a.txt"))
    b = IncomingFile("etag:x", "b.txt", fetch=fetcher(b"same", "b.txt"))
    await ingest.ingest(tg(files=[a]))
    sdo = IncomingItem(
        source=Source.SDO,
        external_key="cm:5",
        channel_external_id="3430",
        channel_title="МИС",
        kind="assign",
        text="ПЗ1",
        published_at=None,
        files=(b,),
    )
    await ingest.ingest(sdo)
    assert await count(sessions, File) == 1
    assert (await one(sessions, "cm:5")).links[0].file_sha256 == (
        await one(sessions, "-1001:1")
    ).links[0].file_sha256


async def test_edit_after_receive_resets_received(ingest, sessions, clock):
    await ingest.ingest(tg(text="Пара в 311 аудитории"))
    async with sessions.begin() as s:
        (await s.execute(select(Item))).scalar_one().received_at = clock()
    clock.tick(30)
    assert await ingest.ingest(tg(text="Пара в 317 аудитории")) is IngestStatus.CHANGED
    item = await one(sessions, "-1001:1")
    assert item.edited and item.received_at is None
    assert item.previous_text == "Пара в 311 аудитории"
    assert item.edited_at == clock()
    assert item.published_at == datetime(2026, 10, 1, 8, 1, tzinfo=UTC)


async def test_replaced_file_detaches_old(ingest, sessions):
    v1 = IncomingFile("etag:1", "pz.txt", fetch=fetcher(b"v1", "pz.txt"))
    v2 = IncomingFile("etag:2", "pz.txt", fetch=fetcher(b"v2", "pz.txt"))
    await ingest.ingest(tg(files=[v1]))
    assert await ingest.ingest(tg(files=[v2])) is IngestStatus.CHANGED
    links = {k.file.text: k for k in (await one(sessions, "-1001:1")).links}
    assert links["v1"].detached_at is not None
    assert links["v2"].detached_at is None
    assert await count(sessions, File) == 2


async def test_failed_fetch_is_retried_and_keeps_old_link(ingest, sessions):
    ok = IncomingFile("etag:1", "pz.txt", fetch=fetcher(b"v1", "pz.txt"))
    await ingest.ingest(tg(files=[ok]))
    broken = IncomingFile("etag:1", "pz.txt", fetch=failing_fetch())
    extra = IncomingFile("etag:9", "new.txt", fetch=failing_fetch())
    assert await ingest.ingest(tg(files=[broken, extra])) is IngestStatus.CHANGED
    item = await one(sessions, "-1001:1")
    assert [k.detached_at for k in item.links] == [None]
    assert item.extra["skipped_files"][0]["reason"] == "fetch_failed"
    fixed = IncomingFile("etag:9", "new.txt", fetch=fetcher(b"n", "new.txt"))
    assert await ingest.ingest(tg(files=[ok, fixed])) is IngestStatus.CHANGED
    assert len((await one(sessions, "-1001:1")).links) == 2


async def test_oversized_file_is_metadata_only(ingest, sessions, settings):
    big = IncomingFile(
        "tg:doc:2",
        "video.mp4",
        size=settings.max_file_bytes + 1,
        fetch=fetcher(b"x", "v"),
    )
    await ingest.ingest(tg(files=[big]))
    item = await one(sessions, "-1001:1")
    assert item.links == []
    assert item.extra["skipped_files"] == [
        {
            "name": "video.mp4",
            "size": settings.max_file_bytes + 1,
            "url": None,
            "reason": "too_large",
        }
    ]
    assert await ingest.ingest(tg(files=[big])) is IngestStatus.SAME


async def test_text_duplicate_points_to_earliest(ingest, sessions):
    text = "Внимание! Завтра пары по моделированию не будет, переносим на четверг"
    await ingest.ingest(tg(msg_id=2, text=text, chat="-1001"))
    await ingest.ingest(tg(msg_id=5, text=text.upper(), chat="-1002"))
    first = await one(sessions, "-1001:2")
    second = await one(sessions, "-1002:5")
    assert first.duplicate_of_id is None
    assert second.duplicate_of_id == first.id


async def test_unhashed_extra_updates_silently(ingest, sessions):
    await ingest.ingest(tg(extra={"submission": "нет ответа"}))
    assert (
        await ingest.ingest(tg(extra={"submission": "отправлено"})) is IngestStatus.SAME
    )
    item = await one(sessions, "-1001:1")
    assert item.extra["submission"] == "отправлено" and not item.edited


async def test_cursor_only_moves_forward(ingest):
    await ingest.ingest(tg())
    assert await ingest.channel_cursor(Source.TELEGRAM, "-1001") == 0
    await ingest.advance_cursor(Source.TELEGRAM, "-1001", 10)
    await ingest.advance_cursor(Source.TELEGRAM, "-1001", 3)
    assert await ingest.channel_cursor(Source.TELEGRAM, "-1001") == 10


async def test_resolver_files_are_attached(sessions, store, clock, settings):
    class Fake:
        async def resolve(self, text):
            return (
                [IncomingFile("yadisk:abc", "r.txt", fetch=fetcher(b"disk", "r.txt"))]
                if "disk.yandex" in text
                else []
            )

    svc = IngestService(
        sessions, store, ExtractorRegistry(), [Fake()], settings.max_file_bytes, clock
    )
    await svc.ingest(tg(text="Методичка https://disk.yandex.ru/d/abc"))
    assert (await one(sessions, "-1001:1")).links[0].file.text == "disk"
