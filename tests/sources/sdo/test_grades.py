from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from inbox_hub.sources.sdo import html

FX = Path(__file__).parents[2] / "fixtures" / "sdo"
MSK = ZoneInfo("Europe/Moscow")


def read(name):
    return (FX / name).read_text(encoding="utf-8")


def test_parse_grade_text():
    assert html.parse_grade_text("70,00\xa0/\xa070,00") == (70.0, 70.0)
    assert html.parse_grade_text("4,5 / 5") == (4.5, 5.0)
    assert html.parse_grade_text("5") == (5.0, None)
    assert html.parse_grade_text("-") == (None, None)
    assert html.parse_grade_text("- Действия Анализ оценок") == (None, None)
    assert html.parse_grade_text(None) == (None, None)


def test_graded_assignment_page():
    grade = html.parse_assign(read("assign_58491.html"), MSK).grade
    assert (grade.value, grade.max) == (70.0, 70.0)
    assert grade.graded_at == datetime(2024, 10, 14, 13, 26, tzinfo=UTC)
    assert grade.grader == "Преподаватель"
    assert grade.grading == "Оценено"
    assert grade.feedback is None


def test_ungraded_assignment_page_has_state_only():
    grade = html.parse_assign(read("assign_90675.html"), MSK).grade
    assert grade.value is None and grade.grading == "Не оценено"
    assert not grade.is_empty


def test_page_without_status_has_empty_grade():
    assert html.parse_assign("<html></html>", MSK).grade.is_empty


def test_gradebook():
    book = html.parse_gradebook(read("gradebook_2309.html"))
    assert (book.items[58491].value, book.items[58491].max) == (70.0, 70.0)
    assert book.items[58536].feedback == "Хорошо, но есть замечания"
    assert book.items[58884].value is None and book.items[58884].max == 100.0
    assert book.items[77001].value is None
    assert (book.course_total.value, book.course_total.max) == (100.0, 100.0)


def test_gradebook_garbage():
    book = html.parse_gradebook("<html><table class='user-grade'><tr><td>x</td></tr>")
    assert book.items == {} and book.course_total is None
