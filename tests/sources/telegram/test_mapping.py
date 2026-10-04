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
