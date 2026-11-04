"""Test setup: offline, readonly cache, fake keys, isolated data dir.

- PUNCHWALK_CACHE=readonly; reads come from a per-test tmp cache dir + fixtures/cache.
- DATA_DIR is a tmp dir, so the cost ledger never touches data/.
- SLACK_WEBHOOK_URL, RESEND_API_KEY and EMAIL_FROM are blank, whatever the local .env says.
- Real network is blocked at the socket layer. respx mocks work because they intercept
  before a socket opens. Tests that need to record into the tmp cache use `readwrite`.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from punchwalk.config import REPO_ROOT, reset_settings

FIXTURES = REPO_ROOT / "fixtures"
PROBE = FIXTURES / "probe"


class NetworkBlocked(RuntimeError):
    pass


_real_connect = socket.socket.connect
_real_create_connection = socket.create_connection


def _guard_connect(self, address, *args, **kwargs):
    if self.family == socket.AF_UNIX:
        return _real_connect(self, address, *args, **kwargs)
    host = address[0] if isinstance(address, tuple) else address
    if host in ("127.0.0.1", "::1", "localhost"):
        return _real_connect(self, address, *args, **kwargs)
    raise NetworkBlocked(f"tests are offline: blocked connection to {address!r} (mock it with respx)")


def _guard_create_connection(address, *args, **kwargs):
    host = address[0]
    if host in ("127.0.0.1", "::1", "localhost"):
        return _real_create_connection(address, *args, **kwargs)
    raise NetworkBlocked(f"tests are offline: blocked connection to {address!r} (mock it with respx)")


@pytest.fixture(autouse=True, scope="session")
def _block_network():
    socket.socket.connect = _guard_connect  # type: ignore[method-assign]
    socket.create_connection = _guard_create_connection  # type: ignore[assignment]
    yield
    socket.socket.connect = _real_connect  # type: ignore[method-assign]
    socket.create_connection = _real_create_connection  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _offline_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    monkeypatch.setenv("PUNCHWALK_CACHE", "readonly")
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("PUNCHWALK_CACHE_DIR", str(data / "cache"))
    monkeypatch.setenv("FIXTURE_CACHE_DIR", str(FIXTURES / "cache"))
    monkeypatch.setenv("MODEL_API_KEY", "test-meta-key")
    monkeypatch.setenv("BASETEN_API_KEY", "test-baseten-key")
    monkeypatch.setenv("REASONING_PROVIDER", "baseten")
    monkeypatch.setenv("COST_CAP_USD", "25")
    # Never pick up a real Slack webhook / Resend key from the local .env (env vars win over .env);
    # tests that need a channel set their own fake value.
    for name in ("SLACK_WEBHOOK_URL", "RESEND_API_KEY", "EMAIL_FROM"):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr("time.sleep", lambda s: None)
    reset_settings()
    _reset_client_state()
    yield
    _reset_client_state()
    reset_settings()


def _reset_client_state() -> None:
    from punchwalk.clients import calls, llm, sam

    calls.clear_sinks()
    llm._format_mode.clear()
    llm._resolved_model.clear()
    llm._dead_models.clear()
    sam.IMAGE_INPUT_MODE = "data_url"


@pytest.fixture
def readwrite(monkeypatch: pytest.MonkeyPatch):
    """Let a test record into its tmp cache (still offline: use respx)."""
    monkeypatch.setenv("PUNCHWALK_CACHE", "readwrite")
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def no_fixture_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Hide fixtures/cache so mocked calls are not answered by recorded entries."""
    empty = tmp_path / "empty_fixture_cache"
    empty.mkdir()
    monkeypatch.setenv("FIXTURE_CACHE_DIR", str(empty))
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def ledger_rows():
    from punchwalk.clients import cost

    return cost.read_ledger
