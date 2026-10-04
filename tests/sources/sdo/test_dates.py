from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from university_pz_parser.sources.sdo.dates import parse_ru_datetime

MSK = ZoneInfo("Europe/Moscow")


def test_parse_variants():
    assert parse_ru_datetime("четверг, 22 октября 2026, 00:00", MSK) == datetime(
        2026, 10, 21, 21, 0, tzinfo=UTC
    )
    assert parse_ru_datetime("7 сентября 2026, 09:00", MSK) == datetime(
        2026, 9, 7, 6, 0, tzinfo=UTC
    )
    assert parse_ru_datetime("1 мая 2026 г., 9:05", MSK) == datetime(
        2026, 5, 1, 6, 5, tzinfo=UTC
    )
    assert parse_ru_datetime("скоро", MSK) is None
