from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import httpx

from inbox_hub.domain.dto import IncomingFile
from inbox_hub.settings import SdoSettings
from inbox_hub.sources.sdo import html
from inbox_hub.sources.sdo.client import SdoHttp


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
        from inbox_hub.sources.sdo.source import Course

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
        from inbox_hub.sources.sdo.source import ModuleDetails

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
                value, maximum = html.parse_grade_text(feedback.get("gradefordisplay"))
                details[m["id"]] = ModuleDetails(
                    text=html.html_to_text(a.get("intro")),
                    files=tuple(self._file(f) for f in attachments),
                    status={
                        "Состояние ответа на задание": (
                            (status.get("lastattempt") or {}).get("submission") or {}
                        ).get("status"),
                    },
                    grade=html.GradeInfo(
                        value=value,
                        max=maximum,
                        graded_at=_ts(
                            (feedback.get("grade") or {}).get("timemodified")
                        ),
                        grading=(status.get("lastattempt") or {}).get("gradingstatus"),
                        feedback=html.html_to_text(comment) if comment else None,
                    ),
                    available_from=_ts(a.get("allowsubmissionsfromdate")),
                    due_at=_ts(a.get("duedate")),
                    uploaded=[
                        t
                        for t in (_ts(f.get("timemodified")) for f in attachments)
                        if t
                    ],
                )
        return html.CoursePage(sections, modules), details
