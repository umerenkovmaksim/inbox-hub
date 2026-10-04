import os
import re
from pathlib import Path

import httpx
from mcp.server import MCPServer

mcp = MCPServer("study-inbox")
_http: httpx.Client | None = None


def configure(base_url: str, token: str) -> None:
    global _http
    _http = httpx.Client(
        base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=60
    )


def _client() -> httpx.Client:
    if _http is None:
        configure(os.environ["INBOX_URL"], os.environ["INBOX_TOKEN"])
    return _http


def _json(resp: httpx.Response):
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def get_items(
    date_from: str,
    date_to: str,
    source: str | None = None,
    include_received: bool = False,
    limit: int = 200,
) -> dict:
    """Учебные материалы (сообщения Telegram-чатов и элементы курсов СДО), опубликованные или изменённые в окне [date_from, date_to).

    date_from/date_to — ISO 8601 с часовым поясом, например 2026-10-01T00:00:00+03:00.
    source — "telegram" или "sdo". По умолчанию возвращаются только ещё не полученные (Received) элементы.
    У изменённых элементов edited=true, есть previous_text, removed_files и files[].added_on_edit.
    Тексты файлов обрезаны до 4000 символов (text_truncated) — полный текст через get_item.
    После обработки вызови mark_received с id обработанных элементов."""
    params = {
        "from": date_from,
        "to": date_to,
        "include_received": include_received,
        "limit": limit,
    }
    if source:
        params["source"] = source
    return _json(_client().get("/items", params=params))


@mcp.tool()
def mark_received(ids: list[int]) -> dict:
    """Пометить элементы как полученные, чтобы get_items их больше не возвращал (пока они снова не изменятся)."""
    return _json(_client().post("/items/receive", json={"ids": ids}))


@mcp.tool()
def get_item(item_id: int) -> dict:
    """Полный элемент с полными текстами всех файлов."""
    return _json(_client().get(f"/items/{item_id}"))


@mcp.tool()
def get_file(sha256: str) -> str:
    """Скачать исходный файл по sha256 на этот компьютер и вернуть путь к нему."""
    resp = _client().get(f"/files/{sha256}")
    resp.raise_for_status()
    match = re.search(
        r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?',
        resp.headers.get("content-disposition", ""),
    )
    name = httpx.URL("x:/" + (match.group(1) if match else "file")).path.rsplit("/", 1)[
        -1
    ]
    target_dir = Path(
        os.environ.get("INBOX_DOWNLOAD_DIR", Path.home() / "Downloads" / "study-inbox")
    ).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{sha256}_{name}"
    target.write_bytes(resp.content)
    return str(target)


@mcp.tool()
def health() -> list:
    """Состояние источников: когда последний раз успешно синхронизировались и последняя ошибка."""
    return _json(_client().get("/health"))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
