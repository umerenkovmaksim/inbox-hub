from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from university_pz_parser.db.models import File, Item
from university_pz_parser.enums import Source
from university_pz_parser.services.ingest import utcnow


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

    async def mark_received(self, ids: list[int]) -> int:
        if not ids:
            return 0
        async with self.sessions.begin() as s:
            result = await s.execute(
                update(Item).where(Item.id.in_(ids)).values(received_at=self.clock())
            )
            return result.rowcount

    async def get_file(self, sha256: str) -> File | None:
        async with self.sessions() as s:
            return await s.get(File, sha256)
