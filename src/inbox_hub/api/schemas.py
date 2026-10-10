from datetime import datetime
from typing import Any

from pydantic import BaseModel

from inbox_hub.db.models import ApiKey, Category, Channel, Item, SourceHealth
from inbox_hub.services.feed import FeedEntry


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


class GradeOut(BaseModel):
    value: float | None = None
    max: float | None = None
    graded_at: datetime | None = None
    grader: str | None = None
    grading: str | None = None
    feedback: str | None = None


class ItemOut(BaseModel):
    id: int
    source: str
    channel: str
    categories: list[str]
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
    grade: GradeOut | None
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
    ok: bool
    last_run_at: datetime | None
    last_ok_at: datetime | None
    last_error: str | None = None
    last_report: dict[str, Any] | None = None


class CategoryOut(BaseModel):
    slug: str
    title: str
    description: str | None
    auto_sources: list[str] = []


class MeOut(BaseModel):
    name: str
    admin: bool
    categories: list[CategoryOut]


class CategoryIn(BaseModel):
    slug: str
    title: str
    description: str | None = None
    auto_sources: list[str] = []


class ChannelOut(BaseModel):
    id: int
    source: str
    external_id: str
    title: str
    tracked: bool
    categories: list[str]


class TrackChatIn(BaseModel):
    chat_id: int
    categories: list[str] = []


class KeyOut(BaseModel):
    name: str
    active: bool
    created_at: datetime
    revoked_at: datetime | None
    last_used_at: datetime | None
    categories: list[str]


class KeyIn(BaseModel):
    name: str
    categories: list[str] = []


class KeyTokenOut(BaseModel):
    name: str
    token: str


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


def item_out(entry: FeedEntry, text_limit: int | None) -> ItemOut:
    item = entry.item
    extra = dict(item.extra or {})
    skipped = extra.pop("skipped_files", [])
    grade = extra.pop("grade", None)
    return ItemOut(
        id=item.id,
        source=item.source,
        channel=item.channel.title,
        categories=entry.categories,
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
        duplicate_of_id=entry.duplicate_of_id,
        received_at=entry.received_at,
        files=[
            _file_out(k, item, text_limit) for k in item.links if k.detached_at is None
        ],
        removed_files=[
            _file_out(k, item, 0) for k in item.links if k.detached_at is not None
        ],
        skipped_files=[SkippedFileOut(**s) for s in skipped],
        grade=GradeOut(**grade) if grade else None,
        extra=extra,
    )


def health_out(row: SourceHealth, detailed: bool) -> HealthOut:
    out = HealthOut(
        source=row.source,
        ok=row.last_error is None,
        last_run_at=row.last_run_at,
        last_ok_at=row.last_ok_at,
    )
    if detailed:
        out.last_error = row.last_error
        out.last_report = row.last_report or {}
    return out


def category_out(c: Category) -> CategoryOut:
    return CategoryOut(
        slug=c.slug,
        title=c.title,
        description=c.description,
        auto_sources=c.auto_sources or [],
    )


def channel_out(c: Channel) -> ChannelOut:
    return ChannelOut(
        id=c.id,
        source=c.source,
        external_id=c.external_id,
        title=c.title,
        tracked=c.tracked,
        categories=[x.slug for x in c.categories],
    )


def key_out(k: ApiKey) -> KeyOut:
    return KeyOut(
        name=k.name,
        active=k.token_hash is not None and k.revoked_at is None,
        created_at=k.created_at,
        revoked_at=k.revoked_at,
        last_used_at=k.last_used_at,
        categories=[x.slug for x in k.categories],
    )
