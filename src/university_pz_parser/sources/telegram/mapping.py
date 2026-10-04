from pathlib import Path

from telethon.tl.custom import Message
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageService,
)
from telethon.utils import get_display_name

from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.enums import Source

DOWNLOADABLE = {"photo", "document"}


def message_link(chat_id: int, msg_id: int) -> str | None:
    raw = str(chat_id)
    if not raw.startswith("-100"):
        return None
    return f"https://t.me/c/{raw[4:]}/{msg_id}"


def message_kind(m) -> str | None:
    if isinstance(m, MessageService):
        return None
    if m.sticker or m.dice or m.game:
        return None
    if isinstance(m.media, MessageMediaPhoto) and m.photo:
        return "photo"
    if isinstance(m.media, MessageMediaDocument) and m.document:
        if m.voice:
            return "voice"
        if m.video_note:
            return "round"
        if m.video or m.gif:
            return "video"
        return "document"
    if m.message:
        return "text"
    return None


def _fetcher(m: Message):
    async def fetch(tmp: Path) -> Path:
        path = await m.download_media(file=tmp)
        if path is None:
            raise RuntimeError(f"telegram media {m.id} not downloaded")
        return Path(path)

    return fetch


def _file(m: Message, kind: str) -> IncomingFile:
    media_id = m.photo.id if kind == "photo" else m.document.id
    name = m.file.name or f"{kind}_{m.id}{m.file.ext or ''}"
    return IncomingFile(
        remote_id=f"tg:{'photo' if kind == 'photo' else 'doc'}:{media_id}",
        name=name,
        mime=m.file.mime_type,
        size=m.file.size,
        fetch=_fetcher(m),
    )


def _author(m) -> str | None:
    if getattr(m, "_sender", None) is not None:
        return get_display_name(m.sender) or None
    return m.post_author


def _forwarded_from(m) -> str | None:
    if m.fwd_from is None:
        return None
    if m.fwd_from.from_name:
        return m.fwd_from.from_name
    if getattr(m.forward, "_sender", None) is not None:
        return get_display_name(m.forward.sender) or None
    return str(m.fwd_from.from_id) if m.fwd_from.from_id else None


def to_incoming(chat_id: int, chat_title: str, m) -> IncomingItem | None:
    kind = message_kind(m)
    if kind is None:
        return None
    files: tuple[IncomingFile, ...] = ()
    extra = {
        "msg_id": m.id,
        "sender_id": m.sender_id,
        "reply_to": m.reply_to_msg_id,
        "forwarded_from": _forwarded_from(m),
        "grouped_id": m.grouped_id,
    }
    if kind in DOWNLOADABLE:
        files = (_file(m, kind),)
    elif m.file is not None:
        extra["media"] = {
            "name": m.file.name,
            "size": m.file.size,
            "mime": m.file.mime_type,
            "duration": m.file.duration,
        }
    return IncomingItem(
        source=Source.TELEGRAM,
        external_key=f"{chat_id}:{m.id}",
        channel_external_id=str(chat_id),
        channel_title=chat_title,
        kind=kind,
        text=m.message or "",
        published_at=m.date,
        edited_at=m.edit_date,
        author=_author(m),
        url=message_link(chat_id, m.id),
        files=files,
        extra=extra,
    )
