from telethon import TelegramClient

from inbox_hub.settings import TelegramSettings


class NotAuthorizedError(RuntimeError):
    pass


class TelegramClientManager:
    def __init__(self, settings: TelegramSettings):
        settings.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(
            str(settings.session_path),
            settings.api_id,
            settings.api_hash.get_secret_value(),
        )

    async def connect(self) -> bool:
        await self.client.connect()
        return await self.client.is_user_authorized()

    async def start(self) -> None:
        if not await self.connect():
            raise NotAuthorizedError("run: inbox-hub login")
        await self.client.get_dialogs()

    async def stop(self) -> None:
        await self.client.disconnect()
