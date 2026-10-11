from datetime import UTC, datetime

from telethon.tl.types import (
    Document,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    InputStickerSetEmpty,
    Message,
    MessageActionPinMessage,
    MessageMediaDocument,
    MessageService,
    PeerChannel,
    PeerUser,
)

from university_pz_parser.sources.telegram.mapping import message_link, to_incoming

DT = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
CHAT = -1002362006703


def doc(attrs, mime="application/pdf"):
    return Document(
        id=77,
        access_hash=1,
        file_reference=b"",
        date=DT,
        mime_type=mime,
        size=1000,
        dc_id=2,
        attributes=attrs,
    )


def test_text_message():
    m = Message(
        id=5,
        peer_id=PeerChannel(2362006703),
        date=DT,
        message="Сдать ПЗ1",
        from_id=PeerUser(9),
        edit_date=None,
    )
    item = to_incoming(CHAT, "ИСиТ", m)
    assert item.external_key == f"{CHAT}:5"
    assert item.kind == "text" and item.text == "Сдать ПЗ1" and item.files == ()
    assert item.url == "https://t.me/c/2362006703/5"
    assert item.extra["msg_id"] == 5


def test_document_message():
    m = Message(
        id=6,
        peer_id=PeerChannel(2362006703),
        date=DT,
        message="",
        media=MessageMediaDocument(
            document=doc([DocumentAttributeFilename("pz1.pdf")])
        ),
    )
    item = to_incoming(CHAT, "ИСиТ", m)
    assert item.kind == "document"
    f = item.files[0]
    assert (f.remote_id, f.name, f.size, f.mime) == (
        "tg:doc:77",
        "pz1.pdf",
        1000,
        "application/pdf",
    )
    assert f.fetch is not None


def test_noise_is_skipped():
    sticker = Message(
        id=7,
        peer_id=PeerChannel(1),
        date=DT,
        message="",
        media=MessageMediaDocument(
            document=doc(
                [DocumentAttributeSticker(alt="😀", stickerset=InputStickerSetEmpty())],
                "image/webp",
            )
        ),
    )
    service = MessageService(
        id=8, peer_id=PeerChannel(1), date=DT, action=MessageActionPinMessage()
    )
    empty = Message(id=9, peer_id=PeerChannel(1), date=DT, message="")
    assert to_incoming(CHAT, "x", sticker) is None
    assert to_incoming(CHAT, "x", service) is None
    assert to_incoming(CHAT, "x", empty) is None


def test_link_for_basic_group_is_none():
    assert message_link(-4000123, 1) is None


def test_link_preview_photo_is_not_a_photo_message():
    from telethon.tl.types import (
        MessageMediaWebPage,
        Photo,
        PhotoSize,
        WebPage,
    )

    photo = Photo(
        id=5,
        access_hash=1,
        file_reference=b"",
        date=DT,
        sizes=[PhotoSize(type="x", w=1, h=1, size=1)],
        dc_id=2,
    )
    page = WebPage(
        id=1, url="https://e.test", display_url="e.test", hash=0, photo=photo
    )
    m = Message(
        id=12,
        peer_id=PeerChannel(1),
        date=DT,
        message="смотри https://e.test",
        media=MessageMediaWebPage(webpage=page),
    )
    item = to_incoming(CHAT, "x", m)
    assert item.kind == "text" and item.files == ()


def _photo_message():
    from telethon.tl.types import MessageMediaPhoto, Photo, PhotoSize

    photo = Photo(
        id=55,
        access_hash=1,
        file_reference=b"\x01\x02",
        date=DT,
        sizes=[PhotoSize(type="x", w=800, h=600, size=50_000)],
        dc_id=2,
    )
    return Message(
        id=13,
        peer_id=PeerChannel(2362006703),
        date=DT,
        message="расписание",
        media=MessageMediaPhoto(photo=photo),
    )


def test_photo_is_downloaded_by_default():
    item = to_incoming(CHAT, "x", _photo_message())
    assert item.kind == "photo"
    assert item.files[0].remote_id == "tg:photo:55"


def test_kinds_outside_download_kinds_keep_metadata_only():
    item = to_incoming(CHAT, "x", _photo_message(), download_kinds={"document"})
    assert item.kind == "photo" and item.files == ()
    assert "media" in item.extra


def test_raw_is_json_serializable_message_dict():
    import json

    item = to_incoming(CHAT, "x", _photo_message())
    assert item.raw["_"] == "Message" and item.raw["id"] == 13
    assert item.raw["date"] == DT.isoformat()
    json.dumps(item.raw)  # bytes (file_reference) were encoded by Telethon


async def test_fetcher_waits_out_flood_and_retries(monkeypatch, tmp_path):
    from telethon.errors import FloodWaitError

    from university_pz_parser.sources.telegram import mapping

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(mapping.asyncio, "sleep", fake_sleep)
    calls = []

    class M:
        id = 1

        async def download_media(self, file):
            calls.append(file)
            if len(calls) == 1:
                raise FloodWaitError(request=None, capture=420)
            target = file / "x.pdf"
            target.write_bytes(b"x")
            return str(target)

    path = await mapping._fetcher(M())(tmp_path)
    assert slept == [420] and len(calls) == 2 and path.exists()
