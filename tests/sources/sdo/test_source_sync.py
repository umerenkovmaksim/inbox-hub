from zoneinfo import ZoneInfo

from university_pz_parser.settings import SdoSettings
from university_pz_parser.sources.sdo import html
from university_pz_parser.sources.sdo.source import Course, SdoSource

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
        from university_pz_parser.services.ingest import IngestStatus

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
