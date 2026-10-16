from datetime import UTC, datetime

import pytest

from inbox_hub.domain.dto import IncomingItem
from inbox_hub.enums import Source
from inbox_hub.services.access import AccessService
from inbox_hub.services.catalog import CatalogError
from inbox_hub.services.catalog_file import CatalogFile, parse_spec


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


@pytest.fixture
def cf(sessions, clock):
    return CatalogFile(sessions, clock=clock)


SPEC = """
categories:
  study:
    title: Учёба
    auto_sources: [sdo]
  work:
    title: Работа
    channels:
      - telegram:-1005
telegram:
  track: [-1006]
keys:
  digest:
    categories: [study, work]
"""


def signs(changes):
    return [str(c) for c in changes]


async def test_apply_creates_everything_and_is_idempotent(cf, catalog, sessions):
    spec = parse_spec(SPEC)
    plan = await cf.plan(spec)
    assert plan.tokens == {} and await catalog.list_categories() == []
    assert signs(plan.changes) == [
        '+ category study "Учёба"',
        '+ category work "Работа"',
        "+ track telegram:-1005 (new chat)",
        "+ track telegram:-1006 (new chat)",
        "+ work <- telegram:-1005",
        "+ key digest: study, work",
    ]
    applied = await cf.apply(spec)
    assert applied.changes == plan.changes
    principal = await AccessService(sessions, "").authenticate(applied.tokens["digest"])
    assert principal.name == "digest" and len(principal.category_ids) == 2
    assert await catalog.tracked_chat_ids() == [-1005, -1006]
    again = await cf.plan(spec, prune=True)
    assert again.changes == [] and again.pending == []


async def test_removals_wait_for_prune(cf, catalog):
    await cf.apply(parse_spec(SPEC))
    await catalog.create_category("news", "Новости")
    smaller = parse_spec(
        """
categories:
  study: {title: Учёба, auto_sources: [sdo]}
keys:
  digest: {categories: [study]}
"""
    )
    plan = await cf.plan(smaller)
    assert plan.changes == []
    assert signs(plan.pending) == [
        "- untrack telegram:-1005",
        "- untrack telegram:-1006",
        "- key digest -x work",
        "- category news (0 channels, keys: -)",
        "- category work (1 channels, keys: digest)",
    ]
    applied = await cf.apply(smaller, prune=True)
    assert applied.changes == plan.pending
    assert [c.slug for c in await catalog.list_categories()] == ["study"]
    assert await catalog.tracked_chat_ids() == []
    [key] = await catalog.list_keys()
    assert [c.slug for c in key.categories] == ["study"]


async def test_auto_source_channels_survive_prune_unless_excluded(cf, catalog, ingest):
    await cf.apply(parse_spec(SPEC))
    await ingest.ingest(sdo_item(1))
    await ingest.ingest(sdo_item(2))
    assert (await cf.plan(parse_spec(SPEC), prune=True)).changes == []
    spec = parse_spec(
        SPEC.replace("auto_sources: [sdo]", "auto_sources: [sdo]\n    exclude: [sdo:2]")
    )
    plan = await cf.apply(spec)
    assert signs(plan.changes) == ["- study -x sdo:2 (course 2) (excluded)"]
    by_id = {c.external_id: c for c in await catalog.list_channels()}
    assert [c.slug for c in by_id["1"].categories] == ["study"]
    assert by_id["2"].categories == []


async def test_unknown_sdo_course_is_a_warning(cf):
    spec = parse_spec("categories:\n  study:\n    title: x\n    channels: [sdo:9]\n")
    plan = await cf.apply(spec)
    assert plan.warnings == [
        "study: sdo:9 is not known yet (appears after a sync), skipped"
    ]


async def test_updates_fields_and_paused_chats(cf, catalog):
    await cf.apply(parse_spec(SPEC))
    spec = parse_spec(
        SPEC.replace("title: Работа", "title: Work\n    description: job").replace(
            "track: [-1006]", "track: [-1006]\n  paused: [-1005]"
        )
    )
    plan = await cf.apply(spec)
    assert signs(plan.changes) == [
        "~ category work: title 'Работа' -> 'Work'",
        "~ category work: description None -> 'job'",
        "- untrack telegram:-1005 (paused)",
    ]
    channels = {c.external_id: c for c in await catalog.list_channels()}
    assert [c.slug for c in channels["-1005"].categories] == ["work"]
    assert (await cf.plan(spec, prune=True)).changes == []


async def test_key_with_unknown_category_fails_whole_apply(cf, catalog):
    with pytest.raises(CatalogError, match="unknown categories"):
        await cf.apply(parse_spec("keys:\n  k: {categories: [nope]}\n"))
    await catalog.create_category("legacy", "Старое")
    spec = parse_spec("keys:\n  k: {categories: [legacy]}\n")
    assert signs((await cf.plan(spec)).changes) == ["+ key k: legacy"]
    with pytest.raises(CatalogError, match="unknown categories"):
        await cf.plan(spec, prune=True)


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("categories: [", "invalid YAML"),
        ("categories:\n  Bad: {title: x}\n", "invalid name"),
        ("categories:\n  a: {title: x, channels: [vk:1]}\n", "channel must be"),
        (
            "categories:\n  a: {title: x, channels: [telegram:abc]}\n",
            "must be a number",
        ),
        (
            "categories:\n  a: {title: x, auto_sources: [no]}\n",
            "categories.a.auto_sources.0",
        ),
        ("categories:\n  a: {titel: x}\n", "categories.a.titel"),
        ("telegram: {track: [1], paused: [1]}\n", "both track and paused"),
        ("categories:\n  a: {title: x, channels: [sdo:1], exclude: [sdo:1]}\n", "both"),
    ],
)
def test_parse_errors(text, error):
    with pytest.raises(CatalogError, match=error):
        parse_spec(text)


async def test_export_round_trips(cf, catalog, ingest):
    await cf.apply(parse_spec(SPEC))
    await ingest.ingest(sdo_item(1))
    await catalog.create_category("2024", 'Архив #1: "старое"')
    await catalog.track_chat(-1007, ["2024"])
    await catalog.untrack_chat(-1007)
    await catalog.create_key("old", [])
    await catalog.revoke_key("old")
    text = await cf.export()
    assert "# auto: sdo:1 course 1" in text
    assert "paused:\n    - -1007" in text
    assert "old:  # revoked" in text
    spec = parse_spec(text)
    assert spec.categories["2024"].title == 'Архив #1: "старое"'
    plan = await cf.plan(spec, prune=True)
    assert plan.changes == [] and plan.pending == []


def test_example_file_parses():
    from pathlib import Path

    example = Path(__file__).parents[2] / "catalog.example.yaml"
    spec = parse_spec(example.read_text(encoding="utf-8"))
    assert spec.categories["study"].exclude == ["sdo:1234"]
