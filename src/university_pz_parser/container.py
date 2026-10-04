from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from university_pz_parser.db.session import make_engine, make_sessions
from university_pz_parser.extractors.registry import ExtractorRegistry
from university_pz_parser.resolvers.yandex_disk import YandexDiskResolver
from university_pz_parser.services.feed import FeedService
from university_pz_parser.services.health import HealthService
from university_pz_parser.services.ingest import IngestService
from university_pz_parser.services.jobs import Job, JobRunner
from university_pz_parser.settings import Settings
from university_pz_parser.sources.telegram.client import TelegramClientManager
from university_pz_parser.sources.telegram.source import TelegramSource
from university_pz_parser.storage.files import FileStore


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    store: FileStore
    ingest: IngestService
    feed: FeedService
    health: HealthService
    runner: JobRunner
    jobs: dict[str, Job] = field(default_factory=dict)
    tg: TelegramClientManager | None = None

    @classmethod
    async def build(cls, settings: Settings, with_sources: bool = True) -> "Container":
        engine = make_engine(settings.database.url)
        sessions = make_sessions(engine)
        http = httpx.AsyncClient(timeout=60, follow_redirects=True)
        store = FileStore(settings.data_dir / "files")
        resolvers = [YandexDiskResolver(http)]
        ingest = IngestService(
            sessions, store, ExtractorRegistry(), resolvers, settings.max_file_bytes
        )
        health = HealthService(sessions)
        c = cls(
            settings=settings,
            engine=engine,
            sessions=sessions,
            http=http,
            store=store,
            ingest=ingest,
            feed=FeedService(sessions),
            health=health,
            runner=JobRunner(health),
        )
        if with_sources:
            tg = TelegramClientManager(settings.telegram)
            c.tg = tg
            c.jobs["telegram"] = TelegramSource(tg.client, ingest, settings.telegram)
        return c

    async def start(self) -> None:
        if self.tg is not None:
            await self.tg.start()
            self.jobs["telegram"].register_handlers()

    async def stop(self) -> None:
        if self.tg is not None:
            await self.tg.stop()
        await self.http.aclose()
        await self.engine.dispose()
