import logging
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin

import httpx

from inbox_hub.domain.dto import FileFetcher
from inbox_hub.settings import SdoSettings
from inbox_hub.sources.sdo import html

log = logging.getLogger(__name__)


class SdoAuthError(RuntimeError):
    pass


class SdoHttp:
    def __init__(self, http: httpx.AsyncClient, settings: SdoSettings):
        self.http = http
        self.settings = settings
        self.base = settings.url.rstrip("/")
        self.sesskey: str | None = None

    def url(self, path: str) -> str:
        return urljoin(self.base + "/", path.lstrip("/"))

    async def login(self) -> None:
        form = await self.http.get(self.url("/login/index.php"))
        token = html.parse_logintoken(form.text)
        resp = await self.http.post(
            self.url("/login/index.php"),
            data={
                "username": self.settings.username,
                "password": self.settings.password.get_secret_value(),
                "logintoken": token or "",
                "anchor": "",
            },
        )
        if html.is_login_page(resp.text):
            raise SdoAuthError("СДО login failed")
        self.sesskey = html.parse_sesskey(resp.text)
        if self.sesskey is None:
            self.sesskey = html.parse_sesskey(
                (await self.http.get(self.url("/my/"))).text
            )
        if self.sesskey is None:
            raise SdoAuthError("sesskey not found after login")

    async def page(self, path: str) -> str:
        resp = await self.http.get(self.url(path))
        if html.is_login_page(resp.text):
            await self.login()
            resp = await self.http.get(self.url(path))
            if html.is_login_page(resp.text):
                raise SdoAuthError(f"still on login page for {path}")
        resp.raise_for_status()
        return resp.text

    async def ajax(self, method: str, args: dict) -> Any:
        if self.sesskey is None:
            await self.login()
        resp = await self.http.post(
            self.url("/lib/ajax/service.php"),
            params={"sesskey": self.sesskey, "info": method},
            json=[{"index": 0, "methodname": method, "args": args}],
        )
        resp.raise_for_status()
        result = resp.json()[0]
        if result.get("error"):
            raise RuntimeError(f"{method}: {result.get('exception')}")
        return result["data"]

    async def resolve_redirect(self, path: str) -> str:
        async with self.http.stream("GET", self.url(path)) as resp:
            return str(resp.url)

    async def head_remote_id(self, url: str) -> str:
        resp = await self.http.head(url)
        resp.raise_for_status()
        etag = resp.headers.get("etag")
        if etag:
            return "etag:" + etag.removeprefix("W/").strip('"')
        if modified := resp.headers.get("last-modified"):
            return f"modified:{url}@{modified}"
        return f"url:{url}"

    def fetcher(self, url: str, name: str) -> FileFetcher:
        async def fetch(tmp: Path) -> Path:
            target = tmp / (name or unquote(url.rsplit("/", 1)[-1].split("?")[0]))
            async with self.http.stream("GET", url) as resp:
                resp.raise_for_status()
                with target.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
            return target

        return fetch
