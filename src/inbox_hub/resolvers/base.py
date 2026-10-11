from typing import Protocol

from inbox_hub.domain.dto import IncomingFile


class LinkResolver(Protocol):
    async def resolve(self, text: str) -> list[IncomingFile]: ...
