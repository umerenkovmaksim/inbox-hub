import asyncio

import pytest
from typer.testing import CliRunner

from inbox_hub.__main__ import app
from inbox_hub.db.session import create_all, make_engine
from inbox_hub.settings import get_settings


@pytest.fixture
def cli(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'inbox.db'}"
    for name, value in {
        "TELEGRAM__API_ID": "1",
        "TELEGRAM__API_HASH": "h",
        "SDO__USERNAME": "u",
        "SDO__PASSWORD": "p",
        "API__TOKEN": "t",
        "DATABASE__URL": url,
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()

    async def init():
        engine = make_engine(url)
        await create_all(engine)
        await engine.dispose()

    asyncio.run(init())
    runner = CliRunner()

    def run(*args):
        return runner.invoke(app, list(args))

    yield run
    get_settings.cache_clear()


def test_categories_chats_and_keys(cli):
    assert cli("categories", "create", "work", "--title", "Работа").exit_code == 0
    assert cli("chats", "add", "-1005", "--category", "work").exit_code == 0
    out = cli("channels", "list").output
    assert "telegram:-1005\ttracked\twork" in out
    created = cli("keys", "create", "work-digest", "--category", "work")
    assert created.exit_code == 0 and created.output.startswith("ihk_")
    assert "work-digest\tactive\twork" in cli("keys", "list").output
    assert cli("keys", "revoke", "work-digest").exit_code == 0
    assert "work-digest\tinactive" in cli("keys", "list").output


def test_errors_exit_nonzero(cli):
    r = cli("keys", "create", "k", "--category", "missing")
    assert r.exit_code == 1 and "no category 'missing'" in r.output


def test_remove_negative_chat_id(cli):
    cli("chats", "add", "-1005")
    r = cli("chats", "remove", "-1005")
    assert r.exit_code == 0, r.output
    assert "telegram:-1005\t-" in cli("channels", "list").output


def test_list_chats_reads_the_collector_snapshot(cli, tmp_path):
    from inbox_hub.db.session import make_sessions
    from inbox_hub.extractors.registry import ExtractorRegistry
    from inbox_hub.services.ingest import IngestService
    from inbox_hub.storage.files import FileStore

    assert "no dialogs yet" in cli("list-chats").output

    async def record():
        engine = make_engine(get_settings().database.url)
        ingest = IngestService(
            make_sessions(engine),
            FileStore(tmp_path / "files"),
            ExtractorRegistry(),
            [],
            1,
        )
        await ingest.record_dialogs([(-1007, "Группа", "group")])
        await engine.dispose()

    asyncio.run(record())
    result = cli("list-chats")
    assert result.exit_code == 0
    assert "-1007\tgroup\tГруппа" in result.output


def test_catalog_plan_apply_export(cli, tmp_path):
    file = tmp_path / "catalog.yaml"
    file.write_text(
        "categories:\n"
        "  work:\n"
        "    title: Работа\n"
        "    channels: [telegram:-1005]\n"
        "keys:\n"
        "  work-digest: {categories: [work]}\n",
        encoding="utf-8",
    )
    plan = cli("catalog", "plan", "-f", str(file))
    assert plan.exit_code == 0, plan.output
    assert "+ category work" in plan.output
    assert cli("categories", "list").output == ""

    declined = CliRunner().invoke(
        app, ["catalog", "apply", "-f", str(file)], input="n\n"
    )
    assert declined.exit_code == 1 and cli("categories", "list").output == ""

    applied = cli("catalog", "apply", "-f", str(file), "--yes")
    assert applied.exit_code == 0, applied.output
    assert "token for work-digest (shown once): ihk_" in applied.output
    assert "telegram:-1005\ttracked\twork" in cli("channels", "list").output
    assert "no changes" in cli("catalog", "plan", "-f", str(file), "--prune").output

    exported = cli("catalog", "export")
    assert "  work:\n    title: Работа" in exported.output
    stdin = CliRunner().invoke(
        app, ["catalog", "apply", "-f", "-"], input=exported.output
    )
    assert stdin.exit_code == 0 and "no changes" in stdin.output


def test_catalog_errors_exit_nonzero(cli, tmp_path):
    missing = cli("catalog", "plan", "-f", str(tmp_path / "nope.yaml"))
    assert missing.exit_code == 1 and "error:" in missing.output
    bad = tmp_path / "bad.yaml"
    bad.write_text("keys:\n  k: {categories: [nope]}\n", encoding="utf-8")
    r = cli("catalog", "apply", "-f", str(bad), "--yes")
    assert r.exit_code == 1 and "unknown categories" in r.output
    bad.write_text("categories:\n  a: {title: x}\n", encoding="utf-8")
    r = CliRunner().invoke(app, ["catalog", "apply", "-f", "-"], input=bad.read_text())
    assert r.exit_code == 1 and "needs --yes" in r.output
