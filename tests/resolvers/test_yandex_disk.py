import httpx
import respx

from inbox_hub.resolvers.yandex_disk import API, YandexDiskResolver


@respx.mock
async def test_file_and_dir(tmp_path):
    respx.get(API, params={"public_key": "https://disk.yandex.ru/d/AAA"}).respond(
        json={
            "type": "file",
            "name": "ПЗ1.docx",
            "mime_type": "application/x",
            "size": 4,
            "sha256": "s1",
            "file": "https://downloader.test/1",
        }
    )
    respx.get(API, params={"public_key": "https://yadi.sk/d/BBB"}).respond(
        json={
            "type": "dir",
            "name": "Лекции",
            "_embedded": {
                "items": [
                    {
                        "type": "file",
                        "name": "l1.pdf",
                        "size": 2,
                        "md5": "m1",
                        "file": "https://downloader.test/2",
                    },
                    {"type": "dir", "name": "sub"},
                ]
            },
        }
    )
    respx.get("https://downloader.test/1").respond(content=b"docx")
    async with httpx.AsyncClient() as http:
        files = await YandexDiskResolver(http).resolve(
            "см https://disk.yandex.ru/d/AAA и https://yadi.sk/d/BBB."
        )
        assert [(f.remote_id, f.name) for f in files] == [
            ("yadisk:s1", "ПЗ1.docx"),
            ("yadisk:m1", "l1.pdf"),
        ]
        path = await files[0].fetch(tmp_path)
        assert path.read_bytes() == b"docx"


@respx.mock
async def test_api_error_returns_empty():
    respx.get(API).respond(status_code=404)
    async with httpx.AsyncClient() as http:
        assert (
            await YandexDiskResolver(http).resolve("https://disk.yandex.ru/d/X") == []
        )


@respx.mock
async def test_server_error_raises_but_missing_link_is_empty():
    respx.get(API, params={"public_key": "https://disk.yandex.ru/d/GONE"}).respond(
        status_code=404
    )
    respx.get(API, params={"public_key": "https://disk.yandex.ru/d/DOWN"}).respond(
        status_code=503
    )
    async with httpx.AsyncClient() as http:
        resolver = YandexDiskResolver(http)
        assert await resolver.resolve("https://disk.yandex.ru/d/GONE") == []
        try:
            await resolver.resolve("https://disk.yandex.ru/d/DOWN")
        except httpx.HTTPStatusError:
            pass
        else:
            raise AssertionError("503 must raise")
