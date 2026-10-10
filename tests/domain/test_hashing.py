from datetime import UTC, datetime

from inbox_hub.domain.dto import IncomingItem
from inbox_hub.domain.hashing import content_hash, text_norm_hash
from inbox_hub.enums import Source


def make(**kw):
    base = dict(
        source=Source.SDO,
        external_key="cm:1",
        channel_external_id="3430",
        channel_title="МИС",
        kind="assign",
        text="Сделать ПЗ1",
        published_at=None,
    )
    base.update(kw)
    return IncomingItem(**base)


def test_hash_ignores_unhashed_extra_and_file_order():
    a = content_hash(make(extra={"submission": "draft"}), ["f2", "f1"])
    b = content_hash(make(extra={"submission": "submitted"}), ["f1", "f2"])
    assert a == b


def test_hash_changes_on_due_date_and_grade():
    base = content_hash(make(), [])
    assert content_hash(make(due_at=datetime(2026, 10, 22, tzinfo=UTC)), []) != base
    assert content_hash(make(hashed_extra={"grade": "5"}), []) != base


def test_text_norm_hash_short_text_is_none():
    assert text_norm_hash("ок") is None


def test_text_norm_hash_normalizes_whitespace_and_case():
    t = "Завтра пара переносится в аудиторию 311, начало в 10:00"
    assert text_norm_hash(t) == text_norm_hash("  " + t.upper().replace(" ", "\n "))
