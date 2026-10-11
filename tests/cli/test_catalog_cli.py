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
