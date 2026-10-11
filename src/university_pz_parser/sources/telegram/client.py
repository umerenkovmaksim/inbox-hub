import fcntl
import os
from pathlib import Path

from telethon import TelegramClient

from university_pz_parser.settings import TelegramSettings


class NotAuthorizedError(RuntimeError):
    pass


class SessionLockedError(RuntimeError):
    pass


class SessionLock:
    """Exclusive lock that keeps a second process off the same Telegram session."""

    def __init__(self, path: Path):
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise SessionLockedError(
                f"telegram session is used by another process ({self.path})"
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> SessionLock:
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class TelegramClientManager:
    def __init__(self, settings: TelegramSettings):
        settings.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.client = TelegramClient(
            str(settings.session_path),
            settings.api_id,
            settings.api_hash.get_secret_value(),
            device_model=settings.device_model,
            system_version=settings.system_version,
            app_version=settings.app_version,
            lang_code=settings.lang_code,
            system_lang_code=settings.system_lang_code,
            flood_sleep_threshold=settings.flood_sleep_threshold,
            connection_retries=None,
            retry_delay=settings.retry_delay,
            auto_reconnect=True,
            catch_up=True,
        )

    async def connect(self) -> bool:
        await self.client.connect()
        return await self.client.is_user_authorized()

    async def start(self) -> None:
        if not await self.connect():
            raise NotAuthorizedError("run: university-pz-parser login")

    async def stop(self) -> None:
        await self.client.disconnect()
