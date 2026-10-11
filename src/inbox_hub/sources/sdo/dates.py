import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
PATTERN = re.compile(
    r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})(?:\s*г\.)?,?\s+(\d{1,2}):(\d{2})", re.I
)


def parse_ru_datetime(text: str, tz: ZoneInfo) -> datetime | None:
    match = PATTERN.search(text or "")
    if not match:
        return None
    day, month_name, year, hour, minute = match.groups()
    month = MONTHS.get(month_name.lower())
    if month is None:
        return None
    local = datetime(int(year), month, int(day), int(hour), int(minute), tzinfo=tz)
    return local.astimezone(UTC)
