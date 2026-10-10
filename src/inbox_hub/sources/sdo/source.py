import logging
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from inbox_hub.domain.dto import IncomingFile, IncomingItem
from inbox_hub.enums import Source
from inbox_hub.services.ingest import IngestService
from inbox_hub.services.jobs import SyncReport
from inbox_hub.settings import SdoSettings
from inbox_hub.sources.sdo import html
from inbox_hub.sources.sdo.client import SdoHttp

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
    grade: html.GradeInfo | None = None
    available_from: datetime | None = None
    due_at: datetime | None = None
    uploaded: list[datetime] = field(default_factory=list)


def _fmt(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def _merged_grade(
    grade: html.GradeInfo | None, row: html.GradeRow | None
) -> html.GradeInfo | None:
    if (
        row is None
        or row.value is None
        or (grade is not None and grade.value is not None)
    ):
        return grade
    base = grade or html.GradeInfo()
    return html.GradeInfo(
        value=row.value,
        max=row.max,
        graded_at=base.graded_at,
        grader=base.grader,
        grading=base.grading,
        feedback=base.feedback or row.feedback,
    )


def build_items(
    course: Course,
    page: html.CoursePage,
    details: dict[int, ModuleDetails],
    gradebook: html.Gradebook | None = None,
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
        row = gradebook.items.get(m.cmid) if gradebook else None
        grade = _merged_grade(d.grade, row)
        hashed = {"grade": grade.as_dict()} if grade and not grade.is_empty else {}
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
    total = gradebook.course_total if gradebook else None
    if total is not None and total.value is not None:
        grade = html.GradeInfo(value=total.value, max=total.max)
        maximum = f" из {_fmt(total.max)}" if total.max is not None else ""
        items.append(
            IncomingItem(
                external_key=f"course_grade:{course.id}",
                kind="course_grade",
                title=f"Итоговая оценка: {course.name}",
                text=f"Итоговая оценка за курс: {_fmt(total.value)}{maximum}",
                published_at=None,
                hashed_extra={"grade": grade.as_dict()},
                extra={"course_id": course.id},
                **common,
            )
        )
    return items


class SdoSource:
    name = "sdo"

    def __init__(
        self,
        sdo: SdoHttp,
        ingest: IngestService,
        settings: SdoSettings,
        tz: ZoneInfo,
        rest=None,
    ):
        self.rest = rest
        self.sdo = sdo
        self.ingest = ingest
        self.settings = settings
        self.tz = tz

    async def sync(self) -> SyncReport:
        report = SyncReport(self.name)
        await self.sdo.login()
        rest_token = None
        if self.rest is not None and self.settings.backend == "auto":
            rest_token = await self.rest.token()
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
        for course in await self.courses():
            try:
                page = html.parse_course(
                    await self.sdo.page(f"/course/view.php?id={course.id}"), self.tz
                )
                gradebook = html.parse_gradebook(
                    await self.sdo.page(f"/grade/report/user/index.php?id={course.id}")
                )
                details: dict[int, ModuleDetails] = {}
                failed: set[int] = set()
                for m in page.modules:
                    try:
                        details[m.cmid] = await self._details(m)
                    except Exception as exc:
                        log.warning(
                            "sdo module %s details failed", m.cmid, exc_info=True
                        )
                        failed.add(m.cmid)
                        report.warnings.append(f"module {m.cmid}: {exc!r}")
                page = html.CoursePage(
                    page.sections,
                    [m for m in page.modules if m.cmid not in failed],
                )
                for item in build_items(course, page, details, gradebook):
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
        if m.kind == "assign" and m.url:
            page = html.parse_assign(await self.sdo.page(m.url), self.tz)
            return ModuleDetails(
                text=page.intro,
                files=tuple([await self._file(f.url, f.name) for f in page.files]),
                status=page.status,
                grade=page.grade,
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
        return ModuleDetails()

    async def _file(self, url: str, name: str) -> IncomingFile:
        return IncomingFile(
            remote_id=await self.sdo.head_remote_id(url),
            name=name,
            url=url,
            fetch=self.sdo.fetcher(url, name),
        )
