"""`temp-clone` — Clone a GitHub repo into a temporary directory, optionally open it in
VS Code, and schedule automatic cleanup.

Requires:
    - GitHub CLI (`gh`) installed and authenticated (`gh auth login`).
    - VS Code CLI (`code`) installed and on PATH, if opening with --open (the default).
    - macOS, for the launchd-based scheduled cleanup.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import typer
from rich.panel import Panel

from kitbag.config import get_settings
from kitbag.console import console


def print_gh_setup_help() -> None:
    console.print(
        Panel.fit(
            "[bold red]GitHub CLI is not installed or not available in PATH.[/bold red]\n\n"
            "Install it on macOS with Homebrew:\n\n"
            "[bold]brew install gh[/bold]\n\n"
            "Then authenticate it:\n\n"
            "[bold]gh auth login[/bold]\n\n"
            "After that, verify it works:\n\n"
            "[bold]gh auth status[/bold]",
            title="Missing gh",
        )
    )


def print_code_setup_help() -> None:
    console.print(
        Panel.fit(
            "[bold red]VS Code CLI is not installed or not available in PATH.[/bold red]\n\n"
            "If VS Code is already installed, enable the shell command:\n\n"
            "1. Open VS Code\n"
            "2. Press [bold]Cmd+Shift+P[/bold]\n"
            "3. Run: [bold]Shell Command: Install 'code' command in PATH[/bold]\n\n"
            "Then verify it works:\n\n"
            "[bold]code --version[/bold]\n\n"
            "Or install VS Code with Homebrew:\n\n"
            "[bold]brew install --cask visual-studio-code[/bold]",
            title="Missing code",
        )
    )


def print_macos_only_help() -> None:
    console.print(
        Panel.fit(
            "[bold red]launchd cleanup requires macOS.[/bold red]\n\n"
            "This utility uses macOS LaunchAgents for delayed cleanup.\n"
            "Run it on macOS, or replace the cleanup implementation for your OS.",
            title="macOS Required",
        )
    )


def require_command(command: str) -> None:
    if shutil.which(command) is not None:
        return

    if command == "gh":
        print_gh_setup_help()
    elif command == "code":
        print_code_setup_help()
    else:
        console.print(f"[red]Missing required command:[/red] {command}")

    raise typer.Exit(1)


def run_command(command: list[str], cwd: Path | None = None) -> None:
    try:
        subprocess.run(command, cwd=cwd, check=True)
    except FileNotFoundError as exc:
        console.print(f"[red]Command not found:[/red] {command[0]}")
        raise typer.Exit(1) from exc
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Command failed:[/red] {' '.join(command)}")
        raise typer.Exit(exc.returncode) from exc


def schedule_cleanup_with_launchd(path: Path, cleanup_hours: int) -> Path:
    """
    Schedule one-time cleanup through macOS launchd.

    Uses StartInterval, so cleanup runs after N seconds from load time.

    The LaunchAgent:
    - deletes the cloned repo directory
    - unloads itself
    - removes its own plist file

    Notes:
    - This is macOS-only.
    - User LaunchAgents generally run while the user is logged in.
    - If the Mac is asleep at the scheduled time, launchd usually runs the job
      when it can afterward.
    """
    if sys.platform != "darwin":
        print_macos_only_help()
        raise typer.Exit(1)

    require_command("launchctl")

    settings = get_settings()
    job_id = f"com.tempclone.cleanup.{uuid.uuid4().hex}"

    settings.launch_agents_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)

    plist_path = settings.launch_agents_dir / f"{job_id}.plist"

    quoted_path = shlex.quote(str(path))
    quoted_plist_path = shlex.quote(str(plist_path))

    cleanup_script = (
        f"/bin/rm -rf {quoted_path}\n"
        f"/bin/launchctl bootout gui/{os.getuid()} {quoted_plist_path} >/dev/null 2>&1 || true\n"
        f"/bin/launchctl unload {quoted_plist_path} >/dev/null 2>&1 || true\n"
        f"/bin/rm -f {quoted_plist_path}\n"
    )

    plist_data = {
        "Label": job_id,
        "ProgramArguments": [
            "/bin/sh",
            "-c",
            cleanup_script,
        ],
        "StartInterval": cleanup_hours * 60 * 60,
        "RunAtLoad": False,
        "StandardOutPath": str(settings.logs_dir / "clone-cleanup.log"),
        "StandardErrorPath": str(settings.logs_dir / "clone-cleanup.err"),
    }

    with plist_path.open("wb") as file:
        plistlib.dump(plist_data, file)

    try:
        run_command(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist_path)])
    except typer.Exit:
        console.print(
            "[yellow]launchctl bootstrap failed. Trying legacy launchctl load...[/yellow]"
        )
        run_command(["launchctl", "load", str(plist_path)])

    return plist_path


def temp_clone_command(
    repo_url: str = typer.Argument(
        ...,
        help="GitHub repo URL, for example https://github.com/org/repo",
    ),
    open_vscode: bool | None = typer.Option(
        None,
        "--open/--no-open",
        help="Open the cloned repo in VS Code. [default: from settings, CLONE_OPEN_VSCODE]",
    ),
    cleanup_hours: int | None = typer.Option(
        None,
        "--cleanup-hours",
        min=1,
        help="Delete the temp directory after this many hours. [default: from settings, CLONE_CLEANUP_HOURS]",
    ),
) -> None:
    """Clone a GitHub repo into a temp directory and optionally open it in VS Code."""
    settings = get_settings()
    # CLI flags override; unset falls back to the configured defaults in Settings.
    open_vscode = settings.clone_open_vscode if open_vscode is None else open_vscode
    cleanup_hours = settings.clone_cleanup_hours if cleanup_hours is None else cleanup_hours

    require_command("gh")

    if open_vscode:
        require_command("code")

    settings.clones_dir.mkdir(parents=True, exist_ok=True)

    unique_name = f"repo-{uuid.uuid4().hex[:12]}"
    clone_dir = settings.clones_dir / unique_name

    console.print(
        Panel.fit(
            f"[bold]Repo:[/bold] {repo_url}\n"
            f"[bold]Target:[/bold] {clone_dir}\n"
            f"[bold]Cleanup:[/bold] after {cleanup_hours} hours via launchd",
            title="Temp Clone",
        )
    )

    run_command(["gh", "repo", "clone", repo_url, str(clone_dir)])

    plist_path = schedule_cleanup_with_launchd(
        path=clone_dir,
        cleanup_hours=cleanup_hours,
    )

    console.print(f"[green]Cloned successfully:[/green] {clone_dir}")
    console.print(f"[yellow]Scheduled cleanup in {cleanup_hours} hours.[/yellow]")
    console.print(f"[dim]LaunchAgent: {plist_path}[/dim]")

    if open_vscode:
        run_command(["code", "--disable-workspace-trust", str(clone_dir)])
        console.print("[green]Opened in VS Code.[/green]")
    else:
        console.print("[blue]VS Code opening skipped.[/blue]")
