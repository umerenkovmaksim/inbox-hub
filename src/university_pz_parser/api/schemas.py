from datetime import datetime
from typing import Any

from pydantic import BaseModel

from university_pz_parser.db.models import Item, SourceHealth


class FileOut(BaseModel):
    sha256: str
    name: str | None
    mime: str | None
    size: int
    url: str | None
    text: str | None
    text_status: str
    text_truncated: bool
    added_on_edit: bool


class SkippedFileOut(BaseModel):
    name: str | None
    size: int | None
    url: str | None
    reason: str


class ItemOut(BaseModel):
    id: int
    source: str
    channel: str
    kind: str
    title: str | None
    text: str
    author: str | None
    url: str | None
    published_at: datetime
    updated_at: datetime
    edited: bool
    edited_at: datetime | None
    previous_text: str | None
    due_at: datetime | None
    available_from: datetime | None
    duplicate_of_id: int | None
    received_at: datetime | None
    files: list[FileOut]
    removed_files: list[FileOut]
    skipped_files: list[SkippedFileOut]
    extra: dict[str, Any]


class ItemsOut(BaseModel):
    items: list[ItemOut]
    count: int


class ReceiveIn(BaseModel):
    ids: list[int]
    versions: dict[int, datetime] | None = None


class ReceiveOut(BaseModel):
    updated: int


class HealthOut(BaseModel):
    source: str
    last_run_at: datetime | None
    last_ok_at: datetime | None
    last_error: str | None
    last_report: dict[str, Any]


def _file_out(link, item: Item, limit: int | None) -> FileOut:
    text = link.file.text
    truncated = link.file.text_status == "truncated"
    if text is not None and limit is not None and len(text) > limit:
        text, truncated = text[:limit], True
    return FileOut(
        sha256=link.file_sha256,
        name=link.name or link.file.original_name,
        mime=link.file.mime,
        size=link.file.size,
        url=link.url,
        text=text,
        text_status=link.file.text_status,
        text_truncated=truncated,
        added_on_edit=link.attached_at > item.first_seen_at,
    )


def item_out(item: Item, text_limit: int | None) -> ItemOut:
    extra = dict(item.extra or {})
    skipped = extra.pop("skipped_files", [])
    return ItemOut(
        id=item.id,
        source=item.source,
        channel=item.channel.title,
        kind=item.kind,
        title=item.title,
        text=item.text,
        author=item.author,
        url=item.url,
        published_at=item.published_at,
        updated_at=item.updated_at,
        edited=item.edited,
        edited_at=item.edited_at,
        previous_text=item.previous_text,
        due_at=item.due_at,
        available_from=item.available_from,
        duplicate_of_id=item.duplicate_of_id,
        received_at=item.received_at,
        files=[
            _file_out(k, item, text_limit) for k in item.links if k.detached_at is None
        ],
        removed_files=[
            _file_out(k, item, 0) for k in item.links if k.detached_at is not None
        ],
        skipped_files=[SkippedFileOut(**s) for s in skipped],
        extra=extra,
    )


def health_out(row: SourceHealth) -> HealthOut:
    return HealthOut(
        source=row.source,
        last_run_at=row.last_run_at,
        last_ok_at=row.last_ok_at,
        last_error=row.last_error,
        last_report=row.last_report or {},
    )
