import logging
import re
from pathlib import Path

import httpx

from university_pz_parser.domain.dto import IncomingFile

log = logging.getLogger(__name__)

API = "https://cloud-api.yandex.net/v1/disk/public/resources"
LINK = re.compile(r"https?://(?:disk\.yandex\.(?:ru|com)|yadi\.sk)/(?:d|i)/[\w-]+")


class YandexDiskResolver:
    def __init__(self, http: httpx.AsyncClient, max_items: int = 50):
        self.http = http
        self.max_items = max_items

    async def resolve(self, text: str) -> list[IncomingFile]:
        files: list[IncomingFile] = []
        for link in dict.fromkeys(LINK.findall(text or "")):
            try:
                resp = await self.http.get(
                    API, params={"public_key": link, "limit": 200}
                )
                resp.raise_for_status()
            except httpx.HTTPError:
                log.warning("yandex disk lookup failed for %s", link, exc_info=True)
                continue
            meta = resp.json()
            entries = (
                [meta]
                if meta.get("type") == "file"
                else meta.get("_embedded", {}).get("items", [])
            )
            for entry in entries[: self.max_items]:
                if entry.get("type") == "file" and entry.get("file"):
                    files.append(self._file(link, entry))
        return files

    def _file(self, link: str, entry: dict) -> IncomingFile:
        download = entry["file"]
        name = entry.get("name")

        async def fetch(tmp: Path) -> Path:
            target = tmp / (Path(name).name if name else "yadisk.bin")
            async with self.http.stream("GET", download, follow_redirects=True) as resp:
                resp.raise_for_status()
                with target.open("wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
            return target

        return IncomingFile(
            remote_id=f"yadisk:{entry.get('sha256') or entry.get('md5') or download}",
            name=name,
            mime=entry.get("mime_type"),
            size=entry.get("size"),
            fetch=fetch,
            url=link,
        )
