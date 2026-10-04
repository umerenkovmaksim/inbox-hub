from typing import Protocol

from university_pz_parser.domain.dto import IncomingFile


class LinkResolver(Protocol):
    async def resolve(self, text: str) -> list[IncomingFile]: ...
