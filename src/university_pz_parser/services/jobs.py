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
