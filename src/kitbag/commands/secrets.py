"""`secrets` — manage credentials in kitbag's local secure storage.

Secrets are stored in the OS keychain (or an encrypted file on headless machines), never
in plaintext. The app's own credentials (see `kitbag configure`) are read from here
automatically; you can also keep arbitrary secrets for your own use.

Subcommands:
    set     Store or replace a secret (value read from a hidden prompt).
    get     Print a stored secret (masked unless --reveal).
    list    List stored secret names and the active backend.
    delete  Remove a stored secret.
    import  Move known credentials out of ~/.kitbag/.env into the secure store.
"""

from __future__ import annotations

import typer
from rich.prompt import Confirm, Prompt
from rich.table import Table

from kitbag import config
from kitbag.console import console
from kitbag.secrets.migrate import migrate_env_file
from kitbag.secrets.registry import BY_NAME, KNOWN_CREDENTIALS
from kitbag.secrets.session import resolve_store
from kitbag.secrets.store import SecretError, validate_name

secrets_app = typer.Typer(
    help="Manage credentials in kitbag's local secure storage.",
    no_args_is_help=True,
)


def _fail(message: str) -> typer.Exit:
    """Print an error and return an Exit(1) for the caller to raise."""
    console.print(f"[bold red]Error:[/bold red] {message}")
    return typer.Exit(1)


@secrets_app.command("set")
def set_secret(
    name: str | None = typer.Argument(None, help="Secret name (prompted if omitted)."),
    value: str | None = typer.Option(
        None,
        "--value",
        help="Secret value. Prefer omitting this and using the hidden prompt so the "
        "value never lands in your shell history.",
    ),
) -> None:
    """Store or replace a secret. The value is read from a hidden prompt by default."""
    try:
        store = resolve_store()
        raw_name = name if name is not None else Prompt.ask("Secret name")
        secret_name = validate_name(raw_name)
        secret_value = value if value is not None else Prompt.ask(
            f"Value for {secret_name}", password=True
        )
        if not secret_value:
            raise _fail("No value provided; nothing stored.")
        store.set(secret_name, secret_value)
    except SecretError as exc:
        raise _fail(str(exc)) from exc
    console.print(f"[green]Stored[/green] {secret_name} in the {store.name} backend.")


@secrets_app.command("get")
def get_secret(
    name: str = typer.Argument(..., help="Secret name to read."),
    reveal: bool = typer.Option(
        False, "--reveal", help="Print the raw value (for piping) instead of masking it."
    ),
) -> None:
    """Print a stored secret. Masked by default; use --reveal to show the raw value."""
    try:
        store = resolve_store()
        secret_name = validate_name(name)
        value = store.get(secret_name)
    except SecretError as exc:
        raise _fail(str(exc)) from exc
    if value is None:
        raise _fail(f"{name} is not set.")
    if reveal:
        # Plain stdout, no Rich markup, so the value pipes cleanly.
        typer.echo(value)
    else:
        console.print(
            f"{secret_name} is set ({len(value)} chars). "
            "Pass [bold]--reveal[/bold] to print the value."
        )


@secrets_app.command("list")
def list_secrets() -> None:
    """List stored secret names (never values) and the active backend."""
    try:
        store = resolve_store()
        names = store.list_names()
    except SecretError as exc:
        raise _fail(str(exc)) from exc

    console.print(f"Backend: [bold]{store.name}[/bold]")
    if not names:
        console.print("[dim]No secrets stored yet.[/dim]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Used by")
    for secret_name in names:
        cred = BY_NAME.get(secret_name)
        table.add_row(
            secret_name,
            "known" if cred else "custom",
            cred.used_by if cred else "—",
        )
    console.print(table)


@secrets_app.command("delete")
def delete_secret(
    name: str = typer.Argument(..., help="Secret name to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Remove a stored secret."""
    try:
        store = resolve_store()
        secret_name = validate_name(name)
        if not yes and not Confirm.ask(f"Delete secret {secret_name}?", default=False):
            console.print("Aborted.")
            raise typer.Exit(0)
        removed = store.delete(secret_name)
    except SecretError as exc:
        raise _fail(str(exc)) from exc
    if removed:
        console.print(f"[green]Deleted[/green] {secret_name}.")
    else:
        console.print(f"[yellow]{secret_name} was not stored; nothing to delete.[/yellow]")


@secrets_app.command("import")
def import_env() -> None:
    """Move known credentials out of ~/.kitbag/.env into the secure store.

    Each migrated value is written to the store and its original line in the file is
    commented out (kept, not deleted, so the change is reversible). Env vars and project
    `.env` files are left alone.
    """
    env_path = config.USER_CONFIG_ENV
    if not env_path.exists():
        console.print(f"[yellow]No file at {env_path}; nothing to import.[/yellow]")
        return
    try:
        store = resolve_store()
        results = migrate_env_file(env_path, store)
    except SecretError as exc:
        raise _fail(str(exc)) from exc

    moved = [r.name for r in results if r.moved]
    if not moved:
        console.print(
            f"No known credentials with values found in {env_path}. "
            f"(Recognised names: {', '.join(c.name for c in KNOWN_CREDENTIALS)}.)"
        )
        return
    console.print(
        f"[green]Moved {len(moved)} credential(s)[/green] into the {store.name} backend: "
        f"{', '.join(moved)}."
    )
    console.print(f"Their lines in {env_path} were commented out (reversible).")
