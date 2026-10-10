from zoneinfo import ZoneInfo

from inbox_hub.settings import SdoSettings
from inbox_hub.sources.sdo import html
from inbox_hub.sources.sdo.source import Course, SdoSource

MSK = ZoneInfo("Europe/Moscow")


class FakeSdo:
    async def login(self):
        pass

    async def ajax(self, *a, **k):
        return {"courses": []}


class FakeIngest:
    def __init__(self):
        self.seen = []

    async def ingest(self, item):
        from inbox_hub.services.ingest import IngestStatus

        self.seen.append(item.external_key)
        return IngestStatus.NEW


class FakeRest:
    def __init__(self, token):
        self._t = token
        self.token_calls = 0

    async def token(self):
        self.token_calls += 1
        return self._t

    async def courses(self, excluded):
        return [Course(1, "К", None)]

    async def course_pages(self, course_id):
        page = html.CoursePage([html.SectionRef(0, "Общее", "Описание курса")], [])
        return page, {}


async def test_html_backend_never_touches_rest():
    rest = FakeRest("T")
    settings = SdoSettings(url="https://x", username="u", password="p")
    src = SdoSource(FakeSdo(), FakeIngest(), settings, MSK, rest=rest)
    report = await src.sync()
    assert rest.token_calls == 0
    assert "sdo backend: html" in report.warnings


async def test_auto_backend_uses_rest_when_token_issued():
    rest = FakeRest("T")
    ingest = FakeIngest()
    settings = SdoSettings(url="https://x", username="u", password="p", backend="auto")
    report = await SdoSource(FakeSdo(), ingest, settings, MSK, rest=rest).sync()
    assert "sdo backend: rest" in report.warnings
    assert ingest.seen == ["section:1:0"]


async def test_auto_backend_falls_back_when_no_token():
    settings = SdoSettings(url="https://x", username="u", password="p", backend="auto")
    report = await SdoSource(
        FakeSdo(), FakeIngest(), settings, MSK, rest=FakeRest(None)
    ).sync()
    assert "sdo backend: html" in report.warnings


async def test_module_details_failure_skips_item_instead_of_blanking_it():
    from pathlib import Path

    fx = Path(__file__).parents[2] / "fixtures" / "sdo"

    class Sdo(FakeSdo):
        async def ajax(self, *a, **k):
            return {"courses": [{"id": 3058, "fullname": "К", "summary": ""}]}

        async def page(self, path):
            if "course/view" in path:
                return (fx / "course_3058.html").read_text()
            if "id=90675" in path:
                raise RuntimeError("timeout")
            return (fx / "assign_92447.html").read_text()

        async def head_remote_id(self, url):
            return "etag:x"

        def fetcher(self, url, name):
            return None

        async def resolve_redirect(self, path):
            return path

    ingest = FakeIngest()
    settings = SdoSettings(url="https://x", username="u", password="p")
    report = await SdoSource(Sdo(), ingest, settings, MSK).sync()
    assert "cm:90675" not in ingest.seen
    assert "cm:92447" in ingest.seen
    assert any("90675" in w for w in report.warnings)


async def test_gradebook_failure_skips_course_and_ingests_nothing():
    from pathlib import Path

    fx = Path(__file__).parents[2] / "fixtures" / "sdo"

    class Sdo(FakeSdo):
        async def ajax(self, *a, **k):
            return {"courses": [{"id": 3058, "fullname": "К", "summary": ""}]}

        async def page(self, path):
            if "grade/report" in path:
                raise RuntimeError("timeout")
            if "course/view" in path:
                return (fx / "course_3058.html").read_text()
            return (fx / "assign_92447.html").read_text()

        async def head_remote_id(self, url):
            return "etag:x"

        def fetcher(self, url, name):
            return None

        async def resolve_redirect(self, path):
            return path

    ingest = FakeIngest()
    settings = SdoSettings(url="https://x", username="u", password="p")
    report = await SdoSource(Sdo(), ingest, settings, MSK).sync()
    assert ingest.seen == []
    assert any("course 3058" in w for w in report.warnings)
