from datetime import datetime

import telethon
from telethon.tl.custom import Message

from university_pz_parser.settings import TelegramSettings


class TelegramParser:
    def __init__(self, settings: TelegramSettings):
        self.settings = settings
        self.client = telethon.TelegramClient(
            "session",
            self.settings.api_id,
            self.settings.api_hash.get_secret_value(),
        )

    def get_data(
        self, date_from: datetime | None = None, date_to: datetime | None = None
    ):
        pass

    def _parse_messages(
        self,
        source_ids: list[int],
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> list[Message]:
        messages = []
        with self.client:
            for source_id in source_ids:
                messages.extend(
                    self._iter_source(
                        source_id=source_id, date_from=date_from, date_to=date_to
                    )
                )
        return messages

    def _iter_source(
        self,
        source_id: int,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ):
        return filter(
            lambda message: date_to and message.date > date_to,
            [self.client.iter_messages(source_id, offset_date=date_from, reverse=True)],
        )
