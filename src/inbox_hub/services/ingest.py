import asyncio
import logging
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from inbox_hub.db.models import Channel, File, Item, ItemFile
from inbox_hub.domain.dto import IncomingFile, IncomingItem
from inbox_hub.domain.hashing import content_hash, text_norm_hash
from inbox_hub.enums import Source
from inbox_hub.extractors.registry import Extraction, ExtractorRegistry
from inbox_hub.resolvers.base import LinkResolver
from inbox_hub.storage.files import FileStore, StoredFile

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


class IngestStatus(StrEnum):
    NEW = "new"
    CHANGED = "changed"
    SAME = "same"


@dataclass(frozen=True)
class Fetched:
    incoming: IncomingFile
    stored: StoredFile


@dataclass
class FetchResult:
    fetched: list[Fetched]
    skipped: list[dict]
    failed_remote_ids: set[str]


class IngestService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        store: FileStore,
        extractors: ExtractorRegistry,
        resolvers: Sequence[LinkResolver],
        max_file_bytes: int,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        self.store = store
        self.extractors = extractors
        self.resolvers = list(resolvers)
        self.max_file_bytes = max_file_bytes
        self.clock = clock
        self._lock = asyncio.Lock()

    async def ingest(self, item: IncomingItem) -> IngestStatus:
        async with self._lock:
            files = await self._collect_files(item)
            digest = content_hash(item, [f.remote_id for f in files])
            attached: set[str] = set()
            async with self.sessions.begin() as s:
                row = await self._find(s, item)
                if row is not None and row.content_hash == digest:
                    row.extra = {**row.extra, **item.extra, **item.hashed_extra}
                    return IngestStatus.SAME
                if row is not None:
                    attached = {k.remote_id for k in row.links if k.detached_at is None}
            result = await self._fetch(files)
            extractions = await self._extract_new(result.fetched)
            final_digest = content_hash(
                item,
                [
                    f.remote_id
                    for f in files
                    if f.remote_id not in result.failed_remote_ids
                    or f.remote_id in attached
                ],
            )
            return await self._write(item, final_digest, result, extractions)

    async def channel_cursor(self, source: Source, external_id: str) -> int:
        async with self.sessions() as s:
            cursor = await s.scalar(
                select(Channel.cursor).where(
                    Channel.source == source, Channel.external_id == external_id
                )
            )
            return cursor or 0

    async def advance_cursor(
        self, source: Source, external_id: str, value: int
    ) -> None:
        async with self.sessions.begin() as s:
            channel = await s.scalar(
                select(Channel).where(
                    Channel.source == source, Channel.external_id == external_id
                )
            )
            if channel is not None and value > channel.cursor:
                channel.cursor = value

    async def _collect_files(self, item: IncomingItem) -> list[IncomingFile]:
        files = list(item.files)
        haystack = "\n".join(filter(None, [item.text, item.url]))
        for resolver in self.resolvers:
            files.extend(await resolver.resolve(haystack))
        unique: dict[str, IncomingFile] = {}
        for f in files:
            unique.setdefault(f.remote_id, f)
        return list(unique.values())

    async def _fetch(self, files: list[IncomingFile]) -> FetchResult:
        result = FetchResult([], [], set())
        tmp = None
        for f in files:
            if f.fetch is None:
                result.skipped.append(self._skip(f, f.size, "not_downloadable"))
                continue
            if f.size is not None and f.size > self.max_file_bytes:
                result.skipped.append(self._skip(f, f.size, "too_large"))
                continue
            tmp = tmp or self.store.new_tmp_dir()
            try:
                path = await f.fetch(tmp)
            except Exception:
                log.warning("fetch failed for %s", f.remote_id, exc_info=True)
                result.skipped.append(self._skip(f, f.size, "fetch_failed"))
                result.failed_remote_ids.add(f.remote_id)
                continue
            size = path.stat().st_size
            if size > self.max_file_bytes:
                path.unlink()
                result.skipped.append(self._skip(f, size, "too_large"))
                continue
            stored = await asyncio.to_thread(self.store.put, path, f.name)
            result.fetched.append(Fetched(f, stored))
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        return result

    @staticmethod
    def _skip(f: IncomingFile, size: int | None, reason: str) -> dict:
        return {"name": f.name, "size": size, "url": f.url, "reason": reason}

    async def _extract_new(self, fetched: list[Fetched]) -> dict[str, Extraction]:
        if not fetched:
            return {}
        shas = {f.stored.sha256 for f in fetched}
        async with self.sessions() as s:
            known = set(
                (
                    await s.scalars(select(File.sha256).where(File.sha256.in_(shas)))
                ).all()
            )
        extractions: dict[str, Extraction] = {}
        for f in fetched:
            sha = f.stored.sha256
            if sha in known or sha in extractions:
                continue
            extractions[sha] = await asyncio.to_thread(
                self.extractors.extract, f.stored.path, f.incoming.name, f.incoming.mime
            )
        return extractions

    async def _write(
        self,
        item: IncomingItem,
        digest: str,
        result: FetchResult,
        extractions: dict[str, Extraction],
    ) -> IngestStatus:
        now = self.clock()
        async with self.sessions.begin() as s:
            existing = await self._find(s, item)
            if existing is not None and existing.content_hash == digest:
                existing.extra = {
                    **existing.extra,
                    **item.extra,
                    **item.hashed_extra,
                    "skipped_files": result.skipped,
                }
                return IngestStatus.SAME
            channel = await self._channel(s, item)
            for sha, extraction in extractions.items():
                f = next(x for x in result.fetched if x.stored.sha256 == sha)
                s.add(
                    File(
                        sha256=sha,
                        size=f.stored.size,
                        mime=f.incoming.mime,
                        original_name=f.incoming.name,
                        local_path=str(f.stored.path),
                        text=extraction.text,
                        text_status=extraction.status,
                    )
                )
            row = await self._find(s, item)
            extra = {**item.extra, **item.hashed_extra, "skipped_files": result.skipped}
            if row is None:
                row = Item(
                    source=item.source,
                    external_key=item.external_key,
                    channel_id=channel.id,
                    published_at=item.published_at or now,
                    first_seen_at=now,
                    edited=False,
                    links=[],
                )
                s.add(row)
                status = IngestStatus.NEW
            else:
                if row.text != item.text:
                    row.previous_text = row.text
                row.edited = True
                row.edited_at = now
                row.received_at = None
                status = IngestStatus.CHANGED
            row.channel_id = channel.id
            row.kind = item.kind
            row.title = item.title
            row.text = item.text
            row.author = item.author
            row.url = item.url
            row.due_at = item.due_at
            row.available_from = item.available_from
            row.updated_at = now
            row.content_hash = digest
            row.text_norm_hash = text_norm_hash(item.text)
            row.extra = extra
            self._sync_links(row, result, now)
            await s.flush()
            row.duplicate_of_id = await self._find_duplicate(s, row)
            if row.duplicate_of_id is None and row.text_norm_hash is not None:
                await s.execute(
                    update(Item)
                    .where(
                        Item.text_norm_hash == row.text_norm_hash,
                        Item.id != row.id,
                        Item.published_at > row.published_at,
                    )
                    .values(duplicate_of_id=row.id)
                )
        return status

    def _sync_links(self, row: Item, result: FetchResult, now: datetime) -> None:
        current = {link.file_sha256: link for link in row.links}
        wanted: dict[str, Fetched] = {}
        for f in result.fetched:
            wanted.setdefault(f.stored.sha256, f)
        for sha, f in wanted.items():
            link = current.get(sha)
            if link is None:
                row.links.append(
                    ItemFile(
                        file_sha256=sha,
                        remote_id=f.incoming.remote_id,
                        name=f.incoming.name,
                        url=f.incoming.url,
                        attached_at=now,
                    )
                )
            elif link.detached_at is not None:
                link.detached_at = None
                link.attached_at = now
        for sha, link in current.items():
            if sha in wanted or link.detached_at is not None:
                continue
            if link.remote_id in result.failed_remote_ids:
                continue
            link.detached_at = now

    async def _find(self, s: AsyncSession, item: IncomingItem) -> Item | None:
        return await s.scalar(
            select(Item).where(
                Item.source == item.source, Item.external_key == item.external_key
            )
        )

    async def _channel(self, s: AsyncSession, item: IncomingItem) -> Channel:
        channel = await s.scalar(
            select(Channel).where(
                Channel.source == item.source,
                Channel.external_id == item.channel_external_id,
            )
        )
        if channel is None:
            channel = Channel(
                source=item.source,
                external_id=item.channel_external_id,
                title=item.channel_title,
                cursor=0,
            )
            s.add(channel)
            await s.flush()
        channel.title = item.channel_title
        if item.channel_description is not None:
            channel.description = item.channel_description
        return channel

    async def _find_duplicate(self, s: AsyncSession, row: Item) -> int | None:
        if row.text_norm_hash is None:
            return None
        earliest = await s.scalar(
            select(Item)
            .where(Item.text_norm_hash == row.text_norm_hash, Item.id != row.id)
            .order_by(Item.published_at, Item.id)
            .limit(1)
        )
        if earliest is None or earliest.published_at > row.published_at:
            return None
        return earliest.id
