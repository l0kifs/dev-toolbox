"""`branch-clean` — Delete local git branches that are already merged into the base
branch, or whose remote-tracking branch is gone (typically after a squash-merged or
rebase-merged PR was cleaned up on the remote).

Operates on whichever git repository you run it from (like `git` itself) — it resolves
the repo root from the current working directory.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import typer
from rich.prompt import Confirm
from rich.table import Table

from kitbag.console import console

DEFAULT_PROTECTED_BRANCHES = {"main", "master"}


class BranchCleanError(RuntimeError):
    """Raised for any recoverable failure in the branch-clean workflow."""


# ── Git helpers ────────────────────────────────────────────────────────────────

def find_repo_root() -> Path:
    """Resolve the git repository root from the current working directory,
    the same way plain `git` commands do.
    """
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        raise BranchCleanError("Not inside a git repository.")
    return Path(r.stdout.strip())


def _run_git(repo_root: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=timeout,
    )


def current_branch(repo_root: Path) -> str | None:
    r = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def list_local_branches(repo_root: Path) -> list[str]:
    r = _run_git(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def detect_base_branch(repo_root: Path, remote: str) -> str | None:
    """The remote's default branch (`origin/HEAD`), falling back to a local `main` or
    `master` if the remote HEAD ref isn't set up locally.
    """
    r = _run_git(repo_root, "symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().rsplit("/", 1)[-1]
    local = set(list_local_branches(repo_root))
    for candidate in ("main", "master"):
        if candidate in local:
            return candidate
    return None


def merged_branches(repo_root: Path, base: str) -> set[str]:
    r = _run_git(repo_root, "branch", "--merged", base, "--format=%(refname:short)")
    if r.returncode != 0:
        raise BranchCleanError(f"git branch --merged failed: {r.stderr.strip()}")
    return {line.strip() for line in r.stdout.splitlines() if line.strip()}


def gone_branches(repo_root: Path) -> set[str]:
    """Local branches whose upstream tracking branch no longer exists on the remote —
    the state git leaves behind after `git fetch --prune` sees a deleted remote branch.
    This is the common case for squash- or rebase-merged PRs, which `--merged` can't see
    since the commits never land on the local branch's own history.
    """
    r = _run_git(
        repo_root, "for-each-ref", "--format=%(refname:short)|%(upstream:track)", "refs/heads",
    )
    gone: set[str] = set()
    for line in r.stdout.splitlines():
        name, _, track = line.partition("|")
        if "[gone]" in track:
            gone.add(name.strip())
    return gone


def fetch_prune(repo_root: Path, remote: str) -> None:
    r = _run_git(repo_root, "fetch", "--prune", remote, timeout=60)
    if r.returncode != 0:
        raise BranchCleanError(f"git fetch --prune failed: {r.stderr.strip()}")


def delete_branch(repo_root: Path, name: str, *, force: bool) -> tuple[bool, str]:
    r = _run_git(repo_root, "branch", "-D" if force else "-d", name)
    return r.returncode == 0, (r.stderr.strip() or r.stdout.strip())


# ── Candidate selection ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BranchCandidate:
    name: str
    reason: str   # "merged" or "gone"
    force: bool   # True if deletion needs -D (not provably merged into base)


def gather_candidates(
    repo_root: Path, base: str | None, protected: set[str]
) -> list[BranchCandidate]:
    merged = merged_branches(repo_root, base) if base else set()
    gone = gone_branches(repo_root)
    names = sorted((merged | gone) - protected)
    return [
        BranchCandidate(name=name, reason="merged" if name in merged else "gone", force=name not in merged)
        for name in names
    ]


# ── CLI ────────────────────────────────────────────────────────────────────────

def branch_clean_command(
    remote: str = typer.Option(
        "origin", "--remote", help="Remote to check for the default branch and gone tracking branches.",
    ),
    fetch: bool = typer.Option(
        True, "--fetch/--no-fetch", help="Run `git fetch --prune` first to refresh remote-tracking info.",
    ),
    base: str | None = typer.Option(
        None, "--base",
        help="Branch merged-ness is checked against. [default: the remote's HEAD, else local main/master]",
    ),
    protect: list[str] = typer.Option(
        [], "--protect", help="Extra branch name to never delete (repeatable).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be deleted without deleting anything.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Delete local branches merged into the base branch or whose remote branch is gone."""
    try:
        repo_root = find_repo_root()
    except BranchCleanError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if fetch:
        try:
            with console.status(f"[cyan]Fetching --prune from {remote}…[/cyan]"):
                fetch_prune(repo_root, remote)
        except BranchCleanError as e:
            console.print(f"[yellow]Warning:[/yellow] {e} (continuing with local state)")

    base_branch = base or detect_base_branch(repo_root, remote)
    branch = current_branch(repo_root)

    protected = set(protect) | DEFAULT_PROTECTED_BRANCHES
    if base_branch:
        protected.add(base_branch)
    if branch:
        protected.add(branch)

    try:
        candidates = gather_candidates(repo_root, base_branch, protected)
    except BranchCleanError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    if not candidates:
        console.print("[green]Nothing to clean up.[/green] No merged or gone-upstream branches found.")
        raise typer.Exit(0)

    if not base_branch:
        console.print(
            "[yellow]No base branch detected[/yellow] (no origin/HEAD and no local main/master); "
            "only gone-upstream branches are listed. Pass --base to check merged-ness too."
        )

    table = Table(show_header=True, header_style="bold")
    table.add_column("Branch")
    table.add_column("Reason")
    table.add_column("Delete mode")
    for c in candidates:
        table.add_row(c.name, c.reason, "[yellow]force (-D)[/yellow]" if c.force else "safe (-d)")
    console.print(table)

    if dry_run:
        console.print(f"[blue]Dry run:[/blue] {len(candidates)} branch(es) would be deleted.")
        raise typer.Exit(0)

    if not yes and not Confirm.ask(f"Delete {len(candidates)} branch(es)?", default=False):
        console.print("Aborted.")
        raise typer.Exit(0)

    deleted: list[str] = []
    failed: list[tuple[str, str]] = []
    for c in candidates:
        ok, message = delete_branch(repo_root, c.name, force=c.force)
        if ok:
            deleted.append(c.name)
        else:
            failed.append((c.name, message))

    if deleted:
        console.print(f"[green]Deleted {len(deleted)} branch(es):[/green] {', '.join(deleted)}")
    if failed:
        console.print(f"[red]Failed to delete {len(failed)} branch(es):[/red]")
        for name, message in failed:
            console.print(f"  [red]{name}[/red]: {message}")
        raise typer.Exit(1)
