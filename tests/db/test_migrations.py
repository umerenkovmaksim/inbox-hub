import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from inbox_hub.settings import get_settings

ROOT = Path(__file__).parents[2]


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db = tmp_path / "inbox.db"
    for name, value in {
        "TELEGRAM__API_ID": "1",
        "TELEGRAM__API_HASH": "h",
        "SDO__USERNAME": "u",
        "SDO__PASSWORD": "p",
        "API__TOKEN": "t",
        "DATABASE__URL": f"sqlite+aiosqlite:///{db}",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "src" / "alembic"))
    yield cfg, db
    get_settings.cache_clear()


def test_0002_moves_existing_data_into_study(alembic_cfg):
    cfg, db = alembic_cfg
    command.upgrade(cfg, "0001")
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            INSERT INTO channels (id, source, external_id, title, cursor)
            VALUES (1, 'telegram', '-1001', 'ИСиТ', 5), (2, 'sdo', '3430', 'МИС', 0);
            INSERT INTO items (id, source, external_key, channel_id, kind, text,
                published_at, first_seen_at, updated_at, content_hash, edited,
                received_at, extra)
            VALUES
                (1, 'telegram', '-1001:1', 1, 'text', 'a', '2026-10-01 08:00:00',
                 '2026-10-01 08:00:00', '2026-10-01 08:00:00', 'h', 0,
                 '2026-10-02 09:00:00', '{}'),
                (2, 'sdo', 'cm:1', 2, 'assign', 'b', '2026-10-01 08:00:00',
                 '2026-10-01 08:00:00', '2026-10-01 08:00:00', 'h', 0, NULL, '{}');
            """
        )
    command.upgrade(cfg, "head")
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT slug, auto_sources FROM categories").fetchall() == [
            ("study", '["sdo"]')
        ]
        assert conn.execute(
            "SELECT channel_id FROM channel_categories ORDER BY channel_id"
        ).fetchall() == [(1,), (2,)]
        assert conn.execute("SELECT name, token_hash FROM api_keys").fetchall() == [
            ("study", None)
        ]
        assert conn.execute("SELECT item_id, received_at FROM receipts").fetchall() == [
            (1, "2026-10-02 09:00:00")
        ]
        assert conn.execute(
            "SELECT id, tracked FROM channels ORDER BY id"
        ).fetchall() == [(1, 1), (2, 0)]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(items)")}
        assert "received_at" not in columns
    command.check(cfg)
    command.downgrade(cfg, "0001")
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT id, received_at FROM items ORDER BY id"
        ).fetchall() == [(1, "2026-10-02 09:00:00"), (2, None)]
