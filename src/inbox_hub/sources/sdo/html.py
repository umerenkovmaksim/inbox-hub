import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from inbox_hub.sources.sdo.dates import parse_ru_datetime

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
class GradeInfo:
    value: float | None = None
    max: float | None = None
    graded_at: datetime | None = None
    grader: str | None = None
    grading: str | None = None
    feedback: str | None = None

    @property
    def is_empty(self) -> bool:
        return all(v is None for v in self.as_dict().values())

    def as_dict(self) -> dict:
        return {
            "value": self.value,
            "max": self.max,
            "graded_at": self.graded_at.isoformat() if self.graded_at else None,
            "grader": self.grader,
            "grading": self.grading,
            "feedback": self.feedback,
        }


@dataclass(frozen=True)
class GradeRow:
    name: str
    value: float | None
    max: float | None
    percent: float | None
    feedback: str | None


@dataclass(frozen=True)
class Gradebook:
    items: dict[int, GradeRow]
    course_total: GradeRow | None


@dataclass(frozen=True)
class AssignPage:
    intro: str
    files: list[RemoteFile]
    status: dict[str, str]
    grade: GradeInfo
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
        name = section.attributes.get("data-sectionname") or _text(
            section.css_first(".sectionname")
        )
        summary = _text(section.css_first(".summarytext"))
        sections.append(SectionRef(number, name, summary))
        for act in section.css("li.activity[data-id]"):
            module = _module(act, number, name, tz)
            if module is not None:
                modules.append(module)
    return CoursePage(sections, modules)


def _module(
    act: Node, number: int, section_name: str, tz: ZoneInfo
) -> ModuleRef | None:
    classes = (act.attributes.get("class") or "").split()
    kind = next(
        (c.removeprefix("modtype_") for c in classes if c.startswith("modtype_")), None
    )
    raw_id = act.attributes.get("data-id")
    if kind is None or not raw_id or not raw_id.isdigit():
        return None
    name_node = act.css_first(".instancename")
    if name_node is not None:
        for hidden in name_node.css(".accesshide"):
            hidden.decompose()
    card = act.css_first("[data-activityname]")
    name = (
        _text(name_node)
        or (card.attributes.get("data-activityname") if card else "")
        or f"cm{raw_id}"
    )
    link = act.css_first(".activityname a, a.aalink")
    description = _text(
        act.css_first(
            ".activity-altcontent, .contentafterlink, .description, .contentwithoutlink"
        )
    )
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
        uploaded = (
            parse_ru_datetime(_text(time_node), tz) if time_node is not None else None
        )
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


NUMBER = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*$")


def _number(text: str | None) -> float | None:
    match = NUMBER.match(text or "")
    return float(match.group(1).replace(",", ".")) if match else None


def parse_grade_text(text: str | None) -> tuple[float | None, float | None]:
    value, _, maximum = (text or "").partition("/")
    return _number(" ".join(value.split())), _number(" ".join(maximum.split()))


def _grade(status: dict[str, str], feedback: dict[str, str], tz: ZoneInfo) -> GradeInfo:
    value, maximum = parse_grade_text(feedback.get("Оценка"))
    comment = next((v for k, v in feedback.items() if k.startswith("Отзыв")), None)
    return GradeInfo(
        value=value,
        max=maximum,
        graded_at=parse_ru_datetime(feedback.get("Оценено в", ""), tz),
        grader=feedback.get("Оценено") or None,
        grading=status.get("Состояние оценивания") or None,
        feedback=comment or None,
    )


def parse_assign(html: str, tz: ZoneInfo) -> AssignPage:
    tree = HTMLParser(html)
    desc = tree.css_first(".activity-description")
    files = _intro_files(desc, tz)
    if desc is not None:
        for tree_node in desc.css('[id^="assign_files_tree"]'):
            tree_node.decompose()
    start, due = _dates(tree.css_first('[data-region="activity-dates"]'), tz)
    status = _table(tree.css_first(".submissionstatustable"))
    return AssignPage(
        intro=_text(desc),
        files=files,
        status=status,
        grade=_grade(status, _table(tree.css_first(".feedback")), tz),
        available_from=start,
        due_at=due,
    )


def parse_folder(html: str) -> list[RemoteFile]:
    tree = HTMLParser(html)
    root = tree.css_first('[role="main"]') or tree.body
    seen: dict[str, RemoteFile] = {}
    for link in root.css('a[href*="pluginfile.php"]') if root else []:
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


def _grade_row(tr: Node, name: str) -> GradeRow:
    value = _number(_text(tr.css_first("td.column-grade")))
    range_text = _text(tr.css_first("td.column-range"))
    maximum = _number(re.split(r"[–-]", range_text)[-1]) if range_text else None
    percent = _number(_text(tr.css_first("td.column-percentage")).removesuffix("%"))
    feedback = _text(tr.css_first("td.column-feedback")) or None
    return GradeRow(name, value, maximum, percent, feedback)


def parse_gradebook(html: str) -> Gradebook:
    tree = HTMLParser(html)
    items: dict[int, GradeRow] = {}
    total: GradeRow | None = None
    for tr in tree.css("table.user-grade tr"):
        head = tr.css_first("th.column-itemname")
        if head is None or tr.css_first("td.column-grade") is None:
            continue
        name = _text(head.css_first(".rowtitle")) or _text(head)
        link = head.css_first("a.gradeitemheader")
        if head.css_first(".courseitem") is not None:
            total = _grade_row(tr, name)
        elif link is not None:
            match = re.search(r"[?&]id=(\d+)", link.attributes.get("href") or "")
            if match:
                items[int(match.group(1))] = _grade_row(tr, name)
    return Gradebook(items, total)
