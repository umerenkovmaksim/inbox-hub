from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from university_pz_parser.domain.hashing import content_hash
from university_pz_parser.sources.sdo.html import (
    CoursePage,
    Gradebook,
    GradeInfo,
    GradeRow,
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


def details(status="Нет ответа", grading="Не оценено", files=(), value=None):
    return ModuleDetails(
        text="Сделать модель",
        files=tuple(files),
        status={"Состояние ответа на задание": status},
        grade=GradeInfo(value=value, max=100.0 if value else None, grading=grading),
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


def test_teacher_grade_changes_hash():
    page = CoursePage([], [module()])
    ungraded = build_items(COURSE, page, {90815: details()})[0]
    graded = build_items(COURSE, page, {90815: details(grading="Оценено", value=90.0)})[
        0
    ]
    assert ungraded.hashed_extra["grade"]["value"] is None
    assert graded.hashed_extra["grade"]["value"] == 90.0
    assert content_hash(ungraded, []) != content_hash(graded, [])


def test_gradebook_grade_fills_module_without_grade_page():
    page = CoursePage([], [module(cmid=5, kind="quiz", name="Тест")])
    book = Gradebook({5: GradeRow("Тест", 8.0, 10.0, None, "молодец")}, None)
    item = build_items(COURSE, page, {}, book)[0]
    assert item.hashed_extra["grade"]["value"] == 8.0
    assert item.hashed_extra["grade"]["max"] == 10.0
    assert item.hashed_extra["grade"]["feedback"] == "молодец"


def test_gradebook_without_value_adds_no_grade():
    page = CoursePage([], [module(cmid=6, kind="scorm", name="Лекция")])
    book = Gradebook({6: GradeRow("Лекция", None, 100.0, None, None)}, None)
    assert build_items(COURSE, page, {}, book)[0].hashed_extra == {}


def test_course_grade_item_only_when_total_is_known():
    page = CoursePage([], [])
    total = GradeRow("Итоговая оценка за курс", 100.0, 100.0, None, None)
    items = build_items(COURSE, page, {}, Gradebook({}, total))
    assert [i.external_key for i in items] == ["course_grade:3430"]
    item = items[0]
    assert item.kind == "course_grade"
    assert item.text == "Итоговая оценка за курс: 100 из 100"
    assert item.hashed_extra["grade"]["value"] == 100.0
    empty = GradeRow("Итоговая оценка за курс", None, 100.0, None, None)
    assert build_items(COURSE, page, {}, Gradebook({}, empty)) == []
