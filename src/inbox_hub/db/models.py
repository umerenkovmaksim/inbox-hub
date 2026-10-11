from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    ForeignKey,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from inbox_hub.db.base import Base
from inbox_hub.db.types import UTCDateTime
from inbox_hub.enums import Source

channel_categories = Table(
    "channel_categories",
    Base.metadata,
    Column(
        "channel_id",
        ForeignKey("channels.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "category_id",
        ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    ),
)

api_key_categories = Table(
    "api_key_categories",
    Base.metadata,
    Column("key_id", ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "category_id",
        ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    auto_sources: Mapped[list[str]] = mapped_column(JSON, default=list)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    categories: Mapped[list[Category]] = relationship(
        secondary=api_key_categories, lazy="selectin", order_by=Category.slug
    )


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (UniqueConstraint("source", "external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Source] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    tracked: Mapped[bool] = mapped_column(default=False, server_default=false())

    categories: Mapped[list[Category]] = relationship(
        secondary=channel_categories, lazy="selectin", order_by=Category.slug
    )


class File(Base):
    __tablename__ = "files"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    mime: Mapped[str | None] = mapped_column(String(255))
    original_name: Mapped[str | None] = mapped_column(String(512))
    local_path: Mapped[str] = mapped_column(String(1024))
    text: Mapped[str | None] = mapped_column(Text)
    text_status: Mapped[str] = mapped_column(String(16))


class ItemFile(Base):
    __tablename__ = "item_files"

    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), primary_key=True)
    file_sha256: Mapped[str] = mapped_column(
        ForeignKey("files.sha256"), primary_key=True, index=True
    )
    remote_id: Mapped[str] = mapped_column(String(1024))
    name: Mapped[str | None] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(String(2048))
    attached_at: Mapped[datetime] = mapped_column(UTCDateTime)
    detached_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    item: Mapped[Item] = relationship(back_populates="links")
    file: Mapped[File] = relationship(lazy="joined")


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("source", "external_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Source] = mapped_column(String(16))
    external_key: Mapped[str] = mapped_column(String(128))
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(1024))
    text: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(String(2048))
    published_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    available_from: Mapped[datetime | None] = mapped_column(UTCDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)
    content_hash: Mapped[str] = mapped_column(String(64))
    text_norm_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    edited: Mapped[bool] = mapped_column(default=False)
    previous_text: Mapped[str | None] = mapped_column(Text)
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    channel: Mapped[Channel] = relationship(lazy="joined")
    links: Mapped[list[ItemFile]] = relationship(
        back_populates="item", lazy="selectin", cascade="all, delete-orphan"
    )


class Receipt(Base):
    __tablename__ = "receipts"

    key_id: Mapped[int] = mapped_column(
        ForeignKey("api_keys.id", ondelete="CASCADE"), primary_key=True
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    received_at: Mapped[datetime] = mapped_column(UTCDateTime)


class TelegramDialog(Base):
    """Snapshot of the account's groups and channels from the last reconcile."""

    __tablename__ = "telegram_dialogs"

    chat_id: Mapped[int] = mapped_column(
        BigInteger, primary_key=True, autoincrement=False
    )
    title: Mapped[str] = mapped_column(String(512))
    kind: Mapped[str] = mapped_column(String(16))
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SourceHealth(Base):
    __tablename__ = "source_health"

    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_ok_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
