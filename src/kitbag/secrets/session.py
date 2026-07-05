"""Interactive helpers for unlocking the secret store from the CLI.

The core store/backend modules stay free of console I/O so they're reusable and safe to
call from the settings source. This module is the one place that may prompt: it resolves
a usable store for an interactive command, asking for the master passphrase only when the
encrypted-file backend is active and no passphrase is already in the environment.
"""

from __future__ import annotations

from rich.prompt import Prompt

from kitbag import config
from kitbag.console import console
from kitbag.secrets.backend import get_store, resolve_backend, resolve_passphrase
from kitbag.secrets.store import SecretError, SecretStore


def resolve_store() -> SecretStore:
    """Return an unlocked store for interactive use.

    Keyring needs no unlocking. For the encrypted-file backend the passphrase comes from
    `KITBAG_MASTER_PASSPHRASE` if set; otherwise the user is prompted — with confirmation
    when the vault is being created for the first time, so a typo can't lock them out.
    """
    if resolve_backend() == "keyring":
        return get_store()

    from_env = resolve_passphrase(None)
    if from_env:
        return get_store(from_env)

    if not config.SECRETS_FILE_PATH.exists():
        console.print(
            "[yellow]No encrypted secrets vault yet — creating one.[/] "
            "Choose a master passphrase; you'll need it to unlock secrets later."
        )
        first = Prompt.ask("New master passphrase", password=True)
        if not first:
            raise SecretError("Passphrase must not be empty.")
        if Prompt.ask("Confirm master passphrase", password=True) != first:
            raise SecretError("Passphrases did not match.")
        return get_store(first)

    return get_store(Prompt.ask("Master passphrase", password=True))
