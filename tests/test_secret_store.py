"""Unit tests for the secret-store backends and name validation."""

from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from kitbag.secrets.store import (
    EncryptedFileStore,
    KeyringStore,
    SecretError,
    validate_name,
)

pytestmark = pytest.mark.unit


# ── name validation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("minimax_api_key", "MINIMAX_API_KEY"), ("  Foo_1 ", "FOO_1")],
)
def test_validate_name_normalises(raw: str, expected: str) -> None:
    assert validate_name(raw) == expected


@pytest.mark.parametrize("bad", ["", "  ", "has space", "with-dash", "1leading", "a!b"])
def test_validate_name_rejects_malformed(bad: str) -> None:
    with pytest.raises(SecretError):
        validate_name(bad)


# ── encrypted-file backend ───────────────────────────────────────────────────


def test_encrypted_file_roundtrip(tmp_path) -> None:
    path = tmp_path / "secrets.enc"
    store = EncryptedFileStore(path, "correct horse")
    store.set("api_key", "value-1")
    store.set("OTHER", "value-2")

    assert store.get("API_KEY") == "value-1"  # names are case-insensitive
    assert store.list_names() == ["API_KEY", "OTHER"]
    # The file on disk must not contain the plaintext.
    assert "value-1" not in path.read_text(encoding="utf-8")


def test_encrypted_file_persists_across_instances(tmp_path) -> None:
    path = tmp_path / "secrets.enc"
    EncryptedFileStore(path, "pw").set("TOKEN", "abc")
    # A fresh instance with the same passphrase reads the same salt and decrypts.
    assert EncryptedFileStore(path, "pw").get("TOKEN") == "abc"


def test_encrypted_file_wrong_passphrase_raises(tmp_path) -> None:
    path = tmp_path / "secrets.enc"
    EncryptedFileStore(path, "right").set("TOKEN", "abc")
    with pytest.raises(SecretError, match="passphrase"):
        EncryptedFileStore(path, "wrong").get("TOKEN")


def test_encrypted_file_delete(tmp_path) -> None:
    store = EncryptedFileStore(tmp_path / "s.enc", "pw")
    store.set("TOKEN", "abc")
    assert store.delete("TOKEN") is True
    assert store.delete("TOKEN") is False
    assert store.get("TOKEN") is None


def test_encrypted_file_requires_passphrase(tmp_path) -> None:
    with pytest.raises(SecretError):
        EncryptedFileStore(tmp_path / "s.enc", "")


# ── keyring backend (in-memory fake) ─────────────────────────────────────────


class InMemoryKeyring(KeyringBackend):
    """A keyring backend that keeps everything in a dict, for tests."""

    priority = 1

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._data.get((service, username))

    def set_password(self, service, username, password) -> None:
        self._data[(service, username)] = password

    def delete_password(self, service, username) -> None:
        if (service, username) not in self._data:
            raise PasswordDeleteError("not found")
        del self._data[(service, username)]


@pytest.fixture
def fake_keyring():
    previous = keyring.get_keyring()
    keyring.set_keyring(InMemoryKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)


def test_keyring_store_roundtrip_and_index(tmp_path, fake_keyring) -> None:
    index = tmp_path / "index.json"
    store = KeyringStore("kitbag-test", index)
    store.set("API_KEY", "v1")
    store.set("OTHER", "v2")

    assert store.get("api_key") == "v1"
    # list_names is backed by the on-disk index, since keyring can't enumerate.
    assert store.list_names() == ["API_KEY", "OTHER"]
    assert index.exists()


def test_keyring_store_delete_updates_index(tmp_path, fake_keyring) -> None:
    store = KeyringStore("kitbag-test", tmp_path / "index.json")
    store.set("API_KEY", "v1")
    assert store.delete("API_KEY") is True
    assert store.delete("API_KEY") is False
    assert store.list_names() == []
    assert store.get("API_KEY") is None
