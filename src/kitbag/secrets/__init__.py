"""Secure credential storage for kitbag.

Credentials are kept out of the plaintext `.env` files and stored in an OS-native
keychain (macOS Keychain, Windows Credential Manager, Linux Secret Service) via the
`keyring` library, with an encrypted-file fallback for headless environments where no
Secret Service is available.

Public API:
    get_store()  -> SecretStore   # the active backend for this machine
    SecretStore                    # ABC: get/set/delete/list_names
    SecretError                    # raised for recoverable storage failures
    KNOWN_CREDENTIALS              # registry of the app's own credential secrets
"""

from __future__ import annotations

from kitbag.secrets.backend import get_store
from kitbag.secrets.registry import KNOWN_CREDENTIALS, KnownCredential
from kitbag.secrets.store import SecretError, SecretStore

__all__ = [
    "KNOWN_CREDENTIALS",
    "KnownCredential",
    "SecretError",
    "SecretStore",
    "get_store",
]
