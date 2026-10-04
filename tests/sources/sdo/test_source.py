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
