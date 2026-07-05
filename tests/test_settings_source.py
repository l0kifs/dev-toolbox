"""Unit tests for the SecretsSettingsSource → Settings mapping."""

from __future__ import annotations

import pytest

import kitbag.secrets.backend as backend
from kitbag.config import SecretsSettingsSource, Settings
from kitbag.secrets.store import SecretStore, validate_name

pytestmark = pytest.mark.unit


class InMemoryStore(SecretStore):
    name = "memory"

    def __init__(self, data: dict[str, str]) -> None:
        self._data = {validate_name(k): v for k, v in data.items()}

    def get(self, name):
        return self._data.get(validate_name(name))

    def set(self, name, value) -> None:
        self._data[validate_name(name)] = value

    def delete(self, name) -> bool:
        return self._data.pop(validate_name(name), None) is not None

    def list_names(self):
        return sorted(self._data)


def test_source_maps_known_credentials_only(monkeypatch) -> None:
    store = InMemoryStore(
        {
            "MINIMAX_API_KEY": "mmx",
            "ANTHROPIC_API_KEY": "",  # empty → skipped
            "MY_CUSTOM_SECRET": "nope",  # not a known credential → skipped
        }
    )
    monkeypatch.setattr(backend, "try_get_store", lambda passphrase=None: store)

    values = SecretsSettingsSource(Settings)()

    assert values == {"minimax_api_key": "mmx"}


def test_source_silent_when_store_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(backend, "try_get_store", lambda passphrase=None: None)
    assert SecretsSettingsSource(Settings)() == {}


def test_source_swallows_store_errors(monkeypatch) -> None:
    class Boom(InMemoryStore):
        def list_names(self):
            raise RuntimeError("backend exploded")

    monkeypatch.setattr(
        backend, "try_get_store", lambda passphrase=None: Boom({"MINIMAX_API_KEY": "x"})
    )
    # A misbehaving backend must not break command startup.
    assert SecretsSettingsSource(Settings)() == {}
