from functools import lru_cache

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramSettings(BaseModel):
    api_id: int
    api_hash: SecretStr
    source_ids: list[int]


class DatabaseSettings(BaseModel):
    url: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
    )

    telegram: TelegramSettings
    database: DatabaseSettings


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore
