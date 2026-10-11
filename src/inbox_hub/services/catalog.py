import re
from collections.abc import Callable, Iterable
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from inbox_hub.db.models import (
    ApiKey,
    Category,
    Channel,
    TelegramDialog,
    api_key_categories,
    channel_categories,
)
from inbox_hub.enums import Source
from inbox_hub.services.access import Principal, hash_token, new_token
from inbox_hub.services.ingest import utcnow

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class CatalogError(ValueError):
    pass


class NotFoundError(CatalogError):
    pass


class CatalogService:
    """Categories, which channels are in them, and API keys scoped to them."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        self.clock = clock

    # categories

    async def list_categories(self) -> list[Category]:
        async with self.sessions() as s:
            return list(
                (await s.scalars(select(Category).order_by(Category.slug))).all()
            )

    async def visible_categories(self, p: Principal) -> list[Category]:
        return [
            c
            for c in await self.list_categories()
            if p.category_ids is None or c.id in p.category_ids
        ]

    async def create_category(
        self,
        slug: str,
        title: str,
        description: str | None = None,
        auto_sources: Iterable[str] = (),
    ) -> Category:
        if not SLUG.match(slug):
            raise CatalogError(f"invalid slug {slug!r}: use a-z, 0-9, '-', '_'")
        sources = [Source(x).value for x in auto_sources]
        async with self.sessions.begin() as s:
            if await self._category(s, slug, required=False) is not None:
                raise CatalogError(f"category {slug!r} already exists")
            category = Category(
                slug=slug, title=title, description=description, auto_sources=sources
            )
            s.add(category)
        return category

    async def delete_category(self, slug: str) -> None:
        async with self.sessions.begin() as s:
            category = await self._category(s, slug)
            await s.execute(
                delete(channel_categories).where(
                    channel_categories.c.category_id == category.id
                )
            )
            await s.execute(
                delete(api_key_categories).where(
                    api_key_categories.c.category_id == category.id
                )
            )
            await s.delete(category)

    # channels

    async def list_channels(self) -> list[Channel]:
        async with self.sessions() as s:
            return list(
                (
                    await s.scalars(
                        select(Channel).order_by(Channel.source, Channel.title)
                    )
                ).all()
            )

    async def add_channel(self, slug: str, ref: str) -> Channel:
        async with self.sessions.begin() as s:
            category = await self._category(s, slug)
            channel = await self._channel(s, ref)
            if category not in channel.categories:
                channel.categories.append(category)
        return channel

    async def remove_channel(self, slug: str, ref: str) -> Channel:
        async with self.sessions.begin() as s:
            category = await self._category(s, slug)
            channel = await self._channel(s, ref)
            if category in channel.categories:
                channel.categories.remove(category)
        return channel

    async def track_chat(
        self, chat_id: int, categories: Iterable[str] = (), title: str | None = None
    ) -> Channel:
        async with self.sessions.begin() as s:
            wanted = [await self._category(s, slug) for slug in categories]
            channel = await s.scalar(
                select(Channel).where(
                    Channel.source == Source.TELEGRAM,
                    Channel.external_id == str(chat_id),
                )
            )
            if channel is None:
                channel = Channel(
                    source=Source.TELEGRAM,
                    external_id=str(chat_id),
                    title=title or str(chat_id),
                    cursor=0,
                    categories=[],
                )
                s.add(channel)
            channel.tracked = True
            for category in wanted:
                if category not in channel.categories:
                    channel.categories.append(category)
        return channel

    async def untrack_chat(self, chat_id: int) -> Channel:
        async with self.sessions.begin() as s:
            channel = await self._channel(s, f"{Source.TELEGRAM}:{chat_id}")
            channel.tracked = False
        return channel

    async def tracked_chat_ids(self) -> list[int]:
        async with self.sessions() as s:
            rows = await s.scalars(
                select(Channel.external_id)
                .where(Channel.source == Source.TELEGRAM, Channel.tracked.is_(True))
                .order_by(Channel.id)
            )
            return [int(x) for x in rows.all()]

    async def telegram_dialogs(self) -> list[TelegramDialog]:
        """Dialog snapshot written by the collector's reconciler."""
        async with self.sessions() as s:
            return list(
                (
                    await s.scalars(
                        select(TelegramDialog).order_by(TelegramDialog.title)
                    )
                ).all()
            )

    async def seed_tracked_chats(self, chat_ids: Iterable[int]) -> None:
        """Track chats from the legacy TELEGRAM__SOURCE_IDS that the DB does not know yet."""
        async with self.sessions.begin() as s:
            known = set(
                (
                    await s.scalars(
                        select(Channel.external_id).where(
                            Channel.source == Source.TELEGRAM
                        )
                    )
                ).all()
            )
            for chat_id in dict.fromkeys(chat_ids):
                if str(chat_id) not in known:
                    s.add(
                        Channel(
                            source=Source.TELEGRAM,
                            external_id=str(chat_id),
                            title=str(chat_id),
                            cursor=0,
                            tracked=True,
                        )
                    )

    # keys

    async def list_keys(self) -> list[ApiKey]:
        async with self.sessions() as s:
            return list((await s.scalars(select(ApiKey).order_by(ApiKey.name))).all())

    async def create_key(
        self, name: str, categories: Iterable[str]
    ) -> tuple[ApiKey, str]:
        if not SLUG.match(name):
            raise CatalogError(f"invalid key name {name!r}: use a-z, 0-9, '-', '_'")
        token = new_token()
        async with self.sessions.begin() as s:
            if await self._key(s, name, required=False) is not None:
                raise CatalogError(f"key {name!r} already exists")
            key = ApiKey(
                name=name,
                token_hash=hash_token(token),
                created_at=self.clock(),
                categories=[await self._category(s, slug) for slug in categories],
            )
            s.add(key)
        return key, token

    async def rotate_key(self, name: str) -> str:
        token = new_token()
        async with self.sessions.begin() as s:
            key = await self._key(s, name)
            key.token_hash = hash_token(token)
            key.revoked_at = None
        return token

    async def revoke_key(self, name: str) -> None:
        async with self.sessions.begin() as s:
            key = await self._key(s, name)
            key.token_hash = None
            key.revoked_at = self.clock()

    async def grant(self, name: str, slug: str) -> ApiKey:
        async with self.sessions.begin() as s:
            key = await self._key(s, name)
            category = await self._category(s, slug)
            if category not in key.categories:
                key.categories.append(category)
        return key

    async def ungrant(self, name: str, slug: str) -> ApiKey:
        async with self.sessions.begin() as s:
            key = await self._key(s, name)
            category = await self._category(s, slug)
            if category in key.categories:
                key.categories.remove(category)
        return key

    # lookups

    @staticmethod
    async def _category(
        s: AsyncSession, slug: str, required: bool = True
    ) -> Category | None:
        category = await s.scalar(select(Category).where(Category.slug == slug))
        if category is None and required:
            raise NotFoundError(f"no category {slug!r}")
        return category

    @staticmethod
    async def _key(s: AsyncSession, name: str, required: bool = True) -> ApiKey | None:
        key = await s.scalar(select(ApiKey).where(ApiKey.name == name))
        if key is None and required:
            raise NotFoundError(f"no key {name!r}")
        return key

    @staticmethod
    async def _channel(s: AsyncSession, ref: str) -> Channel:
        """`ref` is an internal channel id or `source:external_id`."""
        source, sep, external_id = ref.partition(":")
        if sep:
            try:
                source = Source(source)
            except ValueError:
                raise NotFoundError(f"unknown source in {ref!r}") from None
            stmt = select(Channel).where(
                Channel.source == source, Channel.external_id == external_id
            )
        elif ref.lstrip("-").isdigit():
            stmt = select(Channel).where(Channel.id == int(ref))
        else:
            raise NotFoundError(f"bad channel reference {ref!r}")
        channel = await s.scalar(stmt)
        if channel is None:
            raise NotFoundError(f"no channel {ref!r}")
        return channel
