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
