import pytest

from university_pz_parser.settings import (
    ApiSettings,
    DatabaseSettings,
    SdoSettings,
    Settings,
    TelegramSettings,
)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        telegram=TelegramSettings(api_id=1, api_hash="h", source_ids=[-1001]),
        sdo=SdoSettings(url="https://sdo.test", username="u", password="p"),
        api=ApiSettings(token="secret"),
        database=DatabaseSettings(url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"),
        data_dir=tmp_path / "data",
        max_file_mb=1,
        _env_file=None,
    )
