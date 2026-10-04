import shutil
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from university_pz_parser.domain.dto import IncomingFile, IncomingItem
from university_pz_parser.enums import Source
from university_pz_parser.sources.telegram.mapping import message_link


def _text(raw) -> str:
    if isinstance(raw, str):
        return raw
    return "".join(
        part if isinstance(part, str) else part.get("text", "") for part in raw
    )


def _ts(value: str | None) -> datetime | None:
    return datetime.fromtimestamp(int(value), UTC) if value else None


def _local_file(base: Path, rel: str, msg_id: int) -> IncomingFile | None:
    path = base / rel
    if not path.is_file():
        return None

    async def fetch(tmp: Path) -> Path:
        target = tmp / path.name
        shutil.copyfile(path, target)
        return target

    return IncomingFile(
        remote_id=f"export:{msg_id}:{rel}",
        name=path.name,
        size=path.stat().st_size,
        fetch=fetch,
    )


def export_to_items(data: dict, chat_id: int, base: Path) -> Iterator[IncomingItem]:
    title = data.get("name") or str(chat_id)
    for m in data.get("messages", []):
        if m.get("type") != "message":
            continue
        text = _text(m.get("text", ""))
        rel = m.get("file") or m.get("photo")
        f = _local_file(base, rel, m["id"]) if rel else None
        if not text and f is None:
            continue
        yield IncomingItem(
            source=Source.TELEGRAM,
            external_key=f"{chat_id}:{m['id']}",
            channel_external_id=str(chat_id),
            channel_title=title,
            kind="photo" if m.get("photo") else "document" if rel else "text",
            text=text,
            published_at=_ts(m.get("date_unixtime")),
            edited_at=_ts(m.get("edited_unixtime")),
            author=m.get("from"),
            url=message_link(chat_id, m["id"]),
            files=(f,) if f else (),
            extra={
                "msg_id": m["id"],
                "reply_to": m.get("reply_to_message_id"),
                "forwarded_from": m.get("forwarded_from"),
                "imported": True,
            },
        )
