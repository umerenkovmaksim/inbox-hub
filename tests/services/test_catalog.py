from datetime import UTC, datetime

import pytest

from inbox_hub.domain.dto import IncomingItem
from inbox_hub.enums import Source
from inbox_hub.services.access import AccessService, hash_token
from inbox_hub.services.catalog import CatalogError, NotFoundError


def sdo_item(course):
    return IncomingItem(
        source=Source.SDO,
        external_key=f"cm:{course}",
        channel_external_id=str(course),
        channel_title=f"course {course}",
        kind="assign",
        text="ДЗ",
        published_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


async def test_new_channels_join_auto_categories(catalog, ingest):
    await catalog.create_category("study", "Учёба", auto_sources=["sdo"])
    await catalog.create_category("news", "Новости")
    await ingest.ingest(sdo_item(1))
    [channel] = await catalog.list_channels()
    assert [c.slug for c in channel.categories] == ["study"]


async def test_auto_categories_do_not_touch_existing_channels(catalog, ingest):
    await ingest.ingest(sdo_item(1))
    await catalog.create_category("study", "Учёба", auto_sources=["sdo"])
    await ingest.ingest(sdo_item(2))
    channels = {c.external_id: c for c in await catalog.list_channels()}
    assert channels["1"].categories == []
    assert [c.slug for c in channels["2"].categories] == ["study"]


async def test_add_and_remove_channel_by_reference(catalog, ingest):
    await catalog.create_category("study", "Учёба")
    await ingest.ingest(sdo_item(7))
    [channel] = await catalog.list_channels()
    assert [
        c.slug for c in (await catalog.add_channel("study", "sdo:7")).categories
    ] == ["study"]
    assert (await catalog.remove_channel("study", str(channel.id))).categories == []
    with pytest.raises(NotFoundError):
        await catalog.add_channel("study", "sdo:404")
    with pytest.raises(NotFoundError):
        await catalog.add_channel("study", "vk:7")


async def test_track_untrack_and_seed(catalog):
    await catalog.create_category("work", "Работа")
    channel = await catalog.track_chat(-1005, ["work"])
    assert channel.tracked and [c.slug for c in channel.categories] == ["work"]
    await catalog.seed_tracked_chats([-1005, -1006])
    assert await catalog.tracked_chat_ids() == [-1005, -1006]
    await catalog.untrack_chat(-1006)
    await catalog.seed_tracked_chats([-1006])
    assert await catalog.tracked_chat_ids() == [-1005]


async def test_delete_category_drops_memberships(catalog, sessions):
    await catalog.create_category("work", "Работа")
    await catalog.track_chat(-1005, ["work"])
    _, token = await catalog.create_key("w", ["work"])
    await catalog.delete_category("work")
    [channel] = await catalog.list_channels()
    assert channel.categories == []
    principal = await AccessService(sessions, "").authenticate(token)
    assert principal.category_ids == frozenset()


async def test_keys_store_only_hashes(catalog, sessions):
    await catalog.create_category("study", "Учёба")
    key, token = await catalog.create_key("study-digest", ["study"])
    assert key.token_hash == hash_token(token) and token not in key.token_hash
    access = AccessService(sessions, "admin-token")
    assert (await access.authenticate(token)).name == "study-digest"
    assert (await access.authenticate("admin-token")).is_admin
    new = await catalog.rotate_key("study-digest")
    assert await access.authenticate(token) is None
    assert (await access.authenticate(new)).name == "study-digest"
    assert await access.authenticate("") is None


async def test_validation(catalog):
    with pytest.raises(CatalogError):
        await catalog.create_category("Bad Slug", "x")
    with pytest.raises(ValueError):
        await catalog.create_category("ok", "x", auto_sources=["vk"])
    await catalog.create_category("ok", "x")
    with pytest.raises(CatalogError):
        await catalog.create_category("ok", "x")
    with pytest.raises(NotFoundError):
        await catalog.create_key("k", ["missing"])
