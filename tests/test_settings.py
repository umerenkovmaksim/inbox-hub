from datetime import UTC, datetime

from university_pz_parser.settings import Settings


def test_nested_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TELEGRAM__API_ID", "1")
    monkeypatch.setenv("TELEGRAM__API_HASH", "h")
    monkeypatch.setenv("TELEGRAM__SOURCE_IDS", "[-1001, -1002]")
    monkeypatch.setenv("SDO__USERNAME", "u")
    monkeypatch.setenv("SDO__PASSWORD", "p")
    monkeypatch.setenv("SDO__EXCLUDE_COURSE_IDS", "[2309]")
    monkeypatch.setenv("API__TOKEN", "t")
    s = Settings()
    assert s.telegram.source_ids == [-1001, -1002]
    assert s.telegram.backfill_from == datetime(2026, 9, 1, tzinfo=UTC)
    assert s.sdo.exclude_course_ids == [2309]
    assert s.database.url == "sqlite+aiosqlite:///data/inbox.db"
    assert s.max_file_mb == 50
