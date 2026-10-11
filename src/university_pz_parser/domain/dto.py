from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from university_pz_parser.enums import Source

FileFetcher = Callable[[Path], Awaitable[Path]]


@dataclass(frozen=True)
class IncomingFile:
    remote_id: str
    name: str | None
    mime: str | None = None
    size: int | None = None
    fetch: FileFetcher | None = None
    url: str | None = None


@dataclass(frozen=True)
class IncomingItem:
    source: Source
    external_key: str
    channel_external_id: str
    channel_title: str
    kind: str
    text: str
    published_at: datetime | None
    title: str | None = None
    author: str | None = None
    url: str | None = None
    edited_at: datetime | None = None
    due_at: datetime | None = None
    available_from: datetime | None = None
    files: tuple[IncomingFile, ...] = ()
    hashed_extra: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    channel_description: str | None = None
    raw: dict[str, Any] | None = None
