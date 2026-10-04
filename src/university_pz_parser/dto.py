from datetime import datetime
from typing import Any

from pydantic import BaseModel

from university_pz_parser.enums import Source


class ContentItemDTO(BaseModel):
    id: int
    source: Source
    parsed_at: datetime
    published_at: datetime
    files: list
    extra: dict[str, Any]
