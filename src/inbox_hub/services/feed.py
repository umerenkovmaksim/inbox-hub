from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, and_, func, literal, or_, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from inbox_hub.db.models import (
    Category,
    File,
    Item,
    ItemFile,
    Receipt,
    channel_categories,
)
from inbox_hub.enums import Source
from inbox_hub.services.access import Principal
from inbox_hub.services.ingest import utcnow


class ScopeError(Exception):
    """A requested category is not in the caller's scope (or does not exist)."""


class NoReceiptsError(Exception):
    """The caller has no receipts of its own (the admin token)."""


@dataclass(frozen=True)
class FeedQuery:
    date_from: datetime
    date_to: datetime
    source: Source | None = None
    include_received: bool = False
    limit: int = 200
    categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeedEntry:
    item: Item
    received_at: datetime | None
    duplicate_of_id: int | None
    categories: list[str]


def _scoped(stmt: Select, category_ids: frozenset[int] | None) -> Select:
    """The only place that restricts Items to a scope; every read goes through it."""
    if category_ids is None:
        return stmt
    visible_channels = select(channel_categories.c.channel_id).where(
        channel_categories.c.category_id.in_(category_ids)
    )
    return stmt.where(Item.channel_id.in_(visible_channels))


class FeedService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        self.clock = clock

    async def list_items(self, q: FeedQuery, p: Principal) -> list[FeedEntry]:
        in_window = or_(
            and_(Item.published_at >= q.date_from, Item.published_at < q.date_to),
            and_(
                Item.edited.is_(True),
                Item.edited_at >= q.date_from,
                Item.edited_at < q.date_to,
            ),
        )
        async with self.sessions() as s:
            scope = await self._narrow(s, p, q.categories)
            stmt = _scoped(self._with_receipt(p).where(in_window), scope)
            if q.source is not None:
                stmt = stmt.where(Item.source == q.source)
            if not q.include_received:
                stmt = stmt.where(Receipt.received_at.is_(None))
            stmt = stmt.order_by(
                func.coalesce(Item.edited_at, Item.published_at), Item.id
            ).limit(q.limit)
            rows = (await s.execute(stmt)).unique().all()
            return await self._entries(s, p, rows)

    async def get_item(self, item_id: int, p: Principal) -> FeedEntry | None:
        async with self.sessions() as s:
            stmt = _scoped(
                self._with_receipt(p).where(Item.id == item_id), p.category_ids
            )
            rows = (await s.execute(stmt)).unique().all()
            entries = await self._entries(s, p, rows)
            return entries[0] if entries else None

    async def mark_received(
        self,
        ids: list[int],
        p: Principal,
        versions: dict[int, datetime] | None = None,
    ) -> int:
        if p.key_id is None:
            raise NoReceiptsError
        if not ids:
            return 0
        versions = versions or {}
        now = self.clock()
        updated = 0
        async with self.sessions.begin() as s:
            for item_id in dict.fromkeys(ids):
                src = select(
                    literal(p.key_id, Receipt.key_id.type),
                    Item.id,
                    literal(now, Receipt.received_at.type),
                ).where(Item.id == item_id)
                if item_id in versions:
                    src = src.where(Item.updated_at <= versions[item_id])
                src = _scoped(src, p.category_ids)
                stmt = (
                    insert(Receipt)
                    .from_select(["key_id", "item_id", "received_at"], src)
                    .on_conflict_do_update(
                        index_elements=["key_id", "item_id"],
                        set_={"received_at": now},
                    )
                )
                updated += (await s.execute(stmt)).rowcount
        return updated

    async def get_file(self, sha256: str, p: Principal) -> File | None:
        async with self.sessions() as s:
            if p.is_admin:
                return await s.get(File, sha256)
            stmt = (
                select(File)
                .join(ItemFile, ItemFile.file_sha256 == File.sha256)
                .join(Item, Item.id == ItemFile.item_id)
                .where(File.sha256 == sha256)
                .limit(1)
            )
            return await s.scalar(_scoped(stmt, p.category_ids))

    @staticmethod
    def _with_receipt(p: Principal) -> Select:
        return select(Item, Receipt.received_at).outerjoin(
            Receipt, and_(Receipt.item_id == Item.id, Receipt.key_id == p.key_id)
        )

    @staticmethod
    async def _narrow(
        s: AsyncSession, p: Principal, slugs: Sequence[str]
    ) -> frozenset[int] | None:
        if not slugs:
            return p.category_ids
        wanted = set(slugs)
        found = dict(
            (
                await s.execute(
                    select(Category.slug, Category.id).where(Category.slug.in_(wanted))
                )
            ).all()
        )
        allowed = {
            slug: cid
            for slug, cid in found.items()
            if p.category_ids is None or cid in p.category_ids
        }
        if missing := wanted - allowed.keys():
            raise ScopeError(", ".join(sorted(missing)))
        return frozenset(allowed.values())

    @staticmethod
    async def _entries(
        s: AsyncSession, p: Principal, rows: Sequence
    ) -> list[FeedEntry]:
        dup_ids = {item.duplicate_of_id for item, _ in rows if item.duplicate_of_id}
        visible_dups: set[int] = set()
        if dup_ids:
            visible_dups = set(
                (
                    await s.scalars(
                        _scoped(
                            select(Item.id).where(Item.id.in_(dup_ids)), p.category_ids
                        )
                    )
                ).all()
            )
        return [
            FeedEntry(
                item=item,
                received_at=received_at,
                duplicate_of_id=(
                    item.duplicate_of_id
                    if item.duplicate_of_id in visible_dups
                    else None
                ),
                categories=[
                    c.slug
                    for c in item.channel.categories
                    if p.category_ids is None or c.id in p.category_ids
                ],
            )
            for item, received_at in rows
        ]
