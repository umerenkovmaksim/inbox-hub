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
