import hashlib
import json
from collections.abc import Iterable

from inbox_hub.domain.dto import IncomingItem

MIN_DUPLICATE_LEN = 40


def _iso(value):
    return value.isoformat() if value else None


def content_hash(item: IncomingItem, remote_ids: Iterable[str]) -> str:
    payload = {
        "title": item.title,
        "text": item.text,
        "due_at": _iso(item.due_at),
        "available_from": _iso(item.available_from),
        "files": sorted(set(remote_ids)),
        "extra": item.hashed_extra,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def normalize_text(text: str) -> str:
    return " ".join(text.lower().split())


def text_norm_hash(text: str) -> str | None:
    normalized = normalize_text(text)
    if len(normalized) < MIN_DUPLICATE_LEN:
        return None
    return hashlib.sha256(normalized.encode()).hexdigest()
