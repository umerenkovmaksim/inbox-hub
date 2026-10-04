from datetime import UTC, datetime

import httpx
import pytest

from university_pz_parser.api.app import create_app
from university_pz_parser.container import Container
from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.enums import Source

H = {"Authorization": "Bearer secret"}


@pytest.fixture
async def container(settings, engine):
    c = await Container.build(settings, with_sources=False)
    yield c
    await c.stop()


async def seed(c):
    async def fetch(tmp):
        p = tmp / "pz.txt"
        p.write_text("x" * 5000)
        return p

    await c.ingest.ingest(
        IncomingItem(
            source=Source.SDO,
            external_key="cm:1",
            channel_external_id="3430",
            channel_title="МИС",
            kind="assign",
            text="ПЗ1",
            title="Практическое задание №1",
            published_at=datetime(2026, 10, 1, 10, tzinfo=UTC),
            due_at=datetime(2026, 10, 22, tzinfo=UTC),
            files=(IncomingFile("etag:1", "pz.txt", fetch=fetch),),
        )
    )


def client_for(container):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(container)), base_url="http://t"
    )


async def test_auth_required(container):
    async with client_for(container) as client:
        r = await client.get(
            "/items",
            params={"from": "2026-10-01T00:00:00Z", "to": "2026-10-02T00:00:00Z"},
        )
        assert r.status_code == 401
        assert (
            await client.get("/health", headers={"Authorization": "Bearer nope"})
        ).status_code == 401


async def test_items_flow(container):
    await seed(container)
    params = {"from": "2026-10-01T00:00:00+03:00", "to": "2026-10-02T00:00:00+03:00"}
    async with client_for(container) as client:
        body = (await client.get("/items", params=params, headers=H)).json()
        assert body["count"] == 1
        it = body["items"][0]
        assert it["channel"] == "МИС" and it["source"] == "sdo"
        f = it["files"][0]
        assert len(f["text"]) == 4000 and f["text_truncated"] is True
        full = (await client.get(f"/items/{it['id']}", headers=H)).json()
        assert len(full["files"][0]["text"]) == 5000
        raw = await client.get(f"/files/{f['sha256']}", headers=H)
        assert raw.status_code == 200 and raw.content == b"x" * 5000
        r = await client.post("/items/receive", json={"ids": [it["id"]]}, headers=H)
        assert r.json() == {"updated": 1}
        assert (await client.get("/items", params=params, headers=H)).json()[
            "count"
        ] == 0
        assert (await client.get("/items/999", headers=H)).status_code == 404
        assert (await client.get("/files/" + "0" * 64, headers=H)).status_code == 404
        assert (
            await client.get(
                "/items",
                params={"from": "2026-10-01T00:00:00", "to": "2026-10-02T00:00:00"},
                headers=H,
            )
        ).status_code == 422


async def test_admin_unknown_source(container):
    async with client_for(container) as client:
        assert (await client.post("/admin/sync/nope", headers=H)).status_code == 404


async def test_empty_token_is_never_accepted(settings, engine):
    from university_pz_parser.settings import ApiSettings

    settings.api = ApiSettings(token="")
    c = await Container.build(settings, with_sources=False)
    try:
        async with client_for(c) as client:
            for header in ("Bearer ", "Bearer"):
                r = await client.get("/health", headers={"Authorization": header})
                assert r.status_code == 401
    finally:
        await c.stop()


async def test_item_exposes_updated_at(container):
    await seed(container)
    params = {"from": "2026-10-01T00:00:00+03:00", "to": "2026-10-02T00:00:00+03:00"}
    async with client_for(container) as client:
        it = (await client.get("/items", params=params, headers=H)).json()["items"][0]
        assert it["updated_at"]
        r = await client.post(
            "/items/receive",
            json={
                "ids": [it["id"]],
                "versions": {str(it["id"]): "2000-01-01T00:00:00Z"},
            },
            headers=H,
        )
        assert r.json() == {"updated": 0}


async def test_grade_is_structured_in_item(container):
    await container.ingest.ingest(
        IncomingItem(
            source=Source.SDO,
            external_key="cm:2",
            channel_external_id="1",
            channel_title="К",
            kind="assign",
            text="ДЗ",
            published_at=datetime(2026, 10, 1, 10, tzinfo=UTC),
            hashed_extra={
                "grade": {
                    "value": 70.0,
                    "max": 70.0,
                    "graded_at": "2026-10-02T10:00:00+00:00",
                    "grader": "Преподаватель",
                    "grading": "Оценено",
                    "feedback": "ок",
                }
            },
        )
    )
    params = {"from": "2026-10-01T00:00:00+03:00", "to": "2026-10-02T00:00:00+03:00"}
    async with client_for(container) as client:
        it = (await client.get("/items", params=params, headers=H)).json()["items"][0]
    assert it["grade"]["value"] == 70.0 and it["grade"]["grader"] == "Преподаватель"
    assert it["grade"]["graded_at"].startswith("2026-10-02T10:00:00")
    assert "grade" not in it["extra"]
