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
