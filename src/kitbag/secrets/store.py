"""Secret-store abstraction and the two concrete backends.

A `SecretStore` is a flat, string→string vault scoped to this app. Two backends
implement it:

  - `KeyringStore`   — the OS keychain via `keyring`. Preferred everywhere it works.
  - `EncryptedFileStore` — an encrypted JSON blob under `~/.kitbag/`, unlocked with a
    master passphrase. The fallback for headless Linux (no Secret Service over SSH).

Backend selection lives in `backend.py`; this module only defines the contract and the
implementations. Neither backend ever logs or returns values except through `get()`.
"""

from __future__ import annotations

import base64
import builtins
import json
import os
import stat
from abc import ABC, abstractmethod
from pathlib import Path

import keyring
import keyring.errors
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# Secret names are used as keychain account names and JSON keys. Keep them to a sane,
# env-var-like shape so they round-trip cleanly through every backend and shell.
_NAME_MAX_LEN = 128


class SecretError(RuntimeError):
    """Raised for any recoverable failure in the secure-storage workflow."""


def validate_name(name: str) -> str:
    """Normalise and validate a secret name, raising `SecretError` if malformed.

    Names are upper-cased so `minimax_api_key`, `MINIMAX_API_KEY` and env vars all refer
    to the same secret. Allowed characters mirror environment-variable naming.
    """
    normalised = name.strip().upper()
    if not normalised:
        raise SecretError("Secret name must not be empty.")
    if len(normalised) > _NAME_MAX_LEN:
        raise SecretError(f"Secret name is too long (max {_NAME_MAX_LEN} characters).")
    if not all(c.isalnum() or c == "_" for c in normalised):
        raise SecretError(
            f"Invalid secret name {name!r}: use only letters, digits and underscores."
        )
    if normalised[0].isdigit():
        raise SecretError(f"Invalid secret name {name!r}: must not start with a digit.")
    return normalised


def _chmod_600(path: Path) -> None:
    """Restrict a file to owner read/write. Best-effort; silently skipped where the
    platform doesn't support POSIX permissions (e.g. some Windows filesystems)."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


class SecretStore(ABC):
    """A flat, app-scoped vault of named string secrets."""

    #: Human-readable backend name, surfaced by `kitbag secrets list`.
    name: str

    @abstractmethod
    def get(self, name: str) -> str | None:
        """Return the secret value, or ``None`` if it isn't stored."""

    @abstractmethod
    def set(self, name: str, value: str) -> None:
        """Store (or overwrite) a secret."""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Remove a secret. Returns ``True`` if it existed, ``False`` otherwise."""

    @abstractmethod
    def list_names(self) -> list[str]:
        """Return the sorted names of all stored secrets (never their values)."""


# ── Keyring backend ──────────────────────────────────────────────────────────────


class KeyringStore(SecretStore):
    """OS-keychain backend.

    `keyring` can store and fetch by (service, account) but cannot *enumerate* accounts,
    so we keep a names-only index file next to the config. The index holds no values; it
    exists purely so `list` and bulk operations know what to ask the keychain for.
    """

    name = "keyring"

    def __init__(self, service_name: str, index_path: Path) -> None:
        self._service = service_name
        self._index_path = index_path

    def _load_index(self) -> builtins.set[str]:
        try:
            raw = self._index_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return set()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return set()
        return {str(n) for n in data} if isinstance(data, list) else set()

    def _save_index(self, names: builtins.set[str]) -> None:
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps(sorted(names), indent=2) + "\n", encoding="utf-8"
        )
        _chmod_600(self._index_path)

    def get(self, name: str) -> str | None:
        name = validate_name(name)
        try:
            return keyring.get_password(self._service, name)
        except keyring.errors.KeyringError as exc:  # pragma: no cover - backend-specific
            raise SecretError(f"Keychain read failed for {name}: {exc}") from exc

    def set(self, name: str, value: str) -> None:
        name = validate_name(name)
        try:
            keyring.set_password(self._service, name, value)
        except keyring.errors.KeyringError as exc:  # pragma: no cover - backend-specific
            raise SecretError(f"Keychain write failed for {name}: {exc}") from exc
        index = self._load_index()
        index.add(name)
        self._save_index(index)

    def delete(self, name: str) -> bool:
        name = validate_name(name)
        existed = True
        try:
            keyring.delete_password(self._service, name)
        except keyring.errors.PasswordDeleteError:
            existed = False
        except keyring.errors.KeyringError as exc:  # pragma: no cover - backend-specific
            raise SecretError(f"Keychain delete failed for {name}: {exc}") from exc
        index = self._load_index()
        if name in index:
            index.discard(name)
            self._save_index(index)
        return existed

    def list_names(self) -> list[str]:
        return sorted(self._load_index())


# ── Encrypted-file backend ───────────────────────────────────────────────────────


class EncryptedFileStore(SecretStore):
    """Passphrase-encrypted JSON vault, the fallback when no OS keychain is usable.

    On-disk shape (``~/.kitbag/secrets.enc``)::

        {"salt": <b64>, "data": <Fernet token over JSON {name: value}>}

    The Fernet key is derived from the master passphrase with scrypt over the stored
    salt, so the same passphrase reproduces the key without persisting it anywhere.
    """

    name = "encrypted-file"

    # scrypt cost parameters — deliberately expensive to slow brute-forcing of the file.
    _SCRYPT_N = 2**15
    _SCRYPT_R = 8
    _SCRYPT_P = 1
    _SALT_BYTES = 16

    def __init__(self, path: Path, passphrase: str) -> None:
        if not passphrase:
            raise SecretError("A master passphrase is required for the encrypted store.")
        self._path = path
        self._passphrase = passphrase.encode("utf-8")

    def _derive_key(self, salt: bytes) -> bytes:
        kdf = Scrypt(
            salt=salt, length=32, n=self._SCRYPT_N, r=self._SCRYPT_R, p=self._SCRYPT_P
        )
        return base64.urlsafe_b64encode(kdf.derive(self._passphrase))

    def _read_all(self) -> dict[str, str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            blob = json.loads(raw)
            salt = base64.b64decode(blob["salt"])
            token = blob["data"].encode("utf-8")
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            raise SecretError(f"Secrets file {self._path} is corrupt: {exc}") from exc
        key = self._derive_key(salt)
        try:
            plaintext = Fernet(key).decrypt(token)
        except InvalidToken as exc:
            raise SecretError(
                "Could not decrypt the secrets file — wrong master passphrase?"
            ) from exc
        data = json.loads(plaintext)
        return {str(k): str(v) for k, v in data.items()}

    def _write_all(self, data: dict[str, str]) -> None:
        # Reuse the existing salt when present so the passphrase stays consistent across
        # writes; only mint a fresh salt when creating the file for the first time.
        salt: bytes | None = None
        if self._path.exists():
            try:
                salt = base64.b64decode(json.loads(self._path.read_text("utf-8"))["salt"])
            except (json.JSONDecodeError, KeyError, ValueError, OSError):
                salt = None
        if salt is None:
            salt = os.urandom(self._SALT_BYTES)
        key = self._derive_key(salt)
        token = Fernet(key).encrypt(json.dumps(data).encode("utf-8")).decode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"salt": base64.b64encode(salt).decode("ascii"), "data": token})
            + "\n",
            encoding="utf-8",
        )
        _chmod_600(self._path)

    def get(self, name: str) -> str | None:
        return self._read_all().get(validate_name(name))

    def set(self, name: str, value: str) -> None:
        name = validate_name(name)
        data = self._read_all()
        data[name] = value
        self._write_all(data)

    def delete(self, name: str) -> bool:
        name = validate_name(name)
        data = self._read_all()
        if name not in data:
            return False
        del data[name]
        self._write_all(data)
        return True

    def list_names(self) -> list[str]:
        return sorted(self._read_all())
