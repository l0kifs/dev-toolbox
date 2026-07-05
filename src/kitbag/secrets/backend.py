"""Backend selection for the secret store.

`get_store()` returns the right `SecretStore` for this machine, honouring the
`KITBAG_SECRETS_BACKEND` setting:

  - ``keyring`` — force the OS keychain.
  - ``file``    — force the encrypted-file vault (needs a master passphrase).
  - ``auto``    — use the keychain if a usable one is present, else the file.

This module stays free of any console I/O: passphrases are passed in by the caller
(resolved from the environment or an interactive prompt in the CLI layer), never read
here. That keeps the store usable both from commands and from the settings source, which
must never block on a prompt.
"""

from __future__ import annotations

import os

import keyring
import keyring.backends.fail

from kitbag import config
from kitbag.secrets.store import (
    EncryptedFileStore,
    KeyringStore,
    SecretError,
    SecretStore,
)


def keyring_available() -> bool:
    """Return whether the active keyring backend can actually store secrets.

    A missing/unconfigured backend resolves to `keyring.backends.fail.Keyring`, whose
    every operation raises — treat that (and anything that errors while probing) as
    unavailable so `auto` can fall back to the encrypted file.
    """
    try:
        kr = keyring.get_keyring()
    except Exception:  # pragma: no cover - defensive; get_keyring is normally total
        return False
    return not isinstance(kr, keyring.backends.fail.Keyring)


def resolve_backend() -> str:
    """Resolve the configured backend to a concrete ``"keyring"`` or ``"file"``."""
    choice = config.SECRETS_BACKEND
    if choice == "keyring":
        return "keyring"
    if choice == "file":
        return "file"
    if choice == "auto":
        return "keyring" if keyring_available() else "file"
    raise SecretError(
        f"Invalid KITBAG_SECRETS_BACKEND={choice!r}: expected auto, keyring or file."
    )


def resolve_passphrase(passphrase: str | None) -> str | None:
    """Fall back to the `KITBAG_MASTER_PASSPHRASE` env var when none is passed in."""
    return passphrase or os.environ.get(config.MASTER_PASSPHRASE_ENV) or None


def get_store(passphrase: str | None = None) -> SecretStore:
    """Build the active secret store.

    For the encrypted-file backend a passphrase is required; it's taken from the
    argument or `KITBAG_MASTER_PASSPHRASE`. Raises `SecretError` if the file backend is
    selected but no passphrase is available — callers that can prompt should catch this,
    ask the user, and retry with the passphrase supplied.
    """
    backend = resolve_backend()
    if backend == "keyring":
        return KeyringStore(config.SECRETS_SERVICE, config.SECRETS_INDEX_PATH)

    resolved = resolve_passphrase(passphrase)
    if not resolved:
        raise SecretError(
            "The encrypted-file backend needs a master passphrase. Set "
            f"{config.MASTER_PASSPHRASE_ENV} or run an interactive `kitbag secrets` command."
        )
    return EncryptedFileStore(config.SECRETS_FILE_PATH, resolved)


def try_get_store(passphrase: str | None = None) -> SecretStore | None:
    """Like `get_store` but returns ``None`` instead of raising when it can't build a
    usable store without prompting. Used by the settings source, which must stay silent
    and non-blocking during ordinary command startup."""
    try:
        return get_store(passphrase)
    except SecretError:
        return None
