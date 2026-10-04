from datetime import UTC, datetime, timedelta

from university_pz_parser.domain.dto import IncomingItem
from university_pz_parser.enums import Source
from university_pz_parser.services.feed import FeedQuery, FeedService

D = datetime(2026, 10, 1, tzinfo=UTC)


def item(key, at, text="t", source=Source.TELEGRAM):
    return IncomingItem(
        source=source,
        external_key=key,
        channel_external_id="c",
        channel_title="c",
        kind="text",
        text=text,
        published_at=at,
    )


async def test_window_and_received(ingest, sessions, clock):
    feed = FeedService(sessions, clock=clock)
    await ingest.ingest(item("c:1", D - timedelta(days=2)))
    await ingest.ingest(item("c:2", D + timedelta(hours=1)))
    await ingest.ingest(item("c:3", D + timedelta(days=2)))
    got = await feed.list_items(FeedQuery(D, D + timedelta(days=1)))
    assert [i.external_key for i in got] == ["c:2"]
    assert await feed.mark_received([got[0].id, 999]) == 1
    assert await feed.list_items(FeedQuery(D, D + timedelta(days=1))) == []
    again = await feed.list_items(
        FeedQuery(D, D + timedelta(days=1), include_received=True)
    )
    assert again[0].received_at == clock()


async def test_old_item_edited_in_window_reappears(ingest, sessions, clock):
    feed = FeedService(sessions, clock=clock)
    await ingest.ingest(item("c:1", D - timedelta(days=10), text="old"))
    await feed.mark_received([1])
    clock.now = D + timedelta(hours=3)
    await ingest.ingest(item("c:1", D - timedelta(days=10), text="new"))
    got = await feed.list_items(FeedQuery(D, D + timedelta(days=1)))
    assert [(i.external_key, i.previous_text) for i in got] == [("c:1", "old")]


async def test_source_filter_and_limit(ingest, sessions, clock):
    feed = FeedService(sessions, clock=clock)
    for n in range(3):
        await ingest.ingest(item(f"c:{n}", D + timedelta(minutes=n)))
    await ingest.ingest(item("cm:1", D, source=Source.SDO))
    assert (
        len(
            await feed.list_items(
                FeedQuery(D, D + timedelta(days=1), source=Source.SDO)
            )
        )
        == 1
    )
    assert len(await feed.list_items(FeedQuery(D, D + timedelta(days=1), limit=2))) == 2


async def test_mark_received_skips_item_edited_after_fetch(ingest, sessions, clock):
    feed = FeedService(sessions, clock=clock)
    await ingest.ingest(item("c:1", D, text="v1"))
    fetched = (await feed.list_items(FeedQuery(D, D + timedelta(days=1))))[0]
    clock.tick(10)
    await ingest.ingest(item("c:1", D, text="v2"))
    assert await feed.mark_received([fetched.id], {fetched.id: fetched.updated_at}) == 0
    again = await feed.list_items(FeedQuery(D, D + timedelta(days=1)))
    assert [i.text for i in again] == ["v2"]
