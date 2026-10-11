import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from university_pz_parser.settings import get_settings

ROOT = Path(__file__).parents[2]


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db = tmp_path / "inbox.db"
    for key, value in {
        "DATABASE__URL": f"sqlite+aiosqlite:///{db}",
        "TELEGRAM__API_ID": "1",
        "TELEGRAM__API_HASH": "h",
        "TELEGRAM__SOURCE_IDS": "[-1001]",
        "SDO__USERNAME": "u",
        "SDO__PASSWORD": "p",
        "API__TOKEN": "t",
    }.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield Config(str(ROOT / "alembic.ini")), db
    get_settings.cache_clear()


def test_0002_keeps_data_and_lifts_cursor(alembic_cfg):
    cfg, db = alembic_cfg
    command.upgrade(cfg, "0001")
    con = sqlite3.connect(db)
    con.executescript(
        """
        INSERT INTO channels VALUES (1, 'telegram', '-1001', 'ИСиТ', NULL, 3);
        INSERT INTO channels VALUES (2, 'telegram', '-1002', 'Импорт', NULL, 0);
        INSERT INTO channels VALUES (3, 'sdo', '42', 'Курс', NULL, 0);
        INSERT INTO items (id, source, external_key, channel_id, kind, text,
            published_at, first_seen_at, updated_at, content_hash, edited, extra)
        VALUES
          (1, 'telegram', '-1001:2', 1, 'text', 'a', '2026-10-01', '2026-10-01',
           '2026-10-01', 'h1', 0, '{}'),
          (2, 'telegram', '-1001:17', 1, 'text', 'b', '2026-10-01', '2026-10-01',
           '2026-10-01', 'h2', 0, '{}'),
          (3, 'telegram', '-1002:9', 2, 'text', 'c', '2026-10-01', '2026-10-01',
           '2026-10-01', 'h3', 0, '{}'),
          (4, 'sdo', 'cm:5', 3, 'assign', 'd', '2026-10-01', '2026-10-01',
           '2026-10-01', 'h4', 0, '{}');
        """
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(db)
    cursors = dict(con.execute("SELECT external_id, cursor FROM channels"))
    assert cursors == {"-1001": 17, "-1002": 9, "42": 0}
    rows = con.execute("SELECT id, text, raw, deleted_at FROM items ORDER BY id")
    assert list(rows) == [
        (1, "a", None, None),
        (2, "b", None, None),
        (3, "c", None, None),
        (4, "d", None, None),
    ]
    con.close()

    command.downgrade(cfg, "0001")
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM items").fetchone() == (4,)
    con.close()
