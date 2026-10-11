from pathlib import Path

import pytest

from university_pz_parser.container import Container
from university_pz_parser.settings import ApiSettings, check_api_token
from university_pz_parser.sources.telegram.client import NotAuthorizedError


def test_weak_api_tokens_rejected():
    for bad in ("", "change-me", "short"):
        with pytest.raises(ValueError):
            check_api_token(ApiSettings(token=bad))
    check_api_token(ApiSettings(token="x" * 32))


async def test_collector_records_unauthorized_session(settings, engine, monkeypatch):
    from university_pz_parser.sources.telegram import runtime

    class Unauthorized:
        def __init__(self, _settings):
            self.client = type("C", (), {"add_event_handler": lambda *a: None})()

        async def start(self):
            raise NotAuthorizedError("run login")

        async def stop(self):
            pass

    monkeypatch.setattr(runtime, "TelegramClientManager", Unauthorized)
    with pytest.raises(NotAuthorizedError):
        await runtime.run_collector(settings)
    c = await Container.build(settings, with_sources=False)
    report = {h.source: h for h in await c.health.snapshot()}["telegram"]
    assert "login" in report.last_error
    await c.stop()


def test_collector_unit_restarts_slowly():
    unit = Path(__file__).parents[1] / "deploy" / "study-inbox-collector.service"
    text = unit.read_text()
    assert "university-pz-parser collect" in text
    assert "RestartSec=60" in text


def test_unit_has_private_tmp():
    unit = Path(__file__).parents[1] / "deploy" / "study-inbox.service"
    assert "PrivateTmp=true" in unit.read_text()
