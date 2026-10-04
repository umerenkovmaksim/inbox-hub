# Study Inbox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One asyncio server that collects every Item from Telegram study chats and the СДО (Moodle 4.5) into SQLite with file dedup and text extraction, plus an HTTP API and a laptop-side MCP adapter that reads a time window and marks Items as Received.

**Architecture:** Sources (Telegram via Telethon, СДО via httpx + selectolax) produce `IncomingItem` DTOs; `IngestService` decides new/changed/same by `(source, external_key)` + `content_hash`, fetches files, dedups by sha256, extracts text in threads, and writes in one short transaction. `FeedService` serves the API; APScheduler + `JobRunner` drive the sources; the MCP adapter is a separate package that only speaks HTTP.

**Tech Stack:** Python 3.14, uv, SQLAlchemy 2.x async + aiosqlite, Alembic, FastAPI + uvicorn, Telethon 1.x, httpx, selectolax, APScheduler 3.x, python-docx, pypdf, python-pptx, openpyxl, typer, pytest + pytest-asyncio + respx, `mcp>=2` (class `MCPServer`, not `FastMCP`).

**Spec:** `docs/superpowers/specs/2026-10-04-study-inbox-design.md`. Glossary: `CONTEXT.md`.

## Global Constraints

- Python package stays `university_pz_parser` under `src/`; Python `>=3.14`.
- No code comments and no docstrings in the server package (project rule). MCP tool docstrings are allowed — they are the tool descriptions Claude sees.
- Exactly one uvicorn worker; nothing blocking in the event loop (file IO, hashing, extraction → `asyncio.to_thread`).
- All datetimes are timezone-aware UTC in Python; SQLite stores naive UTC through `UTCDateTime`.
- Item identity: `UNIQUE(source, external_key)`; Telegram key `"{chat_id}:{msg_id}"`, СДО module `"cm:{cmid}"`, СДО section `"section:{course_id}:{number}"`.
- File identity: sha256 of content. Replaced file → new link + old link `detached_at`; blobs never deleted.
- `max_file_mb = 50`; feed file text truncated to 4000 chars; stored extraction capped at 200 000 chars.
- Editing an Item: `edited=True`, `edited_at`, `previous_text`, `received_at=None`. Own submission status is not hashed; grade, grading state, teacher feedback, text, title, dates, file remote ids are hashed.
- Telegram backfill from `2026-09-01`; edit recheck 3 days; СДО every 3 h 07–22 Europe/Moscow; Telegram catch-up hourly.
- Every HTTP endpoint requires `Authorization: Bearer <API__TOKEN>`.
- MCP adapter has no imports from `university_pz_parser`; deps `mcp>=2`, `httpx`.

## Review Focus

- A file download fails mid-ingest (network blip, Telegram FloodWait on media) → the Item is still stored, the failed file is excluded from `content_hash` so the next run retries it, and an already-attached previous version is not detached. Test in Task 6.
- The same PDF is posted in Telegram and attached in the СДО → one `files` row, two `item_files` links, extraction runs once. Test in Task 6.
- Teacher replaces a СДО attachment with a same-named file → new sha256 link, old link detached, Item becomes Edited and un-Received; changing only own submission status does not. Tests in Tasks 6 and 12.
- A Telegram message is edited after it was Received → it reappears in `GET /items` for a window containing `edited_at`, with `previous_text`. Test in Task 7.
- Malformed or partial СДО HTML (module without link, empty sections, restricted modules) → parser skips/keeps them without crashing. Test in Task 11.

---

## File Structure

```
pyproject.toml                                  deps, scripts, pytest config
.env.example                                    all settings
alembic.ini, src/alembic/env.py                 async-free env using sync URL
src/alembic/versions/0001_initial.py
src/university_pz_parser/
  __main__.py                                   typer CLI
  settings.py                                   pydantic-settings
  enums.py                                      Source
  logging.py                                    configure_logging()
  domain/dto.py                                 IncomingFile, IncomingItem, SkippedFile
  domain/hashing.py                             content_hash, text_norm_hash
  db/base.py                                    Base
  db/types.py                                   UTCDateTime
  db/models.py                                  Channel, Item, File, ItemFile, SourceHealth
  db/session.py                                 make_engine, make_sessions
  storage/files.py                              FileStore
  extractors/registry.py                        ExtractorRegistry, Extraction
  extractors/formats.py                         per-format text functions
  resolvers/base.py                             LinkResolver protocol
  resolvers/yandex_disk.py                      YandexDiskResolver
  services/ingest.py                            IngestService, IngestStatus
  services/feed.py                              FeedService, FeedQuery
  services/health.py                            HealthService
  services/jobs.py                              SyncReport, Job, JobRunner
  sources/telegram/client.py                    TelegramClientManager
  sources/telegram/mapping.py                   to_incoming
  sources/telegram/source.py                    TelegramSource
  sources/sdo/dates.py                          parse_ru_datetime
  sources/sdo/html.py                           page parsers
  sources/sdo/client.py                         SdoHttp (login, get, head)
  sources/sdo/rest.py                           SdoRest (mobile token API)
  sources/sdo/source.py                         SdoSource
  scheduler.py                                  build_scheduler
  container.py                                  Container
  api/app.py, api/deps.py, api/schemas.py
  api/routers/items.py, files.py, health.py, admin.py
  cli/import_export.py                          Telegram JSON export → IngestService
mcp_adapter/pyproject.toml
mcp_adapter/src/study_inbox_mcp/server.py
deploy/study-inbox.service, deploy/Caddyfile
tests/...                                       mirrors src
```

Files deleted: `src/university_pz_parser/dto.py`, `main.py`, `sources/telegram/parser.py`, `sources/base.py` (empty).

---

### Task 1: Dependencies, settings, test harness

**Files:**
- Modify: `pyproject.toml`, `.env.example`, `.gitignore`
- Modify: `src/university_pz_parser/settings.py`
- Create: `src/university_pz_parser/logging.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_settings.py`
- Delete: `src/university_pz_parser/dto.py`, `src/university_pz_parser/main.py`, `src/university_pz_parser/sources/telegram/parser.py`, `src/university_pz_parser/sources/base.py`

**Interfaces:**
- Produces: `Settings`, `TelegramSettings`, `SdoSettings`, `DatabaseSettings`, `ApiSettings`, `get_settings()`; fixture `settings` in `tests/conftest.py` returning a `Settings` rooted in `tmp_path`.

- [ ] **Step 1: Replace dependencies**

```bash
uv remove ruff python-dotenv
uv add "sqlalchemy[asyncio]>=2.0" aiosqlite alembic pydantic pydantic-settings telethon fastapi "uvicorn[standard]" httpx "apscheduler>=3.11,<4" selectolax python-docx pypdf python-pptx openpyxl typer
uv add --dev pytest pytest-asyncio respx ruff
uv add cryptg || echo "cryptg has no 3.14 wheel; skip it"
```

Then edit `pyproject.toml`:

```toml
[project.scripts]
university-pz-parser = "university_pz_parser.__main__:app"

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
```

- [ ] **Step 2: Write failing settings test**

```python
# tests/test_settings.py
from datetime import UTC, datetime

from university_pz_parser.settings import Settings


def test_nested_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM__API_ID", "1")
    monkeypatch.setenv("TELEGRAM__API_HASH", "h")
    monkeypatch.setenv("TELEGRAM__SOURCE_IDS", "[-1001, -1002]")
    monkeypatch.setenv("SDO__USERNAME", "u")
    monkeypatch.setenv("SDO__PASSWORD", "p")
    monkeypatch.setenv("SDO__EXCLUDE_COURSE_IDS", "[2309]")
    monkeypatch.setenv("API__TOKEN", "t")
    s = Settings()
    assert s.telegram.source_ids == [-1001, -1002]
    assert s.telegram.backfill_from == datetime(2026, 9, 1, tzinfo=UTC)
    assert s.sdo.exclude_course_ids == [2309]
    assert s.database.url == "sqlite+aiosqlite:///data/inbox.db"
    assert s.max_file_mb == 50
```

- [ ] **Step 3: Run it, expect FAIL** — `uv run pytest tests/test_settings.py -v` → `ValidationError`/`AttributeError` (no `sdo`, `api`).

- [ ] **Step 4: Implement settings**

```python
# src/university_pz_parser/settings.py
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseModel):
    api_id: int
    api_hash: SecretStr
    source_ids: list[int]
    session_path: Path = Path("data/telegram")
    backfill_from: datetime = datetime(2026, 9, 1, tzinfo=UTC)
    edit_recheck_days: int = 3


class SdoSettings(BaseModel):
    url: str = "https://sdo.timacad.ru"
    username: str
    password: SecretStr
    exclude_course_ids: list[int] = []


class DatabaseSettings(BaseModel):
    url: str = "sqlite+aiosqlite:///data/inbox.db"


class ApiSettings(BaseModel):
    token: SecretStr
    host: str = "127.0.0.1"
    port: int = 8000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    telegram: TelegramSettings
    sdo: SdoSettings
    api: ApiSettings
    database: DatabaseSettings = DatabaseSettings()
    data_dir: Path = Path("data")
    max_file_mb: int = 50
    feed_text_limit: int = 4000
    tz: str = "Europe/Moscow"

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

```python
# src/university_pz_parser/logging.py
import logging


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
```

```python
# tests/conftest.py
import pytest

from university_pz_parser.settings import (
    ApiSettings,
    DatabaseSettings,
    SdoSettings,
    Settings,
    TelegramSettings,
)


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
```

`.env.example`:

```dotenv
TELEGRAM__API_ID=123456
TELEGRAM__API_HASH=api_hash
TELEGRAM__SOURCE_IDS=[-1001234567890,-1009876543210]
TELEGRAM__BACKFILL_FROM=2026-09-01T00:00:00+03:00
SDO__URL=https://sdo.timacad.ru
SDO__USERNAME=login
SDO__PASSWORD=password
SDO__EXCLUDE_COURSE_IDS=[]
API__TOKEN=change-me
DATABASE__URL=sqlite+aiosqlite:///data/inbox.db
DATA_DIR=data
```

Append `data/` and `.pytest_cache/` to `.gitignore`. Delete the four obsolete files.

- [ ] **Step 5: Run, expect PASS** — `uv run pytest tests/test_settings.py -v`.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "chore: dependencies, settings and test harness"
```

---

### Task 2: Database layer and initial migration

**Files:**
- Create: `src/university_pz_parser/db/types.py`, `db/session.py`, `src/alembic/versions/0001_initial.py`, `tests/db/__init__.py`, `tests/db/test_models.py`
- Modify: `db/base.py`, `db/models.py`, `src/alembic/env.py`, `alembic.ini`, `tests/conftest.py`

**Interfaces:**
- Produces: `Base`; `UTCDateTime`; models `Channel`, `Item`, `File`, `ItemFile`, `SourceHealth` with the columns below; `make_engine(url: str) -> AsyncEngine`; `make_sessions(engine) -> async_sessionmaker[AsyncSession]`; `create_all(engine)` (tests only); fixture `sessions`.

- [ ] **Step 1: Failing test**

```python
# tests/db/test_models.py
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
        item.links.append(
            ItemFile(file_sha256="a" * 64, remote_id="r", attached_at=now)
        )
        s.add(item)
    async with sessions() as s:
        got = (await s.execute(select(Item))).scalar_one()
        assert got.published_at == now
        assert got.published_at.tzinfo is not None
        assert got.links[0].file.sha256 == "a" * 64
        assert got.channel.title == "ИСиТ"
```

- [ ] **Step 2: Run, expect FAIL** (`sessions` fixture missing / model fields missing).

- [ ] **Step 3: Implement**

```python
# src/university_pz_parser/db/base.py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

```python
# src/university_pz_parser/db/types.py
from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)
```

```python
# src/university_pz_parser/db/models.py
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from university_pz_parser.db.base import Base
from university_pz_parser.db.types import UTCDateTime
from university_pz_parser.enums import Source


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Source] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)


class File(Base):
    __tablename__ = "files"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str | None] = mapped_column(String(255))
    original_name: Mapped[str | None] = mapped_column(String(512))
    local_path: Mapped[str] = mapped_column(String(1024))
    text: Mapped[str | None] = mapped_column(Text)
    text_status: Mapped[str] = mapped_column(String(16))


class ItemFile(Base):
    __tablename__ = "item_files"

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    file_sha256: Mapped[str] = mapped_column(
        ForeignKey("files.sha256"), primary_key=True, index=True
    )
    remote_id: Mapped[str] = mapped_column(String(1024))
    name: Mapped[str | None] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(String(2048))
    attached_at: Mapped[datetime] = mapped_column(UTCDateTime)
    detached_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    item: Mapped["Item"] = relationship(back_populates="links")
    file: Mapped[File] = relationship(lazy="joined")


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("source", "external_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Source] = mapped_column(String(16))
    external_key: Mapped[str] = mapped_column(String(128))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(1024))
    text: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(String(2048))
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    available_from: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    content_hash: Mapped[str] = mapped_column(String(64))
    text_norm_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    edited: Mapped[bool] = mapped_column(default=False)
    previous_text: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    channel: Mapped[Channel] = relationship(lazy="joined")
    links: Mapped[list[ItemFile]] = relationship(
        back_populates="item", lazy="selectin", cascade="all, delete-orphan"
    )


class SourceHealth(Base):
    __tablename__ = "source_health"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
```

```python
# src/university_pz_parser/db/session.py
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from university_pz_parser.db.base import Base


def make_engine(url: str) -> AsyncEngine:
    parsed = make_url(url)
    if parsed.database and parsed.get_backend_name() == "sqlite":
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(url)

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(conn, _):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    return engine


def make_sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

Add to `tests/conftest.py`:

```python
from university_pz_parser.db.session import create_all, make_engine, make_sessions


@pytest.fixture
async def engine(settings):
    eng = make_engine(settings.database.url)
    await create_all(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
def sessions(engine):
    return make_sessions(engine)
```

- [ ] **Step 4: Run, expect PASS** — `uv run pytest tests/db -v`.

- [ ] **Step 5: Alembic env + initial migration**

In `src/alembic/env.py` replace imports and URL wiring:

```python
from university_pz_parser.db import models  # noqa: F401
from university_pz_parser.db.base import Base
from university_pz_parser.settings import get_settings

config = context.config
config.set_main_option(
    "sqlalchemy.url",
    get_settings().database.url.replace("+aiosqlite", ""),
)
target_metadata = Base.metadata
```

and in `run_migrations_online` pass `render_as_batch=True` to `context.configure`. Then:

```bash
mkdir -p data src/alembic/versions
uv run alembic revision --autogenerate -m initial --rev-id 0001
uv run alembic upgrade head
uv run alembic check
```

Expected: `No new upgrade operations detected.` Rename the generated file to `src/alembic/versions/0001_initial.py` if needed; add `import university_pz_parser.db.types` if autogenerate rendered `university_pz_parser.db.types.UTCDateTime()`. Delete `data/inbox.db` afterwards.

- [ ] **Step 6: Commit** — `git add -A && git commit -m "feat(db): models, async session, initial migration"`

---

### Task 3: DTOs and hashing

**Files:**
- Create: `src/university_pz_parser/domain/__init__.py`, `domain/dto.py`, `domain/hashing.py`, `tests/domain/__init__.py`, `tests/domain/test_hashing.py`

**Interfaces:**
- Produces:
  - `FileFetcher = Callable[[Path], Awaitable[Path]]` — given a temp dir, downloads and returns the file path.
  - `IncomingFile(remote_id: str, name: str | None, mime: str | None = None, size: int | None = None, fetch: FileFetcher | None = None, url: str | None = None)`
  - `IncomingItem(source, external_key, channel_external_id, channel_title, kind, text, published_at: datetime | None, title=None, author=None, url=None, edited_at=None, due_at=None, available_from=None, files: tuple[IncomingFile, ...] = (), hashed_extra: dict = {}, extra: dict = {}, channel_description: str | None = None)`
  - `content_hash(item: IncomingItem, remote_ids: Iterable[str]) -> str`
  - `normalize_text(text: str) -> str`, `text_norm_hash(text: str) -> str | None` (None when normalized length < 40)

- [ ] **Step 1: Failing tests**

```python
# tests/domain/test_hashing.py
from datetime import UTC, datetime

from university_pz_parser.domain.dto import IncomingItem
from university_pz_parser.domain.hashing import content_hash, text_norm_hash
from university_pz_parser.enums import Source


def make(**kw):
    base = dict(
        source=Source.SDO,
        external_key="cm:1",
        channel_external_id="3430",
        channel_title="МИС",
        kind="assign",
        text="Сделать ПЗ1",
        published_at=None,
    )
    base.update(kw)
    return IncomingItem(**base)


def test_hash_ignores_unhashed_extra_and_file_order():
    a = content_hash(make(extra={"submission": "draft"}), ["f2", "f1"])
    b = content_hash(make(extra={"submission": "submitted"}), ["f1", "f2"])
    assert a == b


def test_hash_changes_on_due_date_and_grade():
    base = content_hash(make(), [])
    assert content_hash(make(due_at=datetime(2026, 10, 22, tzinfo=UTC)), []) != base
    assert content_hash(make(hashed_extra={"grade": "5"}), []) != base


def test_text_norm_hash_short_text_is_none():
    assert text_norm_hash("ок") is None


def test_text_norm_hash_normalizes_whitespace_and_case():
    t = "Завтра пара переносится в аудиторию 311, начало в 10:00"
    assert text_norm_hash(t) == text_norm_hash("  " + t.upper().replace(" ", "\n "))
```

- [ ] **Step 2: Run, expect FAIL** (module missing).

- [ ] **Step 3: Implement**

```python
# src/university_pz_parser/domain/dto.py
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from university_pz_parser.enums import Source

FileFetcher = Callable[[Path], Awaitable[Path]]


@dataclass(frozen=True)
class IncomingFile:
    remote_id: str
    name: str | None
    mime: str | None = None
    size: int | None = None
    fetch: FileFetcher | None = None
    url: str | None = None


@dataclass(frozen=True)
class IncomingItem:
    source: Source
    external_key: str
    channel_external_id: str
    channel_title: str
    kind: str
    text: str
    published_at: datetime | None
    title: str | None = None
    author: str | None = None
    url: str | None = None
    edited_at: datetime | None = None
    due_at: datetime | None = None
    available_from: datetime | None = None
    files: tuple[IncomingFile, ...] = ()
    hashed_extra: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    channel_description: str | None = None
```

```python
# src/university_pz_parser/domain/hashing.py
import hashlib
import json
from collections.abc import Iterable

from university_pz_parser.domain.dto import IncomingItem

MIN_DUPLICATE_LEN = 40


def _iso(value):
    return value.isoformat() if value else None


def content_hash(item: IncomingItem, remote_ids: Iterable[str]) -> str:
    payload = {
        "title": item.title,
        "text": item.text,
        "due_at": _iso(item.due_at),
        "available_from": _iso(item.available_from),
        "files": sorted(set(remote_ids)),
        "extra": item.hashed_extra,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def text_norm_hash(text: str) -> str | None:
    normalized = normalize_text(text)
    if len(normalized) < MIN_DUPLICATE_LEN:
        return None
    return hashlib.sha256(normalized.encode()).hexdigest()
```

- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `git commit -am "feat(domain): incoming DTOs and content hashing"` (add new files first).

---

### Task 4: FileStore

**Files:**
- Create: `src/university_pz_parser/storage/__init__.py`, `storage/files.py`, `tests/storage/__init__.py`, `tests/storage/test_files.py`

**Interfaces:**
- Produces: `StoredFile(sha256: str, size: int, path: Path)`; `FileStore(root: Path)` with `new_tmp_dir() -> Path`, `put(src: Path, name: str | None) -> StoredFile` (sync; moves src into `root/blobs/ab/<sha><suffix>`; if blob exists, deletes src), `resolve(local_path: str) -> Path`.

- [ ] **Step 1: Failing tests**

```python
# tests/storage/test_files.py
import hashlib

from university_pz_parser.storage.files import FileStore


def test_put_moves_and_dedups(tmp_path):
    store = FileStore(tmp_path / "store")
    a = store.new_tmp_dir() / "a.PDF"
    a.write_bytes(b"same")
    b = store.new_tmp_dir() / "b.pdf"
    b.write_bytes(b"same")
    first = store.put(a, "Лекция.PDF")
    second = store.put(b, None)
    assert first.sha256 == hashlib.sha256(b"same").hexdigest()
    assert first.path == second.path
    assert first.path.suffix == ".pdf"
    assert first.path.read_bytes() == b"same"
    assert not a.exists() and not b.exists()
    assert store.resolve(str(first.path)) == first.path
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**

```python
# src/university_pz_parser/storage/files.py
import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredFile:
    sha256: str
    size: int
    path: Path


class FileStore:
    def __init__(self, root: Path):
        self.root = root
        self.blobs = root / "blobs"
        self.tmp = root / "tmp"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.tmp.mkdir(parents=True, exist_ok=True)

    def new_tmp_dir(self) -> Path:
        return Path(tempfile.mkdtemp(dir=self.tmp))

    def put(self, src: Path, name: str | None) -> StoredFile:
        digest = hashlib.sha256()
        size = 0
        with src.open("rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
                size += len(chunk)
        sha = digest.hexdigest()
        suffix = Path(name or src.name).suffix.lower()
        dest = self.blobs / sha[:2] / f"{sha}{suffix}"
        if dest.exists():
            src.unlink()
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, dest)
        return StoredFile(sha, size, dest)

    def resolve(self, local_path: str) -> Path:
        return Path(local_path)
```

- [ ] **Step 4: Run, expect PASS.** — **Step 5: Commit** `feat(storage): content-addressed file store`.

---

### Task 5: Text extractors

**Files:**
- Create: `src/university_pz_parser/extractors/__init__.py`, `extractors/formats.py`, `extractors/registry.py`, `tests/extractors/__init__.py`, `tests/extractors/test_registry.py`

**Interfaces:**
- Produces: `Extraction(text: str | None, status: str)` where status ∈ `ok|truncated|needs_ocr|unsupported|failed`; `ExtractorRegistry(limit: int = 200_000)` with `extract(path: Path, name: str | None, mime: str | None) -> Extraction` (sync; caller wraps in `to_thread`).

- [ ] **Step 1: Failing tests**

```python
# tests/extractors/test_registry.py
import json
import zipfile

import docx
import openpyxl
import pptx
from pypdf import PdfWriter

from university_pz_parser.extractors.registry import ExtractorRegistry


def _pdf_with_text(path, text):
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        None,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode()
    objs[3] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer << /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (
        len(objs) + 1,
        xref,
    )
    path.write_bytes(bytes(out))


def test_docx(tmp_path):
    d = docx.Document()
    d.add_paragraph("Практическое задание 1")
    t = d.add_table(rows=1, cols=2)
    t.cell(0, 0).text = "Вариант"
    t.cell(0, 1).text = "3"
    p = tmp_path / "a.docx"
    d.save(p)
    r = ExtractorRegistry().extract(p, "a.docx", None)
    assert r.status == "ok"
    assert "Практическое задание 1" in r.text and "Вариант\t3" in r.text


def test_pptx_xlsx_ipynb(tmp_path):
    prs = pptx.Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Лекция 3"
    prs.save(tmp_path / "l.pptx")
    wb = openpyxl.Workbook()
    wb.active.append(["ФИО", "Балл"])
    wb.save(tmp_path / "t.xlsx")
    nb = {
        "cells": [
            {"cell_type": "markdown", "source": ["# Занятие 1"]},
            {"cell_type": "code", "source": "print(1)"},
        ]
    }
    (tmp_path / "n.ipynb").write_text(json.dumps(nb))
    reg = ExtractorRegistry()
    assert "Лекция 3" in reg.extract(tmp_path / "l.pptx", "l.pptx", None).text
    assert "ФИО\tБалл" in reg.extract(tmp_path / "t.xlsx", None, None).text
    nbt = reg.extract(tmp_path / "n.ipynb", None, None).text
    assert "# Занятие 1" in nbt and "print(1)" in nbt


def test_pdf_text_and_scan(tmp_path):
    _pdf_with_text(tmp_path / "t.pdf", "Hello PZ")
    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.write(tmp_path / "scan.pdf")
    reg = ExtractorRegistry()
    assert "Hello PZ" in reg.extract(tmp_path / "t.pdf", None, None).text
    assert reg.extract(tmp_path / "scan.pdf", None, None).status == "needs_ocr"


def test_zip_recursive_and_unsupported_and_failed(tmp_path):
    (tmp_path / "in.txt").write_text("внутри архива")
    with zipfile.ZipFile(tmp_path / "a.zip", "w") as z:
        z.write(tmp_path / "in.txt", "dir/in.txt")
    (tmp_path / "x.bin").write_bytes(b"\x00")
    (tmp_path / "bad.docx").write_bytes(b"not a zip")
    reg = ExtractorRegistry()
    zr = reg.extract(tmp_path / "a.zip", None, None)
    assert (
        zr.status == "ok" and "### dir/in.txt" in zr.text and "внутри архива" in zr.text
    )
    assert reg.extract(tmp_path / "x.bin", None, None).status == "unsupported"
    assert reg.extract(tmp_path / "bad.docx", None, None).status == "failed"


def test_truncation(tmp_path):
    (tmp_path / "big.txt").write_text("я" * 50)
    r = ExtractorRegistry(limit=10).extract(tmp_path / "big.txt", None, None)
    assert r.status == "truncated" and len(r.text) == 10
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**

```python
# src/university_pz_parser/extractors/formats.py
import json
from pathlib import Path

import docx
import openpyxl
import pptx
from pypdf import PdfReader


def docx_text(path: Path) -> str:
    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts)


def pptx_text(path: Path) -> str:
    presentation = pptx.Presentation(str(path))
    parts = []
    for number, slide in enumerate(presentation.slides, 1):
        parts.append(f"--- slide {number}")
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text)
    return "\n".join(parts)


def xlsx_text(path: Path) -> str:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    parts = []
    for sheet in workbook.worksheets:
        parts.append(f"--- sheet {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            if any(v is not None for v in row):
                parts.append("\t".join("" if v is None else str(v) for v in row))
    workbook.close()
    return "\n".join(parts)


def pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages).strip()


def ipynb_text(path: Path) -> str:
    notebook = json.loads(path.read_text(encoding="utf-8"))
    parts = []
    for cell in notebook.get("cells", []):
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else source
        if cell.get("cell_type") == "code":
            parts.append(f"```python\n{source}\n```")
        else:
            parts.append(source)
    return "\n\n".join(parts)


def plain_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")
```

```python
# src/university_pz_parser/extractors/registry.py
import logging
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from university_pz_parser.extractors import formats

log = logging.getLogger(__name__)

PLAIN = {
    ".txt",
    ".md",
    ".csv",
    ".py",
    ".sql",
    ".json",
    ".html",
    ".xml",
    ".yaml",
    ".yml",
}
HANDLERS: dict[str, Callable[[Path], str]] = {
    ".docx": formats.docx_text,
    ".pptx": formats.pptx_text,
    ".xlsx": formats.xlsx_text,
    ".pdf": formats.pdf_text,
    ".ipynb": formats.ipynb_text,
    **{suffix: formats.plain_text for suffix in PLAIN},
}
MAX_ZIP_DEPTH = 2


@dataclass(frozen=True)
class Extraction:
    text: str | None
    status: str


class ExtractorRegistry:
    def __init__(self, limit: int = 200_000):
        self.limit = limit

    def extract(self, path: Path, name: str | None, mime: str | None) -> Extraction:
        return self._cap(self._extract(path, name, 0))

    def _cap(self, result: Extraction) -> Extraction:
        if result.text is not None and len(result.text) > self.limit:
            return Extraction(result.text[: self.limit], "truncated")
        return result

    def _extract(self, path: Path, name: str | None, depth: int) -> Extraction:
        suffix = Path(name or path.name).suffix.lower()
        try:
            if suffix == ".zip":
                return self._zip(path, depth)
            handler = HANDLERS.get(suffix)
            if handler is None:
                return Extraction(None, "unsupported")
            text = handler(path)
        except Exception:
            log.warning("extraction failed for %s", name or path, exc_info=True)
            return Extraction(None, "failed")
        if suffix == ".pdf" and not text.strip():
            return Extraction(None, "needs_ocr")
        return Extraction(text, "ok")

    def _zip(self, path: Path, depth: int) -> Extraction:
        if depth >= MAX_ZIP_DEPTH:
            return Extraction(None, "unsupported")
        parts = []
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as tmp:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                target = Path(tmp) / f"m{len(parts)}{Path(info.filename).suffix}"
                target.write_bytes(archive.read(info))
                inner = self._extract(target, info.filename, depth + 1)
                body = inner.text if inner.text else f"[{inner.status}]"
                parts.append(f"### {info.filename}\n{body}")
        return Extraction("\n\n".join(parts), "ok")
```

Note: zip member names in Russian archives made on Windows are cp866; if `info.flag_bits & 0x800 == 0`, use `info.filename.encode("cp437").decode("cp866")` for display — add this in `_zip` when building the `###` header.

- [ ] **Step 4: Run, expect PASS.** — **Step 5: Commit** `feat(extractors): docx/pptx/xlsx/pdf/ipynb/plain/zip`.

---

### Task 6: IngestService

**Files:**
- Create: `src/university_pz_parser/resolvers/__init__.py`, `resolvers/base.py`, `src/university_pz_parser/services/__init__.py`, `services/ingest.py`, `tests/services/__init__.py`, `tests/services/test_ingest.py`
- Modify: `tests/conftest.py` (fixtures `store`, `ingest`, `clock`)

**Interfaces:**
- Consumes: `IncomingItem`, `IncomingFile`, `content_hash`, `text_norm_hash`, `FileStore`, `ExtractorRegistry`, models.
- Produces:
  - `class LinkResolver(Protocol): async def resolve(self, text: str) -> list[IncomingFile]`
  - `IngestStatus` StrEnum: `NEW`, `CHANGED`, `SAME`
  - `IngestService(sessions, store: FileStore, extractors: ExtractorRegistry, resolvers: Sequence[LinkResolver], max_file_bytes: int, clock: Callable[[], datetime] = utcnow)` with
    - `async ingest(item: IncomingItem) -> IngestStatus`
    - `async channel_cursor(source: Source, external_id: str) -> int`
    - `async advance_cursor(source: Source, external_id: str, value: int) -> None` (only forward; creates nothing if channel missing)
  - `utcnow() -> datetime` in `services/ingest.py`.

- [ ] **Step 1: Fixtures**

```python
# tests/conftest.py additions
from datetime import UTC, datetime, timedelta

from university_pz_parser.extractors.registry import ExtractorRegistry
from university_pz_parser.services.ingest import IngestService
from university_pz_parser.storage.files import FileStore


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
```

- [ ] **Step 2: Failing tests**

```python
# tests/services/test_ingest.py
from datetime import UTC, datetime

from sqlalchemy import func, select

from university_pz_parser.db.models import File, Item
from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.enums import Source
from university_pz_parser.extractors.registry import ExtractorRegistry
from university_pz_parser.services.ingest import IngestService, IngestStatus


def fetcher(content: bytes, name: str, calls: list | None = None):
    async def fetch(tmp):
        if calls is not None:
            calls.append(name)
        p = tmp / name
        p.write_bytes(content)
        return p

    return fetch


def failing_fetch():
    async def fetch(tmp):
        raise ConnectionError("boom")

    return fetch


def tg(msg_id=1, text="Сдать ПЗ1 до пятницы", files=(), chat="-1001", **kw):
    return IncomingItem(
        source=Source.TELEGRAM,
        external_key=f"{chat}:{msg_id}",
        channel_external_id=chat,
        channel_title="ИСиТ",
        kind="document" if files else "text",
        text=text,
        published_at=datetime(2026, 10, 1, 8, msg_id % 60, tzinfo=UTC),
        files=tuple(files),
        **kw,
    )


async def one(sessions, key):
    async with sessions() as s:
        return (
            await s.execute(select(Item).where(Item.external_key == key))
        ).scalar_one()


async def count(sessions, model):
    async with sessions() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


async def test_new_then_same_does_not_refetch(ingest, sessions):
    calls = []
    f = IncomingFile("tg:doc:1", "pz1.txt", fetch=fetcher(b"task", "pz1.txt", calls))
    assert await ingest.ingest(tg(files=[f])) is IngestStatus.NEW
    assert await ingest.ingest(tg(files=[f])) is IngestStatus.SAME
    assert calls == ["pz1.txt"]
    item = await one(sessions, "-1001:1")
    assert item.links[0].file.text == "task"
    assert item.links[0].file.text_status == "ok"


async def test_same_file_in_two_sources_is_one_file(ingest, sessions):
    a = IncomingFile("tg:doc:1", "a.txt", fetch=fetcher(b"same", "a.txt"))
    b = IncomingFile("etag:x", "b.txt", fetch=fetcher(b"same", "b.txt"))
    await ingest.ingest(tg(files=[a]))
    sdo = IncomingItem(
        source=Source.SDO,
        external_key="cm:5",
        channel_external_id="3430",
        channel_title="МИС",
        kind="assign",
        text="ПЗ1",
        published_at=None,
        files=(b,),
    )
    await ingest.ingest(sdo)
    assert await count(sessions, File) == 1
    assert (await one(sessions, "cm:5")).links[0].file_sha256 == (
        await one(sessions, "-1001:1")
    ).links[0].file_sha256


async def test_edit_after_receive_resets_received(ingest, sessions, clock):
    await ingest.ingest(tg(text="Пара в 311 аудитории"))
    async with sessions.begin() as s:
        (await s.execute(select(Item))).scalar_one().received_at = clock()
    clock.tick(30)
    assert await ingest.ingest(tg(text="Пара в 317 аудитории")) is IngestStatus.CHANGED
    item = await one(sessions, "-1001:1")
    assert item.edited and item.received_at is None
    assert item.previous_text == "Пара в 311 аудитории"
    assert item.edited_at == clock()
    assert item.published_at == datetime(2026, 10, 1, 8, 1, tzinfo=UTC)


async def test_replaced_file_detaches_old(ingest, sessions):
    v1 = IncomingFile("etag:1", "pz.txt", fetch=fetcher(b"v1", "pz.txt"))
    v2 = IncomingFile("etag:2", "pz.txt", fetch=fetcher(b"v2", "pz.txt"))
    await ingest.ingest(tg(files=[v1]))
    assert await ingest.ingest(tg(files=[v2])) is IngestStatus.CHANGED
    links = {l.file.text: l for l in (await one(sessions, "-1001:1")).links}
    assert links["v1"].detached_at is not None
    assert links["v2"].detached_at is None
    assert await count(sessions, File) == 2


async def test_failed_fetch_is_retried_and_keeps_old_link(ingest, sessions):
    ok = IncomingFile("etag:1", "pz.txt", fetch=fetcher(b"v1", "pz.txt"))
    await ingest.ingest(tg(files=[ok]))
    broken = IncomingFile("etag:1", "pz.txt", fetch=failing_fetch())
    extra = IncomingFile("etag:9", "new.txt", fetch=failing_fetch())
    assert await ingest.ingest(tg(files=[broken, extra])) is IngestStatus.CHANGED
    item = await one(sessions, "-1001:1")
    assert [l.detached_at for l in item.links] == [None]
    assert item.extra["skipped_files"][0]["reason"] == "fetch_failed"
    fixed = IncomingFile("etag:9", "new.txt", fetch=fetcher(b"n", "new.txt"))
    assert await ingest.ingest(tg(files=[ok, fixed])) is IngestStatus.CHANGED
    assert len((await one(sessions, "-1001:1")).links) == 2


async def test_oversized_file_is_metadata_only(ingest, sessions, settings):
    big = IncomingFile(
        "tg:doc:2",
        "video.mp4",
        size=settings.max_file_bytes + 1,
        fetch=fetcher(b"x", "v"),
    )
    await ingest.ingest(tg(files=[big]))
    item = await one(sessions, "-1001:1")
    assert item.links == []
    assert item.extra["skipped_files"] == [
        {
            "name": "video.mp4",
            "size": settings.max_file_bytes + 1,
            "url": None,
            "reason": "too_large",
        }
    ]
    assert await ingest.ingest(tg(files=[big])) is IngestStatus.SAME


async def test_text_duplicate_points_to_earliest(ingest, sessions):
    text = "Внимание! Завтра пары по моделированию не будет, переносим на четверг"
    await ingest.ingest(tg(msg_id=2, text=text, chat="-1001"))
    await ingest.ingest(tg(msg_id=5, text=text.upper(), chat="-1002"))
    first = await one(sessions, "-1001:2")
    second = await one(sessions, "-1002:5")
    assert first.duplicate_of_id is None
    assert second.duplicate_of_id == first.id


async def test_unhashed_extra_updates_silently(ingest, sessions):
    await ingest.ingest(tg(extra={"submission": "нет ответа"}))
    assert (
        await ingest.ingest(tg(extra={"submission": "отправлено"})) is IngestStatus.SAME
    )
    item = await one(sessions, "-1001:1")
    assert item.extra["submission"] == "отправлено" and not item.edited


async def test_cursor_only_moves_forward(ingest):
    await ingest.ingest(tg())
    assert await ingest.channel_cursor(Source.TELEGRAM, "-1001") == 0
    await ingest.advance_cursor(Source.TELEGRAM, "-1001", 10)
    await ingest.advance_cursor(Source.TELEGRAM, "-1001", 3)
    assert await ingest.channel_cursor(Source.TELEGRAM, "-1001") == 10


async def test_resolver_files_are_attached(sessions, store, clock, settings):
    class Fake:
        async def resolve(self, text):
            return (
                [IncomingFile("yadisk:abc", "r.txt", fetch=fetcher(b"disk", "r.txt"))]
                if "disk.yandex" in text
                else []
            )

    svc = IngestService(
        sessions, store, ExtractorRegistry(), [Fake()], settings.max_file_bytes, clock
    )
    await svc.ingest(tg(text="Методичка https://disk.yandex.ru/d/abc"))
    assert (await one(sessions, "-1001:1")).links[0].file.text == "disk"
```

- [ ] **Step 3: Run, expect FAIL** (module missing).

- [ ] **Step 4: Implement**

```python
# src/university_pz_parser/resolvers/base.py
from typing import Protocol

from university_pz_parser.domain.dto import IncomingFile


class LinkResolver(Protocol):
    async def resolve(self, text: str) -> list[IncomingFile]: ...
```

```python
# src/university_pz_parser/services/ingest.py
import asyncio
import logging
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from university_pz_parser.db.models import Channel, File, Item, ItemFile
from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.domain.hashing import content_hash, text_norm_hash
from university_pz_parser.enums import Source
from university_pz_parser.extractors.registry import Extraction, ExtractorRegistry
from university_pz_parser.resolvers.base import LinkResolver
from university_pz_parser.storage.files import FileStore, StoredFile

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
            async with self.sessions.begin() as s:
                row = await self._find(s, item)
                if row is not None and row.content_hash == digest:
                    row.extra = {**row.extra, **item.extra, **item.hashed_extra}
                    return IngestStatus.SAME
            result = await self._fetch(files)
            extractions = await self._extract_new(result.fetched)
            final_digest = content_hash(
                item,
                [
                    f.remote_id
                    for f in files
                    if f.remote_id not in result.failed_remote_ids
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
            try:
                files.extend(await resolver.resolve(haystack))
            except Exception:
                log.warning(
                    "resolver %s failed for %s",
                    type(resolver).__name__,
                    item.external_key,
                    exc_info=True,
                )
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
                row.edited_at = item.edited_at or now
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
```

Two details the tests pin and the engineer must not "simplify":
- The SAME path still writes `extra` (submission status) — that is why it uses `sessions.begin()`.
- The failed-fetch hash excludes failed remote ids, so the stored hash differs from the next run's full hash and the file is retried.

- [ ] **Step 5: Run, expect PASS** — `uv run pytest tests/services/test_ingest.py -v`. If `test_failed_fetch_is_retried_and_keeps_old_link` fails on the second ingest returning SAME, check that `final_digest` (not `digest`) is stored.

- [ ] **Step 6: Commit** — `feat(ingest): upsert by key, file dedup, edits, text duplicates`.

---

### Task 7: FeedService

**Files:**
- Create: `src/university_pz_parser/services/feed.py`, `tests/services/test_feed.py`

**Interfaces:**
- Produces: `FeedQuery(date_from: datetime, date_to: datetime, source: Source | None = None, include_received: bool = False, limit: int = 200)`; `FeedService(sessions, clock=utcnow)` with `async list_items(q) -> list[Item]`, `async get_item(item_id: int) -> Item | None`, `async mark_received(ids: list[int]) -> int`, `async get_file(sha256: str) -> File | None`.
- Window rule: `published_at ∈ [from, to)` OR (`edited` AND `edited_at ∈ [from, to)`); ordered by `coalesce(edited_at, published_at)`, then id.

- [ ] **Step 1: Failing tests**

```python
# tests/services/test_feed.py
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
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**

```python
# src/university_pz_parser/services/feed.py
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
```

Note: in `test_old_item_edited_in_window_reappears`, the first ingest's `received_at` is set via `mark_received([1])` — ids start at 1 in a fresh DB.

- [ ] **Step 4: Run, expect PASS.** — **Step 5: Commit** `feat(feed): time-window query and received marks`.

---

### Task 8: Health, jobs, HTTP API

**Files:**
- Create: `services/health.py`, `services/jobs.py`, `api/__init__.py`, `api/app.py`, `api/deps.py`, `api/schemas.py`, `api/routers/__init__.py`, `api/routers/{items,files,health,admin}.py`, `src/university_pz_parser/container.py`, `tests/api/__init__.py`, `tests/api/test_api.py`, `tests/services/test_jobs.py`

**Interfaces:**
- Produces:
  - `SyncReport(source: str, new: int = 0, changed: int = 0, same: int = 0, warnings: list[str] = [], error: str | None = None)` with `count(status: IngestStatus)` and `as_dict()`.
  - `class Job(Protocol): name: str; async def sync(self) -> SyncReport`
  - `HealthService(sessions, clock)` with `async record(report: SyncReport) -> None`, `async snapshot() -> list[SourceHealth]`.
  - `JobRunner(health: HealthService)` with `async run(job: Job) -> SyncReport` (per-name `asyncio.Lock`, never raises).
  - `Container` dataclass: `settings, engine, sessions, store, ingest, feed, health, runner, jobs: dict[str, Job], http: httpx.AsyncClient, tg: TelegramClientManager | None`; `@classmethod async build(settings, with_sources: bool = True) -> Container`; `async start()`, `async stop()`. In this task `with_sources=False` path only; Tasks 10 & 12 fill the sources.
  - `create_app(container: Container | None = None) -> FastAPI`.
  - Schemas: `FileOut`, `SkippedFileOut`, `ItemOut`, `ItemsOut{items, count}`, `ReceiveIn{ids}`, `ReceiveOut{updated}`, `HealthOut`.

- [ ] **Step 1: Failing tests**

```python
# tests/services/test_jobs.py
from university_pz_parser.services.health import HealthService
from university_pz_parser.services.jobs import JobRunner, SyncReport


class Boom:
    name = "telegram"

    async def sync(self):
        raise RuntimeError("down")


class Ok:
    name = "sdo"

    async def sync(self):
        return SyncReport("sdo", new=2)


async def test_runner_records_and_swallows(sessions, clock):
    health = HealthService(sessions, clock=clock)
    runner = JobRunner(health)
    bad = await runner.run(Boom())
    good = await runner.run(Ok())
    assert bad.error == "RuntimeError('down')"
    assert good.new == 2
    rows = {h.source: h for h in await health.snapshot()}
    assert rows["telegram"].last_ok_at is None and rows["telegram"].last_error
    assert rows["sdo"].last_ok_at == clock() and rows["sdo"].last_report["new"] == 2
```

```python
# tests/api/test_api.py
from datetime import UTC, datetime

import httpx
import pytest

from university_pz_parser.api.app import create_app
from university_pz_parser.container import Container
from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.enums import Source

H = {"Authorization": "Bearer secret"}


@pytest.fixture
async def container(settings, engine):
    c = await Container.build(settings, with_sources=False)
    yield c
    await c.stop()


async def seed(c):
    async def fetch(tmp):
        p = tmp / "pz.txt"
        p.write_text("x" * 5000)
        return p

    await c.ingest.ingest(
        IncomingItem(
            source=Source.SDO,
            external_key="cm:1",
            channel_external_id="3430",
            channel_title="МИС",
            kind="assign",
            text="ПЗ1",
            title="Практическое задание №1",
            published_at=datetime(2026, 10, 1, 10, tzinfo=UTC),
            due_at=datetime(2026, 10, 22, tzinfo=UTC),
            files=(IncomingFile("etag:1", "pz.txt", fetch=fetch),),
        )
    )


def client_for(container):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(container)), base_url="http://t"
    )


async def test_auth_required(container):
    async with client_for(container) as client:
        r = await client.get(
            "/items",
            params={"from": "2026-10-01T00:00:00Z", "to": "2026-10-02T00:00:00Z"},
        )
        assert r.status_code == 401
        assert (
            await client.get("/health", headers={"Authorization": "Bearer nope"})
        ).status_code == 401


async def test_items_flow(container):
    await seed(container)
    params = {"from": "2026-10-01T00:00:00+03:00", "to": "2026-10-02T00:00:00+03:00"}
    async with client_for(container) as client:
        body = (await client.get("/items", params=params, headers=H)).json()
        assert body["count"] == 1
        it = body["items"][0]
        assert it["channel"] == "МИС" and it["source"] == "sdo"
        f = it["files"][0]
        assert len(f["text"]) == 4000 and f["text_truncated"] is True
        full = (await client.get(f"/items/{it['id']}", headers=H)).json()
        assert len(full["files"][0]["text"]) == 5000
        raw = await client.get(f"/files/{f['sha256']}", headers=H)
        assert raw.status_code == 200 and raw.content == b"x" * 5000
        r = await client.post("/items/receive", json={"ids": [it["id"]]}, headers=H)
        assert r.json() == {"updated": 1}
        assert (await client.get("/items", params=params, headers=H)).json()[
            "count"
        ] == 0
        assert (await client.get("/items/999", headers=H)).status_code == 404
        assert (await client.get("/files/" + "0" * 64, headers=H)).status_code == 404
        assert (
            await client.get(
                "/items",
                params={"from": "2026-10-01T00:00:00", "to": "2026-10-02T00:00:00"},
                headers=H,
            )
        ).status_code == 422


async def test_admin_unknown_source(container):
    async with client_for(container) as client:
        assert (await client.post("/admin/sync/nope", headers=H)).status_code == 404
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement services**

```python
# src/university_pz_parser/services/jobs.py
import asyncio
import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Protocol

from university_pz_parser.services.ingest import IngestStatus

log = logging.getLogger(__name__)


@dataclass
class SyncReport:
    source: str
    new: int = 0
    changed: int = 0
    same: int = 0
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def count(self, status: IngestStatus) -> None:
        setattr(self, status.value, getattr(self, status.value) + 1)

    def as_dict(self) -> dict:
        return asdict(self)


class Job(Protocol):
    name: str

    async def sync(self) -> SyncReport: ...


class JobRunner:
    def __init__(self, health):
        self.health = health
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def run(self, job: Job) -> SyncReport:
        async with self._locks[job.name]:
            try:
                report = await job.sync()
            except Exception as exc:
                log.exception("job %s failed", job.name)
                report = SyncReport(job.name, error=repr(exc))
            log.info("job %s: %s", job.name, report.as_dict())
            try:
                await self.health.record(report)
            except Exception:
                log.exception("health record failed")
            return report
```

```python
# src/university_pz_parser/services/health.py
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from university_pz_parser.db.models import SourceHealth
from university_pz_parser.services.ingest import utcnow


class HealthService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        self.clock = clock

    async def record(self, report) -> None:
        now = self.clock()
        async with self.sessions.begin() as s:
            row = await s.get(SourceHealth, report.source)
            if row is None:
                row = SourceHealth(source=report.source)
                s.add(row)
            row.last_run_at = now
            row.last_report = report.as_dict()
            row.last_error = report.error
            if report.error is None:
                row.last_ok_at = now

    async def snapshot(self) -> list[SourceHealth]:
        async with self.sessions() as s:
            return list(
                (
                    await s.scalars(select(SourceHealth).order_by(SourceHealth.source))
                ).all()
            )
```

- [ ] **Step 4: Implement container (sources wired later)**

```python
# src/university_pz_parser/container.py
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from university_pz_parser.db.session import make_engine, make_sessions
from university_pz_parser.extractors.registry import ExtractorRegistry
from university_pz_parser.services.feed import FeedService
from university_pz_parser.services.health import HealthService
from university_pz_parser.services.ingest import IngestService
from university_pz_parser.services.jobs import Job, JobRunner
from university_pz_parser.settings import Settings
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
    tg: object | None = None

    @classmethod
    async def build(cls, settings: Settings, with_sources: bool = True) -> "Container":
        engine = make_engine(settings.database.url)
        sessions = make_sessions(engine)
        http = httpx.AsyncClient(timeout=60, follow_redirects=True)
        store = FileStore(settings.data_dir / "files")
        resolvers = []
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
        return c

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()
```

- [ ] **Step 5: Implement API**

```python
# src/university_pz_parser/api/deps.py
import hmac

from fastapi import Depends, Header, HTTPException, Request

from university_pz_parser.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


async def require_token(
    authorization: str | None = Header(default=None),
    c: Container = Depends(get_container),
) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    expected = c.settings.api.token.get_secret_value()
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        token.encode(), expected.encode()
    ):
        raise HTTPException(status_code=401)
```

```python
# src/university_pz_parser/api/schemas.py
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from university_pz_parser.db.models import Item, SourceHealth


class FileOut(BaseModel):
    sha256: str
    name: str | None
    mime: str | None
    size: int
    url: str | None
    text: str | None
    text_status: str
    text_truncated: bool
    added_on_edit: bool


class SkippedFileOut(BaseModel):
    name: str | None
    size: int | None
    url: str | None
    reason: str


class ItemOut(BaseModel):
    id: int
    source: str
    channel: str
    kind: str
    title: str | None
    text: str
    author: str | None
    url: str | None
    published_at: datetime
    edited: bool
    edited_at: datetime | None
    previous_text: str | None
    due_at: datetime | None
    available_from: datetime | None
    duplicate_of_id: int | None
    received_at: datetime | None
    files: list[FileOut]
    removed_files: list[FileOut]
    skipped_files: list[SkippedFileOut]
    extra: dict[str, Any]


class ItemsOut(BaseModel):
    items: list[ItemOut]
    count: int


class ReceiveIn(BaseModel):
    ids: list[int]


class ReceiveOut(BaseModel):
    updated: int


class HealthOut(BaseModel):
    source: str
    last_run_at: datetime | None
    last_ok_at: datetime | None
    last_error: str | None
    last_report: dict[str, Any]


def _file_out(link, item: Item, limit: int | None) -> FileOut:
    text = link.file.text
    truncated = link.file.text_status == "truncated"
    if text is not None and limit is not None and len(text) > limit:
        text, truncated = text[:limit], True
    return FileOut(
        sha256=link.file_sha256,
        name=link.name or link.file.original_name,
        mime=link.file.mime,
        size=link.file.size,
        url=link.url,
        text=text,
        text_status=link.file.text_status,
        text_truncated=truncated,
        added_on_edit=link.attached_at > item.first_seen_at,
    )


def item_out(item: Item, text_limit: int | None) -> ItemOut:
    extra = dict(item.extra or {})
    skipped = extra.pop("skipped_files", [])
    return ItemOut(
        id=item.id,
        source=item.source,
        channel=item.channel.title,
        kind=item.kind,
        title=item.title,
        text=item.text,
        author=item.author,
        url=item.url,
        published_at=item.published_at,
        edited=item.edited,
        edited_at=item.edited_at,
        previous_text=item.previous_text,
        due_at=item.due_at,
        available_from=item.available_from,
        duplicate_of_id=item.duplicate_of_id,
        received_at=item.received_at,
        files=[
            _file_out(l, item, text_limit) for l in item.links if l.detached_at is None
        ],
        removed_files=[
            _file_out(l, item, 0) for l in item.links if l.detached_at is not None
        ],
        skipped_files=[SkippedFileOut(**s) for s in skipped],
        extra=extra,
    )


def health_out(row: SourceHealth) -> HealthOut:
    return HealthOut(
        source=row.source,
        last_run_at=row.last_run_at,
        last_ok_at=row.last_ok_at,
        last_error=row.last_error,
        last_report=row.last_report or {},
    )
```

```python
# src/university_pz_parser/api/routers/items.py
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from university_pz_parser.api.deps import get_container
from university_pz_parser.api.schemas import (
    ItemOut,
    ItemsOut,
    ReceiveIn,
    ReceiveOut,
    item_out,
)
from university_pz_parser.container import Container
from university_pz_parser.enums import Source
from university_pz_parser.services.feed import FeedQuery

router = APIRouter()


@router.get("/items", response_model=ItemsOut)
async def list_items(
    date_from: datetime = Query(alias="from"),
    date_to: datetime = Query(alias="to"),
    source: Source | None = None,
    include_received: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    c: Container = Depends(get_container),
):
    if date_from.tzinfo is None or date_to.tzinfo is None:
        raise HTTPException(status_code=422, detail="from/to must include timezone")
    items = await c.feed.list_items(
        FeedQuery(date_from, date_to, source, include_received, limit)
    )
    out = [item_out(i, c.settings.feed_text_limit) for i in items]
    return ItemsOut(items=out, count=len(out))


@router.get("/items/{item_id}", response_model=ItemOut)
async def get_item(item_id: int, c: Container = Depends(get_container)):
    item = await c.feed.get_item(item_id)
    if item is None:
        raise HTTPException(status_code=404)
    return item_out(item, None)


@router.post("/items/receive", response_model=ReceiveOut)
async def receive(body: ReceiveIn, c: Container = Depends(get_container)):
    return ReceiveOut(updated=await c.feed.mark_received(body.ids))
```

```python
# src/university_pz_parser/api/routers/files.py
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from university_pz_parser.api.deps import get_container
from university_pz_parser.container import Container

router = APIRouter()


@router.get("/files/{sha256}")
async def get_file(sha256: str, c: Container = Depends(get_container)):
    row = await c.feed.get_file(sha256)
    if row is None:
        raise HTTPException(status_code=404)
    path = c.store.resolve(row.local_path)
    if not path.exists():
        raise HTTPException(status_code=410)
    return FileResponse(
        path,
        media_type=row.mime or "application/octet-stream",
        filename=row.original_name or path.name,
    )
```

```python
# src/university_pz_parser/api/routers/health.py
from fastapi import APIRouter, Depends

from university_pz_parser.api.deps import get_container
from university_pz_parser.api.schemas import HealthOut, health_out
from university_pz_parser.container import Container

router = APIRouter()


@router.get("/health", response_model=list[HealthOut])
async def health(c: Container = Depends(get_container)):
    return [health_out(r) for r in await c.health.snapshot()]
```

```python
# src/university_pz_parser/api/routers/admin.py
from fastapi import APIRouter, Depends, HTTPException

from university_pz_parser.api.deps import get_container
from university_pz_parser.container import Container

router = APIRouter(prefix="/admin")


@router.post("/sync/{source}")
async def sync(source: str, c: Container = Depends(get_container)):
    job = c.jobs.get(source)
    if job is None:
        raise HTTPException(status_code=404)
    return (await c.runner.run(job)).as_dict()
```

```python
# src/university_pz_parser/api/app.py
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from university_pz_parser.api.deps import require_token
from university_pz_parser.api.routers import admin, files, health, items
from university_pz_parser.container import Container


def create_app(container: Container | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if container is not None:
            app.state.container = container
            yield
            return
        from university_pz_parser.runtime import run_server_lifespan

        async with run_server_lifespan(app):
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    if container is not None:
        app.state.container = container
    for module in (items, files, health, admin):
        app.include_router(module.router, dependencies=[Depends(require_token)])
    return app
```

`runtime.run_server_lifespan` is created in Task 13; until then the `container is None` path is unused.

- [ ] **Step 6: Run, expect PASS** — `uv run pytest tests/api tests/services -v`.
- [ ] **Step 7: Commit** — `feat(api): items, files, health, admin endpoints with bearer auth`.

---

### Task 9: Yandex Disk resolver

**Files:**
- Create: `src/university_pz_parser/resolvers/yandex_disk.py`, `tests/resolvers/__init__.py`, `tests/resolvers/test_yandex_disk.py`
- Modify: `container.py` (`resolvers = [YandexDiskResolver(http)]`)

**Interfaces:**
- Produces: `YandexDiskResolver(http: httpx.AsyncClient, max_items: int = 50)` implementing `LinkResolver`. Finds `https?://(disk.yandex.(ru|com)|yadi.sk)/(d|i)/[\w-]+` links. Public API `GET https://cloud-api.yandex.net/v1/disk/public/resources?public_key=<link>&limit=200`; for `type=file` → one `IncomingFile(remote_id=f"yadisk:{sha256 or md5}", name, mime=mime_type, size, url=link, fetch=<download via meta['file']>)`; for `type=dir` → each `_embedded.items` of `type=file` (one level). On HTTP error → log and return `[]`.

- [ ] **Step 1: Failing test**

```python
# tests/resolvers/test_yandex_disk.py
import httpx
import respx

from university_pz_parser.resolvers.yandex_disk import API, YandexDiskResolver


@respx.mock
async def test_file_and_dir(tmp_path):
    respx.get(API, params={"public_key": "https://disk.yandex.ru/d/AAA"}).respond(
        json={
            "type": "file",
            "name": "ПЗ1.docx",
            "mime_type": "application/x",
            "size": 4,
            "sha256": "s1",
            "file": "https://downloader.test/1",
        }
    )
    respx.get(API, params={"public_key": "https://yadi.sk/d/BBB"}).respond(
        json={
            "type": "dir",
            "name": "Лекции",
            "_embedded": {
                "items": [
                    {
                        "type": "file",
                        "name": "l1.pdf",
                        "size": 2,
                        "md5": "m1",
                        "file": "https://downloader.test/2",
                    },
                    {"type": "dir", "name": "sub"},
                ]
            },
        }
    )
    respx.get("https://downloader.test/1").respond(content=b"docx")
    async with httpx.AsyncClient() as http:
        files = await YandexDiskResolver(http).resolve(
            "см https://disk.yandex.ru/d/AAA и https://yadi.sk/d/BBB."
        )
        assert [(f.remote_id, f.name) for f in files] == [
            ("yadisk:s1", "ПЗ1.docx"),
            ("yadisk:m1", "l1.pdf"),
        ]
        path = await files[0].fetch(tmp_path)
        assert path.read_bytes() == b"docx"


@respx.mock
async def test_api_error_returns_empty():
    respx.get(API).respond(status_code=404)
    async with httpx.AsyncClient() as http:
        assert (
            await YandexDiskResolver(http).resolve("https://disk.yandex.ru/d/X") == []
        )
```

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement**

```python
# src/university_pz_parser/resolvers/yandex_disk.py
import logging
import re
from pathlib import Path

import httpx

from university_pz_parser.domain.dto import IncomingFile

log = logging.getLogger(__name__)

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
LINK = re.compile(r"https?://(?:disk\.yandex\.(?:ru|com)|yadi\.sk)/(?:d|i)/[\w-]+")


class YandexDiskResolver:
    def __init__(self, http: httpx.AsyncClient, max_items: int = 50):
        self.http = http
        self.max_items = max_items

    async def resolve(self, text: str) -> list[IncomingFile]:
        files: list[IncomingFile] = []
        for link in dict.fromkeys(LINK.findall(text or "")):
            try:
                resp = await self.http.get(
                    API, params={"public_key": link, "limit": 200}
                )
                resp.raise_for_status()
            except httpx.HTTPError:
                log.warning("yandex disk lookup failed for %s", link, exc_info=True)
                continue
            meta = resp.json()
            entries = (
                [meta]
                if meta.get("type") == "file"
                else meta.get("_embedded", {}).get("items", [])
            )
            for entry in entries[: self.max_items]:
                if entry.get("type") == "file" and entry.get("file"):
                    files.append(self._file(link, entry))
        return files

    def _file(self, link: str, entry: dict) -> IncomingFile:
        download = entry["file"]
        name = entry.get("name")

        async def fetch(tmp: Path) -> Path:
            target = tmp / (name or "yadisk.bin")
            async with self.http.stream("GET", download, follow_redirects=True) as resp:
                resp.raise_for_status()
                with target.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
            return target

        return IncomingFile(
            remote_id=f"yadisk:{entry.get('sha256') or entry.get('md5') or download}",
            name=name,
            mime=entry.get("mime_type"),
            size=entry.get("size"),
            fetch=fetch,
            url=link,
        )
```

- [ ] **Step 4: Run, expect PASS.** Wire into `Container.build`: `resolvers = [YandexDiskResolver(http)]`.
- [ ] **Step 5: Commit** — `feat(resolvers): public Yandex Disk links`.

---

### Task 10: Telegram source

**Files:**
- Create: `sources/telegram/client.py`, `sources/telegram/mapping.py`, `sources/telegram/source.py`, `tests/sources/__init__.py`, `tests/sources/telegram/__init__.py`, `tests/sources/telegram/test_mapping.py`, `tests/sources/telegram/test_source.py`
- Modify: `container.py`

**Interfaces:**
- Consumes: `IngestService.ingest/channel_cursor/advance_cursor`, `SyncReport`, `TelegramSettings`.
- Produces:
  - `NotAuthorizedError(RuntimeError)`; `TelegramClientManager(settings: TelegramSettings)` with `.client: TelegramClient`, `async connect() -> bool` (authorized?), `async start()` (raises `NotAuthorizedError`), `async stop()`.
  - `to_incoming(chat_id: int, chat_title: str, m: Message) -> IncomingItem | None`; `message_link(chat_id: int, msg_id: int) -> str | None`.
  - `TelegramSource(client, ingest, settings, clock=utcnow)` with `name = "telegram"`, `async sync() -> SyncReport`, `register_handlers() -> None`, `async handle_event(event) -> None`.

- [ ] **Step 1: Failing mapping test**

```python
# tests/sources/telegram/test_mapping.py
from datetime import UTC, datetime

from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    InputStickerSetEmpty,
    Message,
    MessageMediaDocument,
    MessageService,
    MessageActionPinMessage,
    PeerChannel,
    PeerUser,
)

from university_pz_parser.sources.telegram.mapping import message_link, to_incoming

DT = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
CHAT = -1002362006703


def doc(attrs, mime="application/pdf"):
    return Document(
        id=77,
        access_hash=1,
        file_reference=b"",
        date=DT,
        mime_type=mime,
        size=1000,
        dc_id=2,
        attributes=attrs,
    )


def test_text_message():
    m = Message(
        id=5,
        peer_id=PeerChannel(2362006703),
        date=DT,
        message="Сдать ПЗ1",
        from_id=PeerUser(9),
        edit_date=None,
    )
    item = to_incoming(CHAT, "ИСиТ", m)
    assert item.external_key == f"{CHAT}:5"
    assert item.kind == "text" and item.text == "Сдать ПЗ1" and item.files == ()
    assert item.url == "https://t.me/c/2362006703/5"
    assert item.extra["msg_id"] == 5


def test_document_message():
    m = Message(
        id=6,
        peer_id=PeerChannel(2362006703),
        date=DT,
        message="",
        media=MessageMediaDocument(
            document=doc([DocumentAttributeFilename("pz1.pdf")])
        ),
    )
    item = to_incoming(CHAT, "ИСиТ", m)
    assert item.kind == "document"
    f = item.files[0]
    assert (f.remote_id, f.name, f.size, f.mime) == (
        "tg:doc:77",
        "pz1.pdf",
        1000,
        "application/pdf",
    )
    assert f.fetch is not None


def test_noise_is_skipped():
    sticker = Message(
        id=7,
        peer_id=PeerChannel(1),
        date=DT,
        message="",
        media=MessageMediaDocument(
            document=doc(
                [DocumentAttributeSticker(alt="😀", stickerset=InputStickerSetEmpty())],
                "image/webp",
            )
        ),
    )
    service = MessageService(
        id=8, peer_id=PeerChannel(1), date=DT, action=MessageActionPinMessage()
    )
    empty = Message(id=9, peer_id=PeerChannel(1), date=DT, message="")
    assert to_incoming(CHAT, "x", sticker) is None
    assert to_incoming(CHAT, "x", service) is None
    assert to_incoming(CHAT, "x", empty) is None


def test_link_for_basic_group_is_none():
    assert message_link(-4000123, 1) is None
```

If a Telethon constructor rejects a keyword (layer changes), check `python -c "from telethon.tl.types import Message; help(Message.__init__)"` and adapt the test, not the mapping.

- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement client + mapping**

```python
# src/university_pz_parser/sources/telegram/client.py
from telethon import TelegramClient

from university_pz_parser.settings import TelegramSettings


class NotAuthorizedError(RuntimeError):
    pass


class TelegramClientManager:
    def __init__(self, settings: TelegramSettings):
        settings.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(
            str(settings.session_path),
            settings.api_id,
            settings.api_hash.get_secret_value(),
        )

    async def connect(self) -> bool:
        await self.client.connect()
        return await self.client.is_user_authorized()

    async def start(self) -> None:
        if not await self.connect():
            raise NotAuthorizedError("run: university-pz-parser login")
        await self.client.get_dialogs()

    async def stop(self) -> None:
        await self.client.disconnect()
```

```python
# src/university_pz_parser/sources/telegram/mapping.py
from pathlib import Path

from telethon.tl.custom import Message
from telethon.tl.types import MessageService
from telethon.utils import get_display_name

from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.enums import Source

DOWNLOADABLE = {"photo", "document"}


def message_link(chat_id: int, msg_id: int) -> str | None:
    raw = str(chat_id)
    if not raw.startswith("-100"):
        return None
    return f"https://t.me/c/{raw[4:]}/{msg_id}"


def message_kind(m) -> str | None:
    if isinstance(m, MessageService):
        return None
    if m.sticker or m.dice or m.game:
        return None
    if m.photo:
        return "photo"
    if m.voice:
        return "voice"
    if m.video_note:
        return "round"
    if m.video or m.gif:
        return "video"
    if m.document:
        return "document"
    if m.message:
        return "text"
    return None


def _fetcher(m: Message):
    async def fetch(tmp: Path) -> Path:
        path = await m.download_media(file=tmp)
        if path is None:
            raise RuntimeError(f"telegram media {m.id} not downloaded")
        return Path(path)

    return fetch


def _file(m: Message, kind: str) -> IncomingFile:
    media_id = m.photo.id if kind == "photo" else m.document.id
    name = m.file.name or f"{kind}_{m.id}{m.file.ext or ''}"
    return IncomingFile(
        remote_id=f"tg:{'photo' if kind == 'photo' else 'doc'}:{media_id}",
        name=name,
        mime=m.file.mime_type,
        size=m.file.size,
        fetch=_fetcher(m),
    )


def _author(m) -> str | None:
    if getattr(m, "_sender", None) is not None:
        return get_display_name(m.sender) or None
    return m.post_author


def _forwarded_from(m) -> str | None:
    if m.fwd_from is None:
        return None
    if m.fwd_from.from_name:
        return m.fwd_from.from_name
    if getattr(m.forward, "_sender", None) is not None:
        return get_display_name(m.forward.sender) or None
    return str(m.fwd_from.from_id) if m.fwd_from.from_id else None


def to_incoming(chat_id: int, chat_title: str, m) -> IncomingItem | None:
    kind = message_kind(m)
    if kind is None:
        return None
    files: tuple[IncomingFile, ...] = ()
    extra = {
        "msg_id": m.id,
        "sender_id": m.sender_id,
        "reply_to": m.reply_to_msg_id,
        "forwarded_from": _forwarded_from(m),
        "grouped_id": m.grouped_id,
    }
    if kind in DOWNLOADABLE:
        files = (_file(m, kind),)
    elif m.file is not None:
        extra["media"] = {
            "name": m.file.name,
            "size": m.file.size,
            "mime": m.file.mime_type,
            "duration": m.file.duration,
        }
    return IncomingItem(
        source=Source.TELEGRAM,
        external_key=f"{chat_id}:{m.id}",
        channel_external_id=str(chat_id),
        channel_title=chat_title,
        kind=kind,
        text=m.message or "",
        published_at=m.date,
        edited_at=m.edit_date,
        author=_author(m),
        url=message_link(chat_id, m.id),
        files=files,
        extra=extra,
    )
```

- [ ] **Step 4: Run mapping test, expect PASS.**

- [ ] **Step 5: Failing source test (fake client)**

```python
# tests/sources/telegram/test_source.py
from datetime import UTC, datetime, timedelta

from telethon.tl.types import Message, PeerChannel

from university_pz_parser.enums import Source
from university_pz_parser.sources.telegram.source import TelegramSource

DT = datetime(2026, 10, 1, 8, tzinfo=UTC)


class FakeClient:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    async def get_entity(self, chat_id):
        return type("Chat", (), {"title": "ИСиТ", "id": chat_id})()

    async def iter_messages(self, entity, **kw):
        self.calls.append(kw)
        for m in self.messages:
            if "min_id" in kw and m.id <= kw["min_id"]:
                continue
            if "offset_date" in kw and m.date < kw["offset_date"]:
                continue
            yield m


def msg(i, text, at=DT, edit=None):
    return Message(id=i, peer_id=PeerChannel(1), date=at, message=text, edit_date=edit)


async def test_backfill_then_cursor(ingest, settings, clock):
    clock.now = DT + timedelta(days=1)
    client = FakeClient([msg(1, "a"), msg(2, "b")])
    src = TelegramSource(client, ingest, settings.telegram, clock=clock)
    report = await src.sync()
    assert report.new == 2
    assert client.calls[0] == {
        "offset_date": settings.telegram.backfill_from,
        "reverse": True,
    }
    assert await ingest.channel_cursor(Source.TELEGRAM, "-1001") == 2
    client.messages.append(msg(3, "c"))
    client.calls.clear()
    report = await src.sync()
    assert client.calls[0] == {"min_id": 2, "reverse": True}
    assert report.new == 1


async def test_recheck_picks_up_edits(ingest, settings, clock):
    clock.now = DT + timedelta(days=1)
    client = FakeClient([msg(1, "пара в 311")])
    src = TelegramSource(client, ingest, settings.telegram, clock=clock)
    await src.sync()
    client.messages[0] = msg(1, "пара в 317", edit=DT + timedelta(hours=2))
    report = await src.sync()
    assert report.changed == 1
```

- [ ] **Step 6: Implement source**

```python
# src/university_pz_parser/sources/telegram/source.py
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from telethon import events
from telethon.errors import FloodWaitError
from telethon.utils import get_display_name

from university_pz_parser.enums import Source
from university_pz_parser.services.ingest import IngestService, utcnow
from university_pz_parser.services.jobs import SyncReport
from university_pz_parser.settings import TelegramSettings
from university_pz_parser.sources.telegram.mapping import to_incoming

log = logging.getLogger(__name__)


class TelegramSource:
    name = "telegram"

    def __init__(
        self,
        client,
        ingest: IngestService,
        settings: TelegramSettings,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.client = client
        self.ingest = ingest
        self.settings = settings
        self.clock = clock

    async def sync(self) -> SyncReport:
        report = SyncReport(self.name)
        for chat_id in self.settings.source_ids:
            try:
                entity = await self.client.get_entity(chat_id)
                title = get_display_name(entity) or getattr(
                    entity, "title", str(chat_id)
                )
                await self._catch_up(chat_id, entity, title, report)
                await self._recheck(chat_id, entity, title, report)
            except FloodWaitError as exc:
                report.warnings.append(f"FloodWait {exc.seconds}s on {chat_id}")
                break
        return report

    async def _catch_up(
        self, chat_id: int, entity, title: str, report: SyncReport
    ) -> None:
        cursor = await self.ingest.channel_cursor(Source.TELEGRAM, str(chat_id))
        if cursor:
            kwargs = {"min_id": cursor, "reverse": True}
        else:
            kwargs = {"offset_date": self.settings.backfill_from, "reverse": True}
        async for m in self.client.iter_messages(entity, **kwargs):
            await self._ingest(chat_id, title, m, report)
            await self.ingest.advance_cursor(Source.TELEGRAM, str(chat_id), m.id)

    async def _recheck(
        self, chat_id: int, entity, title: str, report: SyncReport
    ) -> None:
        since = self.clock() - timedelta(days=self.settings.edit_recheck_days)
        async for m in self.client.iter_messages(
            entity, offset_date=since, reverse=True
        ):
            if m.edit_date:
                await self._ingest(chat_id, title, m, report)

    async def _ingest(self, chat_id: int, title: str, m, report: SyncReport) -> None:
        item = to_incoming(chat_id, title, m)
        if item is not None:
            report.count(await self.ingest.ingest(item))

    def register_handlers(self) -> None:
        chats = list(self.settings.source_ids)
        self.client.add_event_handler(self.handle_event, events.NewMessage(chats=chats))
        self.client.add_event_handler(
            self.handle_event, events.MessageEdited(chats=chats)
        )

    async def handle_event(self, event) -> None:
        try:
            chat = await event.get_chat()
            title = get_display_name(chat) or str(event.chat_id)
            item = to_incoming(event.chat_id, title, event.message)
            if item is not None:
                await self.ingest.ingest(item)
                if not event.message.edit_date:
                    await self.ingest.advance_cursor(
                        Source.TELEGRAM, str(event.chat_id), event.message.id
                    )
        except Exception:
            log.exception("live telegram event failed")
```

Note: the edit recheck uses `offset_date=since, reverse=True` — the fake asserts on the first call only; the recheck is the second call. If the catch-up already ingested a message in the same run, its recheck ingest returns SAME (cheap; no download).

Wire into `Container.build` when `with_sources`:

```python
if with_sources:
    tg = TelegramClientManager(settings.telegram)
    telegram = TelegramSource(tg.client, ingest, settings.telegram)
    c.tg = tg
    c.jobs["telegram"] = telegram
```

and `start()`:

```python
async def start(self) -> None:
    if self.tg is not None:
        await self.tg.start()
        self.jobs["telegram"].register_handlers()


async def stop(self) -> None:
    if self.tg is not None:
        await self.tg.stop()
    await self.http.aclose()
    await self.engine.dispose()
```

- [ ] **Step 7: Run, expect PASS** — `uv run pytest tests/sources/telegram -v`.
- [ ] **Step 8: Commit** — `feat(telegram): mapping, catch-up sync, edit recheck, live events`.

---

### Task 11: СДО HTML parsers

**Files:**
- Create: `sources/sdo/__init__.py`, `sources/sdo/dates.py`, `sources/sdo/html.py`, `tests/sources/sdo/__init__.py`, `tests/sources/sdo/test_dates.py`, `tests/sources/sdo/test_html.py`, `tests/fixtures/sdo/{course_3058.html,course_3430.html,assign_90675.html,assign_92447.html,folder.html,url.html,login.html}`

**Interfaces:**
- Produces:
  - `parse_ru_datetime(text: str, tz: ZoneInfo) -> datetime | None` (UTC result) — handles `"четверг, 10 сентября 2026, 00:00"` and `"7 сентября 2026, 09:00"`.
  - `RemoteFile(url: str, name: str, uploaded_at: datetime | None)`
  - `SectionRef(number: int, name: str, summary: str)`
  - `ModuleRef(cmid: int, kind: str, name: str, url: str | None, section_number: int, section_name: str, description: str, available_from: datetime | None, due_at: datetime | None)`
  - `CoursePage(sections: list[SectionRef], modules: list[ModuleRef])`
  - `AssignPage(intro: str, files: list[RemoteFile], status: dict[str, str], feedback: dict[str, str], available_from, due_at)`
  - `html_to_text(fragment: str | None) -> str`, `file_name_from_url(url: str) -> str`
  - `parse_course(html, tz) -> CoursePage`, `parse_assign(html, tz) -> AssignPage`, `parse_folder(html) -> list[RemoteFile]`, `parse_url_module(html) -> str | None`, `parse_logintoken(html) -> str | None`, `parse_sesskey(html) -> str | None`, `is_login_page(html) -> bool`.

- [ ] **Step 1: Capture fixtures from the live СДО**

The user is logged in inside the Claude browser pane. For each page run in `javascript_tool`:

```js
const h = await (await fetch('/course/view.php?id=3058')).text();
h.replace(/<header[\s\S]*?<\/header>/, '').replace(/Максим[^<]*/g, 'Студент')
```

save the returned string to `tests/fixtures/sdo/course_3058.html` (Write tool). Same for `/course/view.php?id=3430`, `/mod/assign/view.php?id=90675` (intro files + submitted), `/mod/assign/view.php?id=92447` (dates), one `/mod/folder/view.php?id=<first folder cmid in course 2309>`, `/mod/url/view.php?id=81070`, and `fetch('/login/index.php', {credentials: 'omit'})` → `login.html`. Before saving, scrub the student's full name, email and avatar URLs (search the HTML for the name shown in the user menu). Do not save cookies or `sesskey` values: replace `"sesskey":"<value>"` with `"sesskey":"TESTSESSKEY"`.

- [ ] **Step 2: Failing tests**

```python
# tests/sources/sdo/test_dates.py
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from university_pz_parser.sources.sdo.dates import parse_ru_datetime

MSK = ZoneInfo("Europe/Moscow")


def test_parse_variants():
    assert parse_ru_datetime("четверг, 22 октября 2026, 00:00", MSK) == datetime(
        2026, 10, 21, 21, 0, tzinfo=UTC
    )
    assert parse_ru_datetime("7 сентября 2026, 09:00", MSK) == datetime(
        2026, 9, 7, 6, 0, tzinfo=UTC
    )
    assert parse_ru_datetime("1 мая 2026 г., 9:05", MSK) == datetime(
        2026, 5, 1, 6, 5, tzinfo=UTC
    )
    assert parse_ru_datetime("скоро", MSK) is None
```

```python
# tests/sources/sdo/test_html.py
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from university_pz_parser.sources.sdo import html

FX = Path(__file__).parents[2] / "fixtures" / "sdo"
MSK = ZoneInfo("Europe/Moscow")


def read(name):
    return (FX / name).read_text(encoding="utf-8")


def test_course_page():
    page = html.parse_course(read("course_3058.html"), MSK)
    by_id = {m.cmid: m for m in page.modules}
    project = by_id[90905]
    assert project.kind == "assign" and project.name == "Проект"
    assert project.section_number == 0
    assert project.available_from == datetime(2026, 9, 9, 21, 0, tzinfo=UTC)
    assert project.due_at == datetime(2026, 9, 16, 21, 0, tzinfo=UTC)
    assert by_id[90675].section_name == "5 семестр"
    assert all(m.cmid for m in page.modules)
    assert [s.number for s in page.sections][:2] == [0, 1]


def test_assign_page_files_and_status():
    page = html.parse_assign(read("assign_90675.html"), MSK)
    assert [f.name for f in page.files] == ["1_oop.pdf", "Симулятор фермера.docx"]
    assert all("introattachment" in f.url for f in page.files)
    assert page.files[0].uploaded_at == datetime(2026, 9, 7, 6, 0, tzinfo=UTC)
    assert page.status["Состояние ответа на задание"] == "Отправлено для оценивания"
    assert page.status["Состояние оценивания"] == "Не оценено"


def test_assign_dates():
    page = html.parse_assign(read("assign_92447.html"), MSK)
    assert page.due_at == datetime(2026, 10, 21, 21, 0, tzinfo=UTC)
    assert [f.name for f in page.files] == ["90-django-comments.pdf"]


def test_folder_and_url_and_login():
    files = html.parse_folder(read("folder.html"))
    assert files and all(f.name.endswith(".ipynb") for f in files[:3])
    assert html.parse_url_module(read("url.html")).startswith("http")
    assert html.parse_logintoken(read("login.html"))
    assert html.is_login_page(read("login.html"))
    assert not html.is_login_page(read("course_3058.html"))


def test_garbage_does_not_crash():
    assert (
        html.parse_course(
            "<html><body><li class='activity'></li></body></html>", MSK
        ).modules
        == []
    )
    assert html.parse_assign("<html></html>", MSK).files == []
```

After saving the fixture, add one assertion pinning a «Журнал …» url module (it has no link on the course page): `assert by_id[<its data-id from the fixture>].url is None`.

- [ ] **Step 3: Run, expect FAIL.**

- [ ] **Step 4: Implement**

```python
# src/university_pz_parser/sources/sdo/dates.py
import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
PATTERN = re.compile(
    r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})(?:\s*г\.)?,?\s+(\d{1,2}):(\d{2})", re.I
)


def parse_ru_datetime(text: str, tz: ZoneInfo) -> datetime | None:
    match = PATTERN.search(text or "")
    if not match:
        return None
    day, month_name, year, hour, minute = match.groups()
    month = MONTHS.get(month_name.lower())
    if month is None:
        return None
    local = datetime(int(year), month, int(day), int(hour), int(minute), tzinfo=tz)
    return local.astimezone(UTC)
```

```python
# src/university_pz_parser/sources/sdo/html.py
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser, Node

from university_pz_parser.sources.sdo.dates import parse_ru_datetime

START_LABELS = ("открыто с", "открывается", "доступно с")
DUE_LABELS = ("срок сдачи", "закрывается", "закрыто", "последний срок")


@dataclass(frozen=True)
class RemoteFile:
    url: str
    name: str
    uploaded_at: datetime | None


@dataclass(frozen=True)
class SectionRef:
    number: int
    name: str
    summary: str


@dataclass(frozen=True)
class ModuleRef:
    cmid: int
    kind: str
    name: str
    url: str | None
    section_number: int
    section_name: str
    description: str
    available_from: datetime | None
    due_at: datetime | None


@dataclass(frozen=True)
class CoursePage:
    sections: list[SectionRef]
    modules: list[ModuleRef]


@dataclass(frozen=True)
class AssignPage:
    intro: str
    files: list[RemoteFile]
    status: dict[str, str]
    feedback: dict[str, str]
    available_from: datetime | None
    due_at: datetime | None


def _text(node: Node | None) -> str:
    if node is None:
        return ""
    return " ".join(node.text(separator=" ").split())


def html_to_text(fragment: str | None) -> str:
    if not fragment:
        return ""
    return _text(HTMLParser(fragment).body)


def file_name_from_url(url: str) -> str:
    return unquote(url.rsplit("/", 1)[-1].split("?")[0])


def _dates(node: Node | None, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    start = due = None
    if node is None:
        return start, due
    for row in node.css("div"):
        label = _text(row.css_first("strong")).lower()
        if not label:
            continue
        value = parse_ru_datetime(_text(row), tz)
        if label.startswith(START_LABELS):
            start = value
        elif label.startswith(DUE_LABELS):
            due = value
    return start, due


def parse_course(html: str, tz: ZoneInfo) -> CoursePage:
    tree = HTMLParser(html)
    sections: list[SectionRef] = []
    modules: list[ModuleRef] = []
    for section in tree.css("li.section[data-number]"):
        number = int(section.attributes.get("data-number") or 0)
        name = section.attributes.get("data-sectionname") or _text(section.css_first(".sectionname"))
        summary = _text(section.css_first(".summarytext"))
        sections.append(SectionRef(number, name, summary))
        for act in section.css("li.activity[data-id]"):
            module = _module(act, number, name, tz)
            if module is not None:
                modules.append(module)
    return CoursePage(sections, modules)


def _module(act: Node, number: int, section_name: str, tz: ZoneInfo) -> ModuleRef | None:
    classes = (act.attributes.get("class") or "").split()
    kind = next((c.removeprefix("modtype_") for c in classes if c.startswith("modtype_")), None)
    raw_id = act.attributes.get("data-id")
    if kind is None or not raw_id or not raw_id.isdigit():
        return None
    name_node = act.css_first(".instancename")
    if name_node is not None:
        for hidden in name_node.css(".accesshide"):
            hidden.decompose()
    card = act.css_first("[data-activityname]")
    name = _text(name_node) or (card.attributes.get("data-activityname") if card else "") or 
    link = act.css_first(".activityname a, a.aalink")
    description = _text(act.css_first(".activity-altcontent, .contentafterlink, .description, .contentwithoutlink"))
    start, due = _dates(act.css_first('[data-region="activity-dates"]'), tz)
    return ModuleRef(
        cmid=int(raw_id),
        kind=kind,
        name=name,
        url=link.attributes.get("href") if link else None,
        section_number=number,
        section_name=section_name,
        description=description,
        available_from=start,
        due_at=due,
    )


def _intro_files(container: Node | None, tz: ZoneInfo) -> list[RemoteFile]:
    files: list[RemoteFile] = []
    if container is None:
        return files
    for block in container.css("div.fileuploadsubmission"):
        link = block.css_first('a[href*="pluginfile.php"]')
        if link is None:
            continue
        time_node = block.next
        while time_node is not None and time_node.tag != "div":
            time_node = time_node.next
        uploaded = parse_ru_datetime(_text(time_node), tz) if time_node is not None else None
        files.append(RemoteFile(link.attributes["href"], _text(link), uploaded))
    return files


def _table(node: Node | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if node is None:
        return result
    for row in node.css("tr"):
        th, td = row.css_first("th"), row.css_first("td")
        if th is not None and td is not None:
            result[_text(th)] = _text(td)
    return result


def parse_assign(html: str, tz: ZoneInfo) -> AssignPage:
    tree = HTMLParser(html)
    desc = tree.css_first(".activity-description")
    files = _intro_files(desc, tz)
    if desc is not None:
        for tree_node in desc.css('[id^="assign_files_tree"]'):
            tree_node.decompose()
    start, due = _dates(tree.css_first('[data-region="activity-dates"]'), tz)
    return AssignPage(
        intro=_text(desc),
        files=files,
        status=_table(tree.css_first(".submissionstatustable")),
        feedback=_table(tree.css_first(".feedback")),
        available_from=start,
        due_at=due,
    )


def parse_folder(html: str) -> list[RemoteFile]:
    tree = HTMLParser(html)
    root = tree.css_first('[role="main"]') or tree.body
    seen: dict[str, RemoteFile] = {}
    for link in (root.css('a[href*="pluginfile.php"]') if root else []):
        href = link.attributes["href"]
        name = _text(link) or file_name_from_url(href)
        seen.setdefault(href, RemoteFile(href, name, None))
    return list(seen.values())


def parse_url_module(html: str) -> str | None:
    tree = HTMLParser(html)
    link = tree.css_first(".urlworkaround a, [role='main'] a[href^='http']")
    return link.attributes.get("href") if link else None


def parse_logintoken(html: str) -> str | None:
    node = HTMLParser(html).css_first('input[name="logintoken"]')
    return node.attributes.get("value") if node else None


def parse_sesskey(html: str) -> str | None:
    match = re.search(r'"sesskey":"([^"]+)"', html)
    return match.group(1) if match else None


def is_login_page(html: str) -> bool:
    return HTMLParser(html).css_first('form input[name="logintoken"]') is not None
```

Verify `_intro_files` against the fixture: the time div is the sibling *after* `div.fileuploadsubmission` inside the same parent `<div>`. If `block.next` walks text nodes, the loop handles it.

- [ ] **Step 5: Run, expect PASS**; fix selectors only against what the fixtures show.
- [ ] **Step 6: Commit** — `feat(sdo): html parsers with fixtures`.

---

### Task 12: СДО HTTP client and source

**Files:**
- Create: `sources/sdo/client.py`, `sources/sdo/source.py`, `tests/sources/sdo/test_client.py`, `tests/sources/sdo/test_source.py`
- Modify: `container.py`

**Interfaces:**
- Consumes: Task 11 parsers; `IngestService`; `SyncReport`; `SdoSettings`.
- Produces:
  - `SdoAuthError(RuntimeError)`; `SdoHttp(http: httpx.AsyncClient, settings: SdoSettings)` with `async login()`, `async page(path: str) -> str` (re-logins once when it lands on the login page), `async ajax(method: str, args: dict) -> Any`, `async head_remote_id(url: str) -> str`, `fetcher(url: str, name: str) -> FileFetcher`, `async resolve_redirect(path: str) -> str` (final URL after redirects).
  - `SdoSource(sdo: SdoHttp, ingest: IngestService, settings: SdoSettings, tz: ZoneInfo)` with `name = "sdo"`, `async sync() -> SyncReport`, and pure helper `build_items(course, page, details) -> list[IncomingItem]` used by tests.
  - Hash split for assign: `hashed_extra = {"grading": status.get("Состояние оценивания"), "grade": feedback.get("Оценка"), "feedback": feedback.get("Отзыв в виде комментария")}`; `extra = {"submission": status, "section": name, "cmid": cmid}`.

- [ ] **Step 1: Failing client test**

```python
# tests/sources/sdo/test_client.py
from pathlib import Path

import httpx
import respx

from university_pz_parser.settings import SdoSettings
from university_pz_parser.sources.sdo.client import SdoHttp

FX = Path(__file__).parents[2] / "fixtures" / "sdo"
BASE = "https://sdo.test"


@respx.mock
async def test_login_and_relogin_and_head():
    login_html = (FX / "login.html").read_text()
    course_html = (FX / "course_3058.html").read_text()
    respx.get(f"{BASE}/login/index.php").respond(text=login_html)
    post = respx.post(f"{BASE}/login/index.php").respond(
        status_code=303, headers={"Location": f"{BASE}/my/"}
    )
    respx.get(f"{BASE}/my/").respond(text='<script>M.cfg = {"sesskey":"SK"}</script>')
    course = respx.get(f"{BASE}/course/view.php", params={"id": "1"})
    course.side_effect = [
        httpx.Response(200, text=login_html),
        httpx.Response(200, text=course_html),
    ]
    respx.head(f"{BASE}/pluginfile.php/1/a.pdf").respond(headers={"ETag": '"abc"'})
    async with httpx.AsyncClient(follow_redirects=True) as http:
        sdo = SdoHttp(http, SdoSettings(url=BASE, username="u", password="p"))
        await sdo.login()
        assert sdo.sesskey == "SK"
        assert b"username=u" in post.calls[0].request.content
        assert "data-number" in await sdo.page("/course/view.php?id=1")
        assert post.call_count == 2
        assert await sdo.head_remote_id(f"{BASE}/pluginfile.php/1/a.pdf") == "etag:abc"
```

- [ ] **Step 2: Implement client**

```python
# src/university_pz_parser/sources/sdo/client.py
import logging
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin

import httpx

from university_pz_parser.domain.dto import FileFetcher
from university_pz_parser.settings import SdoSettings
from university_pz_parser.sources.sdo import html

log = logging.getLogger(__name__)


class SdoAuthError(RuntimeError):
    pass


class SdoHttp:
    def __init__(self, http: httpx.AsyncClient, settings: SdoSettings):
        self.http = http
        self.settings = settings
        self.base = settings.url.rstrip("/")
        self.sesskey: str | None = None

    def url(self, path: str) -> str:
        return urljoin(self.base + "/", path.lstrip("/"))

    async def login(self) -> None:
        form = await self.http.get(self.url("/login/index.php"))
        token = html.parse_logintoken(form.text)
        resp = await self.http.post(
            self.url("/login/index.php"),
            data={
                "username": self.settings.username,
                "password": self.settings.password.get_secret_value(),
                "logintoken": token or "",
                "anchor": "",
            },
        )
        if html.is_login_page(resp.text):
            raise SdoAuthError("СДО login failed")
        self.sesskey = html.parse_sesskey(resp.text)
        if self.sesskey is None:
            self.sesskey = html.parse_sesskey(
                (await self.http.get(self.url("/my/"))).text
            )
        if self.sesskey is None:
            raise SdoAuthError("sesskey not found after login")

    async def page(self, path: str) -> str:
        resp = await self.http.get(self.url(path))
        if html.is_login_page(resp.text):
            await self.login()
            resp = await self.http.get(self.url(path))
            if html.is_login_page(resp.text):
                raise SdoAuthError(f"still on login page for {path}")
        resp.raise_for_status()
        return resp.text

    async def ajax(self, method: str, args: dict) -> Any:
        if self.sesskey is None:
            await self.login()
        resp = await self.http.post(
            self.url("/lib/ajax/service.php"),
            params={"sesskey": self.sesskey, "info": method},
            json=[{"index": 0, "methodname": method, "args": args}],
        )
        resp.raise_for_status()
        result = resp.json()[0]
        if result.get("error"):
            raise RuntimeError(f"{method}: {result.get('exception')}")
        return result["data"]

    async def resolve_redirect(self, path: str) -> str:
        async with self.http.stream("GET", self.url(path)) as resp:
            return str(resp.url)

    async def head_remote_id(self, url: str) -> str:
        try:
            resp = await self.http.head(url)
            resp.raise_for_status()
        except httpx.HTTPError:
            return f"url:{url}"
        etag = resp.headers.get("etag")
        if etag:
            return "etag:" + etag.removeprefix("W/").strip('"')
        if modified := resp.headers.get("last-modified"):
            return f"modified:{url}@{modified}"
        return f"url:{url}"

    def fetcher(self, url: str, name: str) -> FileFetcher:
        async def fetch(tmp: Path) -> Path:
            target = tmp / (name or unquote(url.rsplit("/", 1)[-1].split("?")[0]))
            async with self.http.stream("GET", url) as resp:
                resp.raise_for_status()
                with target.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
            return target

        return fetch
```

Note: the re-login test calls `login()` twice (initial + re-login), hence `post.call_count == 2`.

- [ ] **Step 3: Run client test, expect PASS.**

- [ ] **Step 4: Failing source test (pure mapping)**

```python
# tests/sources/sdo/test_source.py
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from university_pz_parser.domain.dto import IncomingFile
from university_pz_parser.domain.hashing import content_hash
from university_pz_parser.sources.sdo.html import (
    AssignPage,
    CoursePage,
    ModuleRef,
    SectionRef,
)
from university_pz_parser.sources.sdo.source import Course, ModuleDetails, build_items

MSK = ZoneInfo("Europe/Moscow")
COURSE = Course(id=3430, name="Моделирование информационных систем", summary="09.03.02")


def module(**kw):
    base = dict(
        cmid=90815,
        kind="assign",
        name="Практическое задание №1",
        url="u",
        section_number=2,
        section_name="Практическое задание №1",
        description="",
        available_from=None,
        due_at=None,
    )
    base.update(kw)
    return ModuleRef(**base)


def details(status="Нет ответа", grading="Не оценено", files=()):
    return ModuleDetails(
        text="Сделать модель",
        files=tuple(files),
        status={"Состояние ответа на задание": status, "Состояние оценивания": grading},
        feedback={},
        available_from=None,
        due_at=datetime(2026, 10, 1, tzinfo=UTC),
        uploaded=[datetime(2026, 9, 9, 10, 42, tzinfo=UTC)],
    )


def test_assign_item_and_section_item():
    page = CoursePage(
        [SectionRef(0, "О дисциплине", "Курс про UML"), SectionRef(1, "Пустая", "")],
        [module()],
    )
    items = build_items(COURSE, page, {90815: details()})
    keys = [i.external_key for i in items]
    assert keys == ["section:3430:0", "cm:90815"]
    assign = items[1]
    assert assign.title == "Практическое задание №1"
    assert assign.published_at == datetime(2026, 9, 9, 10, 42, tzinfo=UTC)
    assert (
        assign.channel_title == COURSE.name and assign.channel_description == "09.03.02"
    )
    assert assign.url == "u"


def test_own_submission_not_hashed_but_grading_is():
    page = CoursePage([], [module()])
    a = build_items(COURSE, page, {90815: details(status="Нет ответа")})[0]
    b = build_items(COURSE, page, {90815: details(status="Отправлено")})[0]
    c = build_items(COURSE, page, {90815: details(grading="Оценено")})[0]
    assert content_hash(a, []) == content_hash(b, [])
    assert content_hash(a, []) != content_hash(c, [])


def test_scorm_has_no_files():
    page = CoursePage([], [module(cmid=1, kind="scorm", name="Тест 1")])
    item = build_items(COURSE, page, {})[0]
    assert item.kind == "scorm" and item.files == () and item.text == ""
```

- [ ] **Step 5: Implement source**

```python
# src/university_pz_parser/sources/sdo/source.py
import logging
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.enums import Source
from university_pz_parser.services.ingest import IngestService
from university_pz_parser.services.jobs import SyncReport
from university_pz_parser.settings import SdoSettings
from university_pz_parser.sources.sdo import html
from university_pz_parser.sources.sdo.client import SdoHttp

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Course:
    id: int
    name: str
    summary: str | None


@dataclass(frozen=True)
class ModuleDetails:
    text: str = ""
    files: tuple[IncomingFile, ...] = ()
    status: dict[str, str] = field(default_factory=dict)
    feedback: dict[str, str] = field(default_factory=dict)
    available_from: datetime | None = None
    due_at: datetime | None = None
    uploaded: list[datetime] = field(default_factory=list)


def build_items(
    course: Course, page: html.CoursePage, details: dict[int, ModuleDetails]
) -> list[IncomingItem]:
    common = dict(
        source=Source.SDO,
        channel_external_id=str(course.id),
        channel_title=course.name,
        channel_description=course.summary,
    )
    items: list[IncomingItem] = []
    for section in page.sections:
        if section.summary:
            items.append(
                IncomingItem(
                    external_key=f"section:{course.id}:{section.number}",
                    kind="section",
                    title=section.name,
                    text=section.summary,
                    published_at=None,
                    extra={"section": section.name},
                    **common,
                )
            )
    for m in page.modules:
        d = details.get(m.cmid, ModuleDetails())
        available_from = d.available_from or m.available_from
        due_at = d.due_at or m.due_at
        dates = [x for x in [*d.uploaded, available_from] if x is not None]
        text = "\n\n".join(filter(None, [m.description, d.text]))
        hashed = {}
        if m.kind == "assign":
            hashed = {
                "grading": d.status.get("Состояние оценивания"),
                "grade": d.feedback.get("Оценка"),
                "feedback": d.feedback.get("Отзыв в виде комментария"),
            }
        items.append(
            IncomingItem(
                external_key=f"cm:{m.cmid}",
                kind=m.kind,
                title=m.name,
                text=text,
                url=m.url,
                published_at=min(dates) if dates else None,
                available_from=available_from,
                due_at=due_at,
                files=d.files,
                hashed_extra=hashed,
                extra={
                    "section": m.section_name,
                    "cmid": m.cmid,
                    "submission": d.status.get("Состояние ответа на задание"),
                },
                **common,
            )
        )
    return items


class SdoSource:
    name = "sdo"

    def __init__(
        self, sdo: SdoHttp, ingest: IngestService, settings: SdoSettings, tz: ZoneInfo
    ):
        self.sdo = sdo
        self.ingest = ingest
        self.settings = settings
        self.tz = tz

    async def sync(self) -> SyncReport:
        report = SyncReport(self.name)
        await self.sdo.login()
        for course in await self.courses():
            try:
                page = html.parse_course(
                    await self.sdo.page(f"/course/view.php?id={course.id}"), self.tz
                )
                details = {m.cmid: await self._details(m) for m in page.modules}
                for item in build_items(course, page, details):
                    report.count(await self.ingest.ingest(item))
            except Exception as exc:
                log.exception("sdo course %s failed", course.id)
                report.warnings.append(f"course {course.id}: {exc!r}")
        return report

    async def courses(self) -> list[Course]:
        data = await self.sdo.ajax(
            "core_course_get_enrolled_courses_by_timeline_classification",
            {"classification": "all", "limit": 0, "offset": 0, "sort": "fullname"},
        )
        excluded = set(self.settings.exclude_course_ids)
        return [
            Course(c["id"], c["fullname"], html.html_to_text(c.get("summary")) or None)
            for c in data["courses"]
            if c["id"] not in excluded
        ]

    async def _details(self, m: html.ModuleRef) -> ModuleDetails:
        try:
            if m.kind == "assign" and m.url:
                page = html.parse_assign(await self.sdo.page(m.url), self.tz)
                return ModuleDetails(
                    text=page.intro,
                    files=tuple([await self._file(f.url, f.name) for f in page.files]),
                    status=page.status,
                    feedback=page.feedback,
                    available_from=page.available_from,
                    due_at=page.due_at,
                    uploaded=[f.uploaded_at for f in page.files if f.uploaded_at],
                )
            if m.kind == "resource" and m.url:
                final = await self.sdo.resolve_redirect(m.url)
                if "pluginfile.php" in final:
                    return ModuleDetails(
                        files=(await self._file(final, html.file_name_from_url(final)),)
                    )
                return ModuleDetails(
                    files=tuple(
                        [
                            await self._file(f.url, f.name)
                            for f in html.parse_folder(await self.sdo.page(m.url))
                        ]
                    )
                )
            if m.kind == "folder" and m.url:
                found = html.parse_folder(await self.sdo.page(m.url))
                return ModuleDetails(
                    files=tuple([await self._file(f.url, f.name) for f in found])
                )
            if m.kind == "url" and m.url:
                target = html.parse_url_module(await self.sdo.page(m.url))
                return ModuleDetails(text=target or "")
        except Exception:
            log.warning("sdo module %s details failed", m.cmid, exc_info=True)
        return ModuleDetails()

    async def _file(self, url: str, name: str) -> IncomingFile:
        return IncomingFile(
            remote_id=await self.sdo.head_remote_id(url),
            name=name,
            url=url,
            fetch=self.sdo.fetcher(url, name),
        )
```

Note on URL modules: putting the target URL into `text` lets the Yandex Disk resolver pick it up through `IngestService._collect_files`.

Wire into `Container.build` when `with_sources`:

```python
sdo_http = httpx.AsyncClient(timeout=120, follow_redirects=True)
c.jobs["sdo"] = SdoSource(
    SdoHttp(sdo_http, settings.sdo), ingest, settings.sdo, ZoneInfo(settings.tz)
)
c.extra_clients.append(sdo_http)
```

Add `extra_clients: list[httpx.AsyncClient] = field(default_factory=list)` to `Container` and close them in `stop()`. A separate client keeps СДО cookies isolated from the Yandex Disk client.

- [ ] **Step 6: Run, expect PASS** — `uv run pytest tests/sources/sdo -v`.
- [ ] **Step 7: Live smoke run** (needs `.env` with real `SDO__*`; user fills it):

```bash
uv run python -c "
import asyncio, httpx
from zoneinfo import ZoneInfo
from university_pz_parser.settings import get_settings
from university_pz_parser.sources.sdo.client import SdoHttp
from university_pz_parser.sources.sdo.source import SdoSource
async def main():
    s = get_settings()
    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as h:
        src = SdoSource(SdoHttp(h, s.sdo), None, s.sdo, ZoneInfo(s.tz))
        await src.sdo.login()
        print(await src.courses())
asyncio.run(main())"
```

Expected: the 5 courses (minus excluded). Do not print or log the password.

- [ ] **Step 8: Commit** — `feat(sdo): login, course crawl, module details`.

---

### Task 13: СДО mobile REST backend (preferred when token is issued)

**Files:**
- Create: `sources/sdo/rest.py`, `tests/sources/sdo/test_rest.py`
- Modify: `sources/sdo/source.py` (try REST first)

**Interfaces:**
- Produces: `SdoRest(http, settings)` with `async token() -> str | None` (`POST /login/token.php` with `service=moodle_mobile_app`; returns None on `{"error": ...}`), `async call(fn: str, **params) -> Any` (`POST /webservice/rest/server.php` with `wstoken`, `wsfunction`, `moodlewsrestformat=json`; params flattened Moodle-style `courseids[0]=1`), `async course_pages(course_id) -> tuple[html.CoursePage, dict[int, ModuleDetails]]` built from `core_course_get_contents` + `mod_assign_get_assignments` + `mod_assign_get_submission_status`.
- `SdoSource.sync()` → if `await rest.token()` succeeds use REST for courses (`core_enrol_get_users_courses` with `userid` from `core_webservice_get_site_info`) and pages; else HTML path. Report which in `report.warnings` (`"sdo backend: rest"` / `"sdo backend: html"`).
- REST file `remote_id` = `f"rest:{fileurl}@{timemodified}:{filesize}"`; downloads use `fileurl + ("&" if "?" in fileurl else "?") + "token=" + token`.

- [ ] **Step 1: Check whether the token is issued** — with the user's `.env`:

```bash
uv run python -c "
import asyncio, httpx
from university_pz_parser.settings import get_settings
async def main():
    s = get_settings().sdo
    async with httpx.AsyncClient() as h:
        r = await h.post(s.url + '/login/token.php', data={'username': s.username, 'password': s.password.get_secret_value(), 'service': 'moodle_mobile_app'})
        print({k: ('***' if k in ('token','privatetoken') else v) for k, v in r.json().items()})
asyncio.run(main())"
```

If the output has `error`, **stop this task**: mark it skipped in the plan, keep the HTML backend, and tell the user. Otherwise continue.

- [ ] **Step 2: Failing test** (respx, fake responses shaped like Moodle 4.5):

```python
# tests/sources/sdo/test_rest.py
import httpx
import respx

from university_pz_parser.settings import SdoSettings
from university_pz_parser.sources.sdo.rest import SdoRest

BASE = "https://sdo.test"


@respx.mock
async def test_contents_to_pages():
    respx.post(f"{BASE}/login/token.php").respond(json={"token": "T"})

    def server(request):
        fn = dict(httpx.QueryParams(request.content.decode()))["wsfunction"]
        return httpx.Response(
            200,
            json={
                "core_course_get_contents": [
                    {
                        "section": 0,
                        "name": "Общее",
                        "summary": "<p>Про курс</p>",
                        "modules": [
                            {
                                "id": 90905,
                                "modname": "assign",
                                "name": "Проект",
                                "url": f"{BASE}/mod/assign/view.php?id=90905",
                                "instance": 7,
                                "description": "",
                                "dates": [
                                    {"label": "Срок сдачи:", "timestamp": 1789592400}
                                ],
                            },
                            {
                                "id": 92422,
                                "modname": "resource",
                                "name": "Лекция 1",
                                "url": "u",
                                "contents": [
                                    {
                                        "type": "file",
                                        "filename": "l1.pdf",
                                        "fileurl": f"{BASE}/webservice/pluginfile.php/1/l1.pdf?forcedownload=1",
                                        "timemodified": 1789000000,
                                        "filesize": 10,
                                        "mimetype": "application/pdf",
                                    }
                                ],
                            },
                        ],
                    },
                ],
                "mod_assign_get_assignments": {
                    "courses": [
                        {
                            "id": 3058,
                            "assignments": [
                                {
                                    "id": 7,
                                    "cmid": 90905,
                                    "intro": "<p>Сделать проект</p>",
                                    "duedate": 1789592400,
                                    "allowsubmissionsfromdate": 1789506000,
                                    "introattachments": [
                                        {
                                            "filename": "p.docx",
                                            "fileurl": f"{BASE}/webservice/pluginfile.php/2/p.docx",
                                            "timemodified": 1789000001,
                                            "filesize": 5,
                                            "mimetype": "x",
                                        }
                                    ],
                                }
                            ],
                        }
                    ]
                },
                "mod_assign_get_submission_status": {
                    "lastattempt": {"submission": {"status": "submitted"}},
                    "feedback": {"gradefordisplay": "5", "plugins": []},
                },
            }[fn],
        )

    respx.post(f"{BASE}/webservice/rest/server.php").mock(side_effect=server)
    async with httpx.AsyncClient() as http:
        rest = SdoRest(http, SdoSettings(url=BASE, username="u", password="p"))
        assert await rest.token() == "T"
        page, details = await rest.course_pages(3058)
    assert page.sections[0].summary == "Про курс"
    assert [m.cmid for m in page.modules] == [90905, 92422]
    d = details[90905]
    assert d.text == "Сделать проект"
    assert (
        d.files[0].remote_id.startswith("rest:")
        and "token=T" not in d.files[0].remote_id
    )
    assert d.status["Состояние ответа на задание"] == "submitted"
    assert d.feedback["Оценка"] == "5"
    assert details[92422].files[0].name == "l1.pdf"
```

- [ ] **Step 3: Implement `rest.py`**

```python
# src/university_pz_parser/sources/sdo/rest.py
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import httpx

from university_pz_parser.domain.dto import IncomingFile
from university_pz_parser.settings import SdoSettings
from university_pz_parser.sources.sdo import html
from university_pz_parser.sources.sdo.client import SdoHttp


def _flatten(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, list):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]", v, out)
    else:
        out[prefix] = value


def _ts(value: int | None) -> datetime | None:
    return datetime.fromtimestamp(value, UTC) if value else None


class SdoRest:
    def __init__(self, http: httpx.AsyncClient, settings: SdoSettings):
        self.http = http
        self.settings = settings
        self.base = settings.url.rstrip("/")
        self._token: str | None = None

    async def token(self) -> str | None:
        resp = await self.http.post(
            f"{self.base}/login/token.php",
            data={
                "username": self.settings.username,
                "password": self.settings.password.get_secret_value(),
                "service": "moodle_mobile_app",
            },
        )
        self._token = resp.json().get("token") if resp.status_code == 200 else None
        return self._token

    async def call(self, fn: str, **params: Any) -> Any:
        data: dict[str, Any] = {
            "wstoken": self._token,
            "wsfunction": fn,
            "moodlewsrestformat": "json",
        }
        for key, value in params.items():
            _flatten(key, value, data)
        resp = await self.http.post(
            f"{self.base}/webservice/rest/server.php", data=data
        )
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("exception"):
            raise RuntimeError(f"{fn}: {body.get('errorcode')}")
        return body

    async def courses(self, excluded: list[int]):
        from university_pz_parser.sources.sdo.source import Course

        site = await self.call("core_webservice_get_site_info")
        rows = await self.call("core_enrol_get_users_courses", userid=site["userid"])
        return [
            Course(c["id"], c["fullname"], html.html_to_text(c.get("summary")) or None)
            for c in rows
            if c["id"] not in set(excluded)
        ]

    def _file(self, f: dict) -> IncomingFile:
        url = f["fileurl"]
        download = url + ("&" if "?" in url else "?") + f"token={self._token}"
        return IncomingFile(
            remote_id=f"rest:{url}@{f.get('timemodified')}:{f.get('filesize')}",
            name=f.get("filename"),
            mime=f.get("mimetype"),
            size=f.get("filesize"),
            url=url,
            fetch=SdoHttp(self.http, self.settings).fetcher(
                download, f.get("filename") or ""
            ),
        )

    async def course_pages(self, course_id: int):
        from university_pz_parser.sources.sdo.source import ModuleDetails

        contents = await self.call("core_course_get_contents", courseid=course_id)
        assigns = await self.call("mod_assign_get_assignments", courseids=[course_id])
        by_cmid = {
            a["cmid"]: a
            for c in assigns.get("courses", [])
            for a in c.get("assignments", [])
        }
        sections, modules, details = [], [], {}
        for sec in contents:
            sections.append(
                html.SectionRef(
                    sec["section"],
                    sec.get("name", ""),
                    html.html_to_text(sec.get("summary")),
                )
            )
            for m in sec.get("modules", []):
                dates = defaultdict(lambda: None)
                for d in m.get("dates", []):
                    label = d.get("label", "").lower()
                    if label.startswith(html.START_LABELS):
                        dates["start"] = _ts(d.get("timestamp"))
                    elif label.startswith(html.DUE_LABELS):
                        dates["due"] = _ts(d.get("timestamp"))
                modules.append(
                    html.ModuleRef(
                        cmid=m["id"],
                        kind=m["modname"],
                        name=m["name"],
                        url=m.get("url"),
                        section_number=sec["section"],
                        section_name=sec.get("name", ""),
                        description=html.html_to_text(m.get("description")),
                        available_from=dates["start"],
                        due_at=dates["due"],
                    )
                )
                files = [
                    self._file(f)
                    for f in m.get("contents", [])
                    if f.get("type") == "file"
                ]
                if m["modname"] == "url":
                    target = next(
                        (
                            f.get("fileurl")
                            for f in m.get("contents", [])
                            if f.get("type") == "url"
                        ),
                        "",
                    )
                    details[m["id"]] = ModuleDetails(text=target or "")
                    continue
                if m["modname"] != "assign" or m["id"] not in by_cmid:
                    details[m["id"]] = ModuleDetails(files=tuple(files))
                    continue
                a = by_cmid[m["id"]]
                attachments = a.get("introattachments", [])
                status = await self.call(
                    "mod_assign_get_submission_status", assignid=a["id"]
                )
                feedback = status.get("feedback") or {}
                comment = next(
                    (
                        p["editorfields"][0]["text"]
                        for p in feedback.get("plugins", [])
                        if p.get("type") == "comments" and p.get("editorfields")
                    ),
                    None,
                )
                details[m["id"]] = ModuleDetails(
                    text=html.html_to_text(a.get("intro")),
                    files=tuple(self._file(f) for f in attachments),
                    status={
                        "Состояние ответа на задание": (
                            (status.get("lastattempt") or {}).get("submission") or {}
                        ).get("status"),
                        "Состояние оценивания": (status.get("lastattempt") or {}).get(
                            "gradingstatus"
                        ),
                    },
                    feedback={
                        k: v
                        for k, v in {
                            "Оценка": feedback.get("gradefordisplay"),
                            "Отзыв в виде комментария": html.html_to_text(comment)
                            if comment
                            else None,
                        }.items()
                        if v
                    },
                    available_from=_ts(a.get("allowsubmissionsfromdate")),
                    due_at=_ts(a.get("duedate")),
                    uploaded=[
                        t
                        for t in (_ts(f.get("timemodified")) for f in attachments)
                        if t
                    ],
                )
        return html.CoursePage(sections, modules), details
```

In `SdoSource.__init__` add `rest: SdoRest | None = None` and in `sync()`:

```python
await self.sdo.login()
rest_token = await self.rest.token() if self.rest else None
if rest_token:
    report.warnings.append("sdo backend: rest")
    for course in await self.rest.courses(self.settings.exclude_course_ids):
        try:
            page, details = await self.rest.course_pages(course.id)
            for item in build_items(course, page, details):
                report.count(await self.ingest.ingest(item))
        except Exception as exc:
            log.exception("sdo rest course %s failed", course.id)
            report.warnings.append(f"course {course.id}: {exc!r}")
    return report
report.warnings.append("sdo backend: html")
```

Wire `SdoRest(sdo_http, settings.sdo)` into the container next to `SdoHttp`.

Important: switching backends changes `remote_id` formats (`etag:` vs `rest:`), which would mark every СДО Item Edited once. Persist the chosen backend in `SourceHealth.last_report` and log a warning when it flips; accept the one-time re-delivery.

- [ ] **Step 4: Run, expect PASS.** — **Step 5: Commit** `feat(sdo): mobile REST backend with HTML fallback`.

---

### Task 14: Scheduler, runtime lifespan, CLI, JSON-export import

**Files:**
- Create: `scheduler.py`, `runtime.py`, `__main__.py`, `cli/__init__.py`, `cli/import_export.py`, `tests/test_scheduler.py`, `tests/cli/__init__.py`, `tests/cli/test_import_export.py`, `tests/fixtures/tg_export.json`

**Interfaces:**
- Produces:
  - `build_scheduler(c: Container) -> AsyncIOScheduler` with job ids `telegram_hourly` (`CronTrigger(minute=5)`), `sdo` (`CronTrigger(hour="7-22/3", minute=20)`), all `max_instances=1, coalesce=True, misfire_grace_time=600`, timezone `settings.tz`; plus a one-shot `telegram_startup` run at start.
  - `run_server_lifespan(app)` async context manager: build container with sources, `await c.start()`, `app.state.container = c`, start scheduler, yield, shutdown.
  - CLI commands (`typer`): `login`, `list-chats`, `init-db`, `sync {telegram|sdo}`, `import-export FILE --chat-id ID`, `serve`.
  - `export_to_items(data: dict, chat_id: int) -> Iterator[IncomingItem]` — text only (files from exports are local paths; attach when `photo`/`file` path exists next to the JSON via a local `fetch` that copies).

- [ ] **Step 1: Failing tests**

```python
# tests/test_scheduler.py
from university_pz_parser.container import Container
from university_pz_parser.scheduler import build_scheduler


async def test_jobs_registered(settings, engine):
    c = await Container.build(settings, with_sources=False)
    c.jobs["telegram"] = object()
    c.jobs["sdo"] = object()
    sched = build_scheduler(c)
    assert {j.id for j in sched.get_jobs()} == {"telegram_hourly", "sdo"}
    await c.stop()
```

```python
# tests/cli/test_import_export.py
import json
from pathlib import Path

from university_pz_parser.cli.import_export import export_to_items

FX = Path(__file__).parents[1] / "fixtures" / "tg_export.json"


def test_export_mapping():
    data = json.loads(FX.read_text())
    items = list(export_to_items(data, -1002362006703, FX.parent))
    assert [i.external_key for i in items] == ["-1002362006703:10", "-1002362006703:11"]
    assert items[0].text == "Сдать ПЗ1 до пятницы https://disk.yandex.ru/d/AAA"
    assert items[0].published_at.isoformat() == "2026-09-02T07:15:00+00:00"
    assert items[1].author == "Храмов Д.Э."
```

`tests/fixtures/tg_export.json`:

```json
{"name": "ИСиТ", "type": "private_supergroup", "id": 2362006703, "messages": [
  {"id": 9, "type": "service", "date": "2026-09-01T10:00:00", "date_unixtime": "1788246000", "text": ""},
  {"id": 10, "type": "message", "date": "2026-09-02T10:15:00", "date_unixtime": "1788333300",
   "from": "Староста", "from_id": "user1",
   "text": ["Сдать ПЗ1 до пятницы ", {"type": "link", "text": "https://disk.yandex.ru/d/AAA"}],
   "text_entities": []},
  {"id": 11, "type": "message", "date": "2026-09-03T09:00:00", "date_unixtime": "1788415200",
   "from": "Храмов Д.Э.", "from_id": "user2", "text": "Лекция переносится", "edited_unixtime": "1788418800"}
]}
```

- [ ] **Step 2: Implement**

```python
# src/university_pz_parser/cli/import_export.py
import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.enums import Source
from university_pz_parser.sources.telegram.mapping import message_link


def _text(raw) -> str:
    if isinstance(raw, str):
        return raw
    return "".join(
        part if isinstance(part, str) else part.get("text", "") for part in raw
    )


def _ts(value: str | None) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


def _local_file(base: Path, rel: str, msg_id: int) -> IncomingFile | None:
    path = base / rel
    if not path.is_file():
        return None

    async def fetch(tmp: Path) -> Path:
        target = tmp / path.name
        shutil.copyfile(path, target)
        return target

    return IncomingFile(
        remote_id=f"export:{msg_id}:{rel}",
        name=path.name,
        size=path.stat().st_size,
        fetch=fetch,
    )


def export_to_items(data: dict, chat_id: int, base: Path) -> Iterator[IncomingItem]:
    title = data.get("name") or str(chat_id)
    for m in data.get("messages", []):
        if m.get("type") != "message":
            continue
        text = _text(m.get("text", ""))
        rel = m.get("file") or m.get("photo")
        f = _local_file(base, rel, m["id"]) if rel else None
        if not text and f is None:
            continue
        yield IncomingItem(
            source=Source.TELEGRAM,
            external_key=f"{chat_id}:{m['id']}",
            channel_external_id=str(chat_id),
            channel_title=title,
            kind="photo" if m.get("photo") else "document" if rel else "text",
            text=text,
            published_at=_ts(m.get("date_unixtime")),
            edited_at=_ts(m.get("edited_unixtime")),
            author=m.get("from"),
            url=message_link(chat_id, m["id"]),
            files=(f,) if f else (),
            extra={
                "msg_id": m["id"],
                "reply_to": m.get("reply_to_message_id"),
                "forwarded_from": m.get("forwarded_from"),
                "imported": True,
            },
        )
```

```python
# src/university_pz_parser/scheduler.py
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from university_pz_parser.container import Container


def build_scheduler(c: Container) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=c.settings.tz)
    common = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 600}
    if "telegram" in c.jobs:
        scheduler.add_job(
            c.runner.run,
            CronTrigger(minute=5),
            args=[c.jobs["telegram"]],
            id="telegram_hourly",
            **common,
        )
    if "sdo" in c.jobs:
        scheduler.add_job(
            c.runner.run,
            CronTrigger(hour="7-22/3", minute=20),
            args=[c.jobs["sdo"]],
            id="sdo",
            **common,
        )
    return scheduler
```

```python
# src/university_pz_parser/runtime.py
import asyncio
from contextlib import asynccontextmanager

from university_pz_parser.container import Container
from university_pz_parser.scheduler import build_scheduler
from university_pz_parser.settings import get_settings


@asynccontextmanager
async def run_server_lifespan(app):
    c = await Container.build(get_settings())
    await c.start()
    app.state.container = c
    scheduler = build_scheduler(c)
    scheduler.start()
    startup = [asyncio.create_task(c.runner.run(job)) for job in c.jobs.values()]
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        for task in startup:
            task.cancel()
        await c.stop()
```

```python
# src/university_pz_parser/__main__.py
import asyncio
import json
from pathlib import Path

import typer

from university_pz_parser.logging import configure_logging
from university_pz_parser.settings import get_settings

app = typer.Typer(no_args_is_help=True)


@app.command()
def login() -> None:
    from university_pz_parser.sources.telegram.client import TelegramClientManager

    async def run():
        tg = TelegramClientManager(get_settings().telegram)
        await tg.client.start()
        me = await tg.client.get_me()
        typer.echo(f"logged in as {me.first_name} ({me.id})")
        await tg.stop()

    asyncio.run(run())


@app.command("list-chats")
def list_chats() -> None:
    from university_pz_parser.sources.telegram.client import TelegramClientManager

    async def run():
        tg = TelegramClientManager(get_settings().telegram)
        await tg.start()
        async for d in tg.client.iter_dialogs():
            if d.is_group or d.is_channel:
                typer.echo(f"{d.id}\t{d.name}")
        await tg.stop()

    asyncio.run(run())


@app.command("init-db")
def init_db() -> None:
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config("alembic.ini"), "head")


@app.command()
def sync(source: str) -> None:
    from university_pz_parser.container import Container

    async def run():
        c = await Container.build(get_settings())
        try:
            if source == "telegram":
                await c.tg.start()
            report = await c.runner.run(c.jobs[source])
            typer.echo(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        finally:
            await c.stop()

    configure_logging()
    asyncio.run(run())


@app.command("import-export")
def import_export(
    file: Path, chat_id: int = typer.Option(..., help="marked id, e.g. -1002362006703")
) -> None:
    from university_pz_parser.cli.import_export import export_to_items
    from university_pz_parser.container import Container

    async def run():
        c = await Container.build(get_settings(), with_sources=False)
        counts: dict[str, int] = {}
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            for item in export_to_items(data, chat_id, file.parent):
                status = await c.ingest.ingest(item)
                counts[status] = counts.get(status, 0) + 1
        finally:
            await c.stop()
        typer.echo(counts)

    configure_logging()
    asyncio.run(run())


@app.command()
def serve() -> None:
    import uvicorn

    from university_pz_parser.api.app import create_app

    configure_logging()
    s = get_settings()
    uvicorn.run(
        create_app(), host=s.api.host, port=s.api.port, workers=1, log_config=None
    )


if __name__ == "__main__":
    app()
```

- [ ] **Step 3: Run all tests** — `uv run pytest -v`. Expect PASS.
- [ ] **Step 4: Manual smoke** (user present, real `.env`): `uv run university-pz-parser login` (user types phone/code — do not type them for the user), `uv run university-pz-parser list-chats`, put the ids into `.env`, `uv run university-pz-parser init-db`, `uv run university-pz-parser sync telegram`, `uv run university-pz-parser sync sdo`, then `uv run university-pz-parser serve` and

```bash
curl -s -H "Authorization: Bearer $API__TOKEN" "http://127.0.0.1:8000/items?from=2026-09-01T00:00:00%2B03:00&to=2026-10-05T00:00:00%2B03:00&limit=5"
```

- [ ] **Step 5: Commit** — `feat: scheduler, server lifespan, CLI, telegram export import`.

---

### Task 15: MCP adapter

**Files:**
- Create: `mcp_adapter/pyproject.toml`, `mcp_adapter/README.md`, `mcp_adapter/src/study_inbox_mcp/__init__.py`, `mcp_adapter/src/study_inbox_mcp/server.py`, `mcp_adapter/tests/test_server.py`

**Interfaces:**
- Consumes: HTTP contract from Task 8 only.
- Produces: console script `study-inbox-mcp`; tools `get_items(date_from: str, date_to: str, source: str | None = None, include_received: bool = False, limit: int = 200) -> dict`, `mark_received(ids: list[int]) -> dict`, `get_item(item_id: int) -> dict`, `get_file(sha256: str) -> str` (path under `INBOX_DOWNLOAD_DIR`, default `~/Downloads/study-inbox`), `health() -> list`. Env: `INBOX_URL`, `INBOX_TOKEN`.

- [ ] **Step 1: Package**

```toml
# mcp_adapter/pyproject.toml
[project]
name = "study-inbox-mcp"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["mcp>=2", "httpx>=0.27"]

[project.scripts]
study-inbox-mcp = "study_inbox_mcp.server:main"

[dependency-groups]
dev = ["pytest", "respx"]

[build-system]
requires = ["uv_build>=0.12.19,<0.13.0"]
build-backend = "uv_build"
```

- [ ] **Step 2: Failing test**

```python
# mcp_adapter/tests/test_server.py
import httpx
import respx

from study_inbox_mcp import server


@respx.mock
def test_tools_call_api(tmp_path, monkeypatch):
    monkeypatch.setenv("INBOX_DOWNLOAD_DIR", str(tmp_path))
    server.configure("https://inbox.test", "tok")
    items = respx.get("https://inbox.test/items").respond(
        json={"items": [], "count": 0}
    )
    respx.post("https://inbox.test/items/receive").respond(json={"updated": 2})
    respx.get("https://inbox.test/files/abc").respond(
        content=b"pdf", headers={"content-disposition": 'attachment; filename="l1.pdf"'}
    )
    assert (
        server.get_items("2026-10-01T00:00:00+03:00", "2026-10-02T00:00:00+03:00")[
            "count"
        ]
        == 0
    )
    assert items.calls[0].request.headers["Authorization"] == "Bearer tok"
    assert items.calls[0].request.url.params["from"] == "2026-10-01T00:00:00+03:00"
    assert server.mark_received([1, 2]) == {"updated": 2}
    path = server.get_file("abc")
    assert path.endswith("abc_l1.pdf") and open(path, "rb").read() == b"pdf"
```

- [ ] **Step 3: Implement**

```python
# mcp_adapter/src/study_inbox_mcp/server.py
import os
import re
from pathlib import Path

import httpx
from mcp.server import MCPServer

mcp = MCPServer("study-inbox")
_http: httpx.Client | None = None


def configure(base_url: str, token: str) -> None:
    global _http
    _http = httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=60
    )


def _client() -> httpx.Client:
    if _http is None:
        configure(os.environ["INBOX_URL"], os.environ["INBOX_TOKEN"])
    return _http


def _json(resp: httpx.Response):
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def get_items(
    date_from: str,
    date_to: str,
    source: str | None = None,
    include_received: bool = False,
    limit: int = 200,
) -> dict:
    """Учебные материалы (сообщения Telegram-чатов и элементы курсов СДО), опубликованные или изменённые в окне [date_from, date_to).

    date_from/date_to — ISO 8601 с часовым поясом, например 2026-10-01T00:00:00+03:00.
    source — "telegram" или "sdo". По умолчанию возвращаются только ещё не полученные (Received) элементы.
    У изменённых элементов edited=true, есть previous_text, removed_files и files[].added_on_edit.
    Тексты файлов обрезаны до 4000 символов (text_truncated) — полный текст через get_item.
    После обработки вызови mark_received с id обработанных элементов."""
    params = {
        "from": date_from,
        "to": date_to,
        "include_received": include_received,
        "limit": limit,
    }
    if source:
        params["source"] = source
    return _json(_client().get("/items", params=params))


@mcp.tool()
def mark_received(ids: list[int]) -> dict:
    """Пометить элементы как полученные, чтобы get_items их больше не возвращал (пока они снова не изменятся)."""
    return _json(_client().post("/items/receive", json={"ids": ids}))


@mcp.tool()
def get_item(item_id: int) -> dict:
    """Полный элемент с полными текстами всех файлов."""
    return _json(_client().get(f"/items/{item_id}"))


@mcp.tool()
def get_file(sha256: str) -> str:
    """Скачать исходный файл по sha256 на этот компьютер и вернуть путь к нему."""
    resp = _client().get(f"/files/{sha256}")
    resp.raise_for_status()
    match = re.search(
        r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?',
        resp.headers.get("content-disposition", ""),
    )
    name = httpx.URL("x:/" + (match.group(1) if match else "file")).path.rsplit("/", 1)[
        -1
    ]
    target_dir = Path(
        os.environ.get("INBOX_DOWNLOAD_DIR", Path.home() / "Downloads" / "study-inbox")
    ).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{sha256}_{name}"
    target.write_bytes(resp.content)
    return str(target)


@mcp.tool()
def health() -> list:
    """Состояние источников: когда последний раз успешно синхронизировались и последняя ошибка."""
    return _json(_client().get("/health"))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
```

Before implementing, re-check `MCPServer` import and `@mcp.tool()` against the installed `mcp` version (`uv run python -c "import mcp.server; print(mcp.server.MCPServer)"`); if `mcp` resolved to 1.x, pin `mcp>=2` or switch to `from mcp.server.fastmcp import FastMCP`.

Simplify `get_file` name parsing if it reads awkwardly: `urllib.parse.unquote` on the `filename*=` value.

- [ ] **Step 4: Run** — `cd mcp_adapter && uv run pytest -v`. Expect PASS.
- [ ] **Step 5: README with Claude config**

```json
{
  "mcpServers": {
    "study-inbox": {
      "command": "uv",
      "args": ["run", "--directory", "/ABS/PATH/university-pz-parser/mcp_adapter", "study-inbox-mcp"],
      "env": {"INBOX_URL": "https://inbox.example.ru", "INBOX_TOKEN": "..."}
    }
  }
}
```

- [ ] **Step 6: Commit** — `feat(mcp): study-inbox MCP adapter`.

---

### Task 16: Deploy files and README

**Files:**
- Create: `deploy/study-inbox.service`, `deploy/Caddyfile`
- Modify: `README.md`

- [ ] **Step 1: systemd unit**

```ini
[Unit]
Description=Study Inbox
After=network-online.target

[Service]
User=studyinbox
WorkingDirectory=/opt/study-inbox
EnvironmentFile=/opt/study-inbox/.env
ExecStart=/opt/study-inbox/.venv/bin/university-pz-parser serve
Restart=always
RestartSec=10
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/study-inbox/data

[Install]
WantedBy=multi-user.target
```

```
inbox.example.ru {
    reverse_proxy 127.0.0.1:8000
}
```

- [ ] **Step 2: README** — sections: what it does (link `CONTEXT.md`), setup (`uv sync`, `.env` from `.env.example`, `login`, `list-chats`, `init-db`), running (`serve`, one worker only), API table (from the spec), MCP setup (link `mcp_adapter/README.md`), deploy (copy unit + Caddyfile).
- [ ] **Step 3: Final checks** — `uv run ruff check . && uv run ruff format --check . && uv run pytest -v && uv run alembic check`.
- [ ] **Step 4: Commit** — `docs: deploy files and README`.
