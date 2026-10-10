from datetime import UTC, datetime, timedelta

import pytest

from inbox_hub.db.session import create_all, make_engine, make_sessions
from inbox_hub.extractors.registry import ExtractorRegistry
from inbox_hub.services.ingest import IngestService
from inbox_hub.settings import (
    ApiSettings,
    DatabaseSettings,
    SdoSettings,
    Settings,
    TelegramSettings,
)
from inbox_hub.storage.files import FileStore


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        telegram=TelegramSettings(api_id=1, api_hash="h", source_ids=[-1001]),
        sdo=SdoSettings(url="https://sdo.test", username="u", password="p"),
        api=ApiSettings(token="secret"),
        database=DatabaseSettings(url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"),
        data_dir=tmp_path / "data",
        max_file_mb=1,
        _env_file=None,
    )


@pytest.fixture
async def engine(settings):
    eng = make_engine(settings.database.url)
    await create_all(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
def sessions(engine):
    return make_sessions(engine)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def tick(self, minutes=1):
        self.now += timedelta(minutes=minutes)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(settings):
    return FileStore(settings.data_dir / "files")


@pytest.fixture
def ingest(sessions, store, clock, settings):
    return IngestService(
        sessions=sessions,
        store=store,
        extractors=ExtractorRegistry(),
        resolvers=[],
        max_file_bytes=settings.max_file_bytes,
        clock=clock,
    )
