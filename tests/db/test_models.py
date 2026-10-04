from datetime import UTC, datetime

from sqlalchemy import select

from university_pz_parser.db.models import Channel, File, Item, ItemFile
from university_pz_parser.enums import Source


async def test_roundtrip_keeps_utc(sessions):
    now = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    async with sessions.begin() as s:
        ch = Channel(source=Source.TELEGRAM, external_id="-1001", title="ИСиТ")
        s.add(ch)
        await s.flush()
        s.add(File(sha256="a" * 64, size=1, local_path="x", text_status="ok"))
        item = Item(
            source=Source.TELEGRAM,
            external_key="-1001:1",
            channel_id=ch.id,
            kind="text",
            text="hi",
            published_at=now,
            first_seen_at=now,
            updated_at=now,
            content_hash="h",
        )
        item.links.append(ItemFile(file_sha256="a" * 64, remote_id="r", attached_at=now))
        s.add(item)
    async with sessions() as s:
        got = (await s.execute(select(Item))).scalar_one()
        assert got.published_at == now
        assert got.published_at.tzinfo is not None
        assert got.links[0].file.sha256 == "a" * 64
        assert got.channel.title == "ИСиТ"
