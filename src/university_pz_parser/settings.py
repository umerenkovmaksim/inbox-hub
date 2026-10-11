from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseModel):
    api_id: int
    api_hash: SecretStr
    source_ids: list[int]
    session_path: Path = Path("data/telegram")
    backfill_from: datetime = datetime(2026, 9, 1, tzinfo=UTC)
    device_model: str = "Study Inbox"
    system_version: str = "Linux"
    app_version: str = "1.0"
    lang_code: str = "en"
    system_lang_code: str = "en"
    flood_sleep_threshold: int = Field(300, ge=300)
    retry_delay: int = Field(10, ge=1)
    reconcile_interval_hours: float = Field(4, gt=0)
    download_kinds: set[Literal["document", "photo"]] = {"document", "photo"}

    @property
    def lock_path(self) -> Path:
        return self.session_path.with_name(self.session_path.name + ".lock")


class SdoSettings(BaseModel):
    url: str = "https://sdo.timacad.ru"
    username: str
    password: SecretStr
    exclude_course_ids: list[int] = []
    backend: Literal["html", "auto"] = "html"


class DatabaseSettings(BaseModel):
    url: str = "sqlite+aiosqlite:///data/inbox.db"


class ApiSettings(BaseModel):
    token: SecretStr
    host: str = "127.0.0.1"
    port: int = 8000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    telegram: TelegramSettings
    sdo: SdoSettings
    api: ApiSettings
    database: DatabaseSettings = DatabaseSettings()
    data_dir: Path = Path("data")
    max_file_mb: int = 50
    feed_text_limit: int = 4000
    tz: str = "Europe/Moscow"

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


MIN_TOKEN_LENGTH = 24


def check_api_token(api: ApiSettings) -> None:
    token = api.token.get_secret_value()
    if len(token) < MIN_TOKEN_LENGTH or token == "change-me":
        raise ValueError(
            f"API__TOKEN must be a random string of at least {MIN_TOKEN_LENGTH} chars"
        )
