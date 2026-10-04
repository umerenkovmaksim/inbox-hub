from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from university_pz_parser.db import Base
from university_pz_parser.enums import Source


class ContentItemFile(Base):
    __tablename__ = "content_item_files"

    content_item_id: Mapped[int] = mapped_column(
        ForeignKey("content_items.id"), primary_key=True
    )
    file_id: Mapped[int] = mapped_column(ForeignKey("files.id"), primary_key=True)


class ContentItem(Base):
    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[Source]
    parsed_at: Mapped[datetime | None]
    published_at: Mapped[datetime | None]

    files: Mapped[list[File]] = relationship(secondary="content_item_files")
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)


class File(Base):
    __tablename__ = "files"

    sha256: Mapped[str] = mapped_column(primary_key=True)
