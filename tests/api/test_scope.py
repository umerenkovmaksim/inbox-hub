from datetime import UTC, datetime

import pytest

from inbox_hub.domain.dto import IncomingFile, IncomingItem
from inbox_hub.enums import Source
from inbox_hub.services.jobs import SyncReport
from tests.api.conftest import client_for

ADMIN = {"Authorization": "Bearer secret"}
WINDOW = {"from": "2026-10-01T00:00:00Z", "to": "2026-10-02T00:00:00Z"}
SHARED_TEXT = "Хакатон в субботу, регистрация до пятницы по ссылке в описании"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def tg(chat, msg, text, hour=8, files=()):
    return IncomingItem(
        source=Source.TELEGRAM,
        external_key=f"{chat}:{msg}",
        channel_external_id=str(chat),
        channel_title=f"chat {chat}",
        kind="text",
        text=text,
        published_at=datetime(2026, 10, 1, hour, tzinfo=UTC),
        files=files,
    )


@pytest.fixture
async def world(container):
    cat = container.catalog
    await cat.create_category("study", "Учёба")
    await cat.create_category("work", "Работа")
    await cat.track_chat(-1001, ["study"])
    await cat.track_chat(-1002, ["work"])
    await cat.track_chat(-1003, ["study", "work"])
    await cat.track_chat(-1004)
    _, study = await cat.create_key("study-digest", ["study"])
    _, work = await cat.create_key("work-digest", ["work"])
    _, empty = await cat.create_key("nothing", [])

    async def fetch(tmp):
        p = tmp / "nda.txt"
        p.write_text("секретный рабочий документ")
        return p

    ingest = container.ingest
    await ingest.ingest(tg(-1001, 1, SHARED_TEXT, hour=8))
    await ingest.ingest(
        tg(
            -1002,
            1,
            "рабочее",
            hour=9,
            files=(IncomingFile("d:1", "nda.txt", fetch=fetch),),
        )
    )
    await ingest.ingest(tg(-1002, 2, SHARED_TEXT, hour=10))
    await ingest.ingest(tg(-1003, 1, "общий канал", hour=11))
    await ingest.ingest(tg(-1004, 1, "без категории", hour=12))
    return {"study": bearer(study), "work": bearer(work), "empty": bearer(empty)}


async def keys_of(client, headers, **params):
    r = await client.get("/items", params={**WINDOW, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return {i["id"]: i for i in r.json()["items"]}


async def test_each_key_sees_only_its_categories(container, world):
    async with client_for(container) as client:
        study = await keys_of(client, world["study"])
        work = await keys_of(client, world["work"])
        admin = await keys_of(client, ADMIN)
        assert {i["channel"] for i in study.values()} == {"chat -1001", "chat -1003"}
        assert {i["channel"] for i in work.values()} == {"chat -1002", "chat -1003"}
        assert len(admin) == 5
        assert await keys_of(client, world["empty"]) == {}
        shared = next(i for i in study.values() if i["channel"] == "chat -1003")
        assert shared["categories"] == ["study"]
        assert work[shared["id"]]["categories"] == ["work"]
        assert admin[shared["id"]]["categories"] == ["study", "work"]


async def test_out_of_scope_item_and_file_are_not_found(container, world):
    async with client_for(container) as client:
        work = await keys_of(client, world["work"])
        nda = next(i for i in work.values() if i["files"])
        sha = nda["files"][0]["sha256"]
        assert (
            await client.get(f"/items/{nda['id']}", headers=world["study"])
        ).status_code == 404
        assert (
            await client.get(f"/files/{sha}", headers=world["study"])
        ).status_code == 404
        assert (
            await client.get(f"/items/{nda['id']}", headers=world["work"])
        ).status_code == 200
        assert (
            await client.get(f"/files/{sha}", headers=world["work"])
        ).status_code == 200


async def test_category_parameter_only_narrows(container, world):
    async with client_for(container) as client:
        for slugs in (["work"], ["nope"], ["study", "work"]):
            r = await client.get(
                "/items", params={**WINDOW, "categories": slugs}, headers=world["study"]
            )
            assert r.status_code == 403
        narrowed = await keys_of(client, world["work"], categories=["work"])
        assert len(narrowed) == 3
        assert len(await keys_of(client, ADMIN, categories=["study"])) == 2


async def test_duplicate_link_hidden_outside_scope(container, world):
    async with client_for(container) as client:
        work = await keys_of(client, world["work"])
        admin = await keys_of(client, ADMIN)
        dup = next(i for i in work.values() if i["text"] == SHARED_TEXT)
        assert admin[dup["id"]]["duplicate_of_id"] is not None
        assert dup["duplicate_of_id"] is None


async def test_receipts_are_per_key_and_scoped(container, world):
    async with client_for(container) as client:
        study = await keys_of(client, world["study"])
        work = await keys_of(client, world["work"])
        shared_id = next(i for i in study.values() if i["channel"] == "chat -1003")[
            "id"
        ]
        work_only = [i for i in work if i not in study]
        r = await client.post(
            "/items/receive",
            json={"ids": [shared_id, *work_only]},
            headers=world["study"],
        )
        assert r.json() == {"updated": 1}
        assert shared_id not in await keys_of(client, world["study"])
        assert set(await keys_of(client, world["work"])) == set(work)
        r = await client.post(
            "/items/receive", json={"ids": [shared_id]}, headers=ADMIN
        )
        assert r.status_code == 400

        await container.ingest.ingest(tg(-1003, 1, "общий канал (исправлено)", hour=11))
        assert shared_id in await keys_of(client, world["study"])


async def test_revoked_and_rotated_keys(container, world):
    async with client_for(container) as client:
        assert (await client.get("/me", headers=world["study"])).status_code == 200
        new_token = await container.catalog.rotate_key("study-digest")
        assert (await client.get("/me", headers=world["study"])).status_code == 401
        me = (await client.get("/me", headers=bearer(new_token))).json()
        assert me["name"] == "study-digest" and not me["admin"]
        assert [c["slug"] for c in me["categories"]] == ["study"]
        await container.catalog.revoke_key("study-digest")
        assert (await client.get("/me", headers=bearer(new_token))).status_code == 401


async def test_grant_takes_effect_immediately(container, world):
    async with client_for(container) as client:
        assert len(await keys_of(client, world["study"])) == 2
        await container.catalog.grant("study-digest", "work")
        assert len(await keys_of(client, world["study"])) == 4


async def test_keys_cannot_reach_admin(container, world):
    async with client_for(container) as client:
        for method, path in (
            ("GET", "/admin/keys"),
            ("POST", "/admin/keys/work-digest/rotate"),
            ("GET", "/admin/categories"),
            ("POST", "/admin/sync/telegram"),
        ):
            r = await client.request(method, path, headers=world["study"])
            assert r.status_code == 403, path
        assert (await client.get("/admin/keys", headers=ADMIN)).status_code == 200


async def test_health_is_trimmed_for_keys(container, world):
    await container.health.record(SyncReport("telegram", warnings=["chat -1002: boom"]))
    async with client_for(container) as client:
        mine = (await client.get("/health", headers=world["study"])).json()
        admin = (await client.get("/health", headers=ADMIN)).json()
    assert mine == [
        {
            "source": "telegram",
            "ok": True,
            "last_run_at": mine[0]["last_run_at"],
            "last_ok_at": mine[0]["last_ok_at"],
        }
    ]
    assert admin[0]["last_report"]["warnings"] == ["chat -1002: boom"]


async def test_admin_manages_catalog_over_http(container, world):
    async with client_for(container) as client:
        r = await client.post(
            "/admin/categories",
            json={"slug": "hackathons", "title": "Хакатоны", "auto_sources": []},
            headers=ADMIN,
        )
        assert r.status_code == 200
        r = await client.put(
            "/admin/categories/hackathons/channels/telegram:-1004", headers=ADMIN
        )
        assert r.json()["categories"] == ["hackathons"]
        r = await client.post(
            "/admin/keys",
            json={"name": "hack", "categories": ["hackathons"]},
            headers=ADMIN,
        )
        token = r.json()["token"]
        got = await keys_of(client, bearer(token))
        assert [i["text"] for i in got.values()] == ["без категории"]
        assert (
            await client.post("/admin/keys", json={"name": "hack"}, headers=ADMIN)
        ).status_code == 422
        assert (
            await client.delete("/admin/categories/nope", headers=ADMIN)
        ).status_code == 404
