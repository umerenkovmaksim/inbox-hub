"""Guards for the account-safety rules of the Telegram integration."""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from inbox_hub.settings import TelegramSettings
from inbox_hub.sources.telegram.client import (
    SessionLock,
    SessionLockedError,
    TelegramClientManager,
)

SRC = Path(__file__).parents[3] / "src" / "inbox_hub"

FORBIDDEN = re.compile(
    r"send_read_acknowledge|send_message|send_file|get_entity\(|ResolveUsername"
    r"|GetFullUser|GetFullChannel|get_participants|iter_participants"
    r"|get_dialogs\(|JoinChannel|ImportChatInvite|SendReaction|UpdateStatus"
)


def test_no_forbidden_telegram_calls_in_source():
    hits = [
        f"{p.relative_to(SRC)}:{n}: {line.strip()}"
        for p in SRC.rglob("*.py")
        for n, line in enumerate(p.read_text().splitlines(), 1)
        if FORBIDDEN.search(line)
    ]
    assert hits == []


def test_consumers_do_not_import_telethon():
    code = (
        "import sys\n"
        "import inbox_hub.api.app, inbox_hub.runtime\n"
        "import inbox_hub.services.feed, inbox_hub.container\n"
        "import inbox_hub.scheduler\n"
        "assert 'telethon' not in sys.modules, 'consumer imported telethon'\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_consumer_sources_do_not_mention_telethon():
    for part in ("api", "services"):
        for p in (SRC / part).rglob("*.py"):
            assert "telethon" not in p.read_text(), p


def test_session_lock_is_exclusive(tmp_path):
    path = tmp_path / "telegram.lock"
    with SessionLock(path), pytest.raises(SessionLockedError):
        SessionLock(path).acquire()
    with SessionLock(path):  # released on exit
        pass


def test_session_lock_blocks_other_processes(tmp_path):
    path = tmp_path / "telegram.lock"
    code = (
        "import sys\n"
        "from inbox_hub.sources.telegram.client import SessionLock\n"
        f"SessionLock(__import__('pathlib').Path({str(path)!r})).acquire()\n"
    )
    with SessionLock(path):
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True)
    assert proc.returncode != 0
    assert b"SessionLockedError" in proc.stderr


def test_client_parameters_are_stable_and_safe(tmp_path):
    s = TelegramSettings(
        api_id=1, api_hash="h", source_ids=[-1001], session_path=tmp_path / "tg"
    )
    client = TelegramClientManager(s).client
    assert client.flood_sleep_threshold >= 300
    assert client._connection_retries is None
    assert client._catch_up is True
    init = client._init_request
    assert (init.device_model, init.system_version, init.app_version) == (
        "Inbox Hub",
        "Linux",
        "1.0",
    )


def test_flood_threshold_below_300_is_rejected():
    with pytest.raises(ValueError):
        TelegramSettings(
            api_id=1, api_hash="h", source_ids=[], flood_sleep_threshold=60
        )
