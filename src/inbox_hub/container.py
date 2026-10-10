import logging
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from inbox_hub.db.session import make_engine, make_sessions
from inbox_hub.extractors.registry import ExtractorRegistry
from inbox_hub.resolvers.yandex_disk import YandexDiskResolver
from inbox_hub.services.feed import FeedService
from inbox_hub.services.health import HealthService
from inbox_hub.services.ingest import IngestService
from inbox_hub.services.jobs import Job, JobRunner, SyncReport
from inbox_hub.settings import Settings
from inbox_hub.sources.sdo.client import SdoHttp
from inbox_hub.sources.sdo.rest import SdoRest
from inbox_hub.sources.sdo.source import SdoSource
from inbox_hub.sources.telegram.client import TelegramClientManager
from inbox_hub.sources.telegram.source import TelegramSource
from inbox_hub.storage.files import FileStore

log = logging.getLogger(__name__)


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
    extra_clients: list[httpx.AsyncClient] = field(default_factory=list)

    @classmethod
    async def build(cls, settings: Settings, with_sources: bool = True) -> Container:
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
            sdo_http = httpx.AsyncClient(timeout=120, follow_redirects=True)
            c.extra_clients.append(sdo_http)
            c.jobs["sdo"] = SdoSource(
                SdoHttp(sdo_http, settings.sdo),
                ingest,
                settings.sdo,
                ZoneInfo(settings.tz),
                rest=SdoRest(sdo_http, settings.sdo),
            )
        return c

    async def start(self) -> None:
        if self.tg is not None:
            try:
                await self.tg.start()
                self.jobs["telegram"].register_handlers()
            except Exception as exc:
                log.exception("telegram unavailable, running without it")
                self.jobs.pop("telegram", None)
                await self.health.record(SyncReport("telegram", error=repr(exc)))

    async def stop(self) -> None:
        if self.tg is not None:
            await self.tg.stop()
        for client in self.extra_clients:
            await client.aclose()
        await self.http.aclose()
        await self.engine.dispose()
