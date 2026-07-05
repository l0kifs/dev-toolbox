"""`configure` — first-run wizard that walks through kitbag's own credentials.

Shows each credential the built-in commands need, whether it's already stored, and lets
you set or replace it. Values go straight into the secure store (never a plaintext file).
For granular, scriptable edits use `kitbag secrets` instead.
"""

from __future__ import annotations

import typer
from rich.prompt import Confirm, Prompt

from kitbag.console import console
from kitbag.secrets.registry import KNOWN_CREDENTIALS
from kitbag.secrets.session import resolve_store
from kitbag.secrets.store import SecretError


def configure_command() -> None:
    """Interactively set up the credentials kitbag's commands need."""
    try:
        store = resolve_store()
        stored = set(store.list_names())
    except SecretError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        f"Storing credentials in the [bold]{store.name}[/bold] backend. "
        "Leave a value blank to keep the current one.\n"
    )

    updated = 0
    for cred in KNOWN_CREDENTIALS:
        status = "[green]set[/green]" if cred.name in stored else "[yellow]not set[/yellow]"
        console.print(f"[bold]{cred.name}[/bold] ({status}) — {cred.description}")
        console.print(f"  [dim]used by: kitbag {cred.used_by}[/dim]")

        prompt = "  Replace it?" if cred.name in stored else "  Set it now?"
        if not Confirm.ask(prompt, default=cred.name not in stored):
            console.print()
            continue

        value = Prompt.ask(f"  Value for {cred.name}", password=True)
        if not value:
            console.print("  [dim]Skipped (blank).[/dim]\n")
            continue
        try:
            store.set(cred.name, value)
        except SecretError as exc:
            console.print(f"  [bold red]Error:[/bold red] {exc}\n")
            continue
        updated += 1
        console.print("  [green]Saved.[/green]\n")

    console.print(
        f"Done — {updated} credential(s) updated. "
        "Run [bold]kitbag secrets list[/bold] to review what's stored."
    )
