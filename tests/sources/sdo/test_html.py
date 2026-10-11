from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from inbox_hub.sources.sdo import html

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
    assert by_id[80843].url is None
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
