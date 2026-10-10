from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from inbox_hub.db.models import File, Item
from inbox_hub.enums import Source
from inbox_hub.services.ingest import utcnow


@dataclass(frozen=True)
class FeedQuery:
    date_from: datetime
    date_to: datetime
    source: Source | None = None
    include_received: bool = False
    limit: int = 200


class FeedService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        self.clock = clock

    async def list_items(self, q: FeedQuery) -> list[Item]:
        in_window = or_(
            and_(Item.published_at >= q.date_from, Item.published_at < q.date_to),
            and_(
                Item.edited.is_(True),
                Item.edited_at >= q.date_from,
                Item.edited_at < q.date_to,
            ),
        )
        stmt = select(Item).where(in_window)
        if q.source is not None:
            stmt = stmt.where(Item.source == q.source)
        if not q.include_received:
            stmt = stmt.where(Item.received_at.is_(None))
        stmt = stmt.order_by(
            func.coalesce(Item.edited_at, Item.published_at), Item.id
        ).limit(q.limit)
        async with self.sessions() as s:
            return list((await s.scalars(stmt)).unique().all())

    async def get_item(self, item_id: int) -> Item | None:
        async with self.sessions() as s:
            return await s.get(Item, item_id)

    async def mark_received(
        self, ids: list[int], versions: dict[int, datetime] | None = None
    ) -> int:
        if not ids:
            return 0
        versions = versions or {}
        now = self.clock()
        updated = 0
        async with self.sessions.begin() as s:
            for item_id in dict.fromkeys(ids):
                stmt = update(Item).where(Item.id == item_id)
                if item_id in versions:
                    stmt = stmt.where(Item.updated_at <= versions[item_id])
                updated += (await s.execute(stmt.values(received_at=now))).rowcount
        return updated

    async def get_file(self, sha256: str) -> File | None:
        async with self.sessions() as s:
            return await s.get(File, sha256)
