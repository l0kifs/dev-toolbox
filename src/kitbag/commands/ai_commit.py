"""`ai-commit` — Generate a Conventional Commits message for staged changes via MiniMax,
review/edit it interactively, then create the commit.

Operates on whichever git repository you run it from (like `git` itself) — it resolves
the repo root from the current working directory.

Requires:
    - Staged changes (`git add ...`) in the current repository.
    - MINIMAX_API_KEY set in the environment or a local .env file.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import typer
from rich.panel import Panel
from rich.prompt import Prompt

from kitbag.config import get_settings
from kitbag.console import console

try:
    # Rich's Prompt/Console.input() only get arrow-key/backspace line editing
    # when readline has been loaded first — see rich.console.Console.input().
    import readline  # noqa: F401
except ImportError:  # pragma: no cover - not available on some Windows setups
    pass

# The Conventional Commits type vocabulary — part of the prompt spec, not a tunable.
# Every configurable value (model, budgets, timeouts, retries) lives in Settings.
COMMIT_TYPES = (
    "feat", "fix", "docs", "style", "refactor", "perf", "test",
    "build", "ci", "chore", "revert",
)


class CommitError(RuntimeError):
    """Raised for any recoverable failure in the commit workflow."""


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
        raise CommitError("Not inside a git repository.")
    return Path(r.stdout.strip())


def _run_git(repo_root: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=repo_root, capture_output=True, text=True, timeout=timeout,
    )


def staged_files(repo_root: Path) -> list[str]:
    r = _run_git(repo_root, "diff", "--cached", "--name-only")
    if r.returncode != 0:
        raise CommitError(f"git diff failed: {r.stderr.strip()}")
    return [line for line in r.stdout.splitlines() if line.strip()]


def staged_diff(repo_root: Path) -> str:
    r = _run_git(repo_root, "diff", "--cached")
    if r.returncode != 0:
        raise CommitError(f"git diff failed: {r.stderr.strip()}")
    return r.stdout


def staged_file_diff(repo_root: Path, path: str) -> str:
    r = _run_git(repo_root, "diff", "--cached", "--", path)
    return r.stdout if r.returncode == 0 else ""


def staged_numstat(repo_root: Path) -> dict[str, tuple[str, str]]:
    """Map path -> (insertions, deletions) for staged files, for overflow summaries."""
    r = _run_git(repo_root, "diff", "--cached", "--numstat")
    stats: dict[str, tuple[str, str]] = {}
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            insertions, deletions, path = parts
            stats[path] = (insertions, deletions)
    return stats


def current_branch(repo_root: Path) -> str | None:
    r = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    return r.stdout.strip() if r.returncode == 0 else None


def commit_with_message(repo_root: Path, message: str) -> str:
    r = subprocess.run(
        ["git", "commit", "-F", "-"],
        cwd=repo_root, input=message, capture_output=True, text=True, timeout=60,
    )
    if r.returncode != 0:
        raise CommitError(f"git commit failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


# ── MiniMax API helpers ────────────────────────────────────────────────────────

def make_client(api_key: str) -> httpx.Client:
    settings = get_settings()
    return httpx.Client(
        base_url=settings.minimax_api_base,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=settings.minimax_api_timeout,
    )


def _post(client: httpx.Client, path: str, payload: dict, retries: int | None = None) -> dict:
    if retries is None:
        retries = get_settings().minimax_http_retries
    delay = 5
    for attempt in range(retries):
        try:
            resp = client.post(path, json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {429, 502, 503, 504, 529} and attempt < retries - 1:
                console.print(f"[yellow]retry in {delay}s (HTTP {e.response.status_code})[/yellow]")
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise CommitError("Exhausted retries calling MiniMax API")


def count_tokens(client: httpx.Client, content: str, instructions: str) -> int:
    """Estimate input tokens without invoking the model (free, not rate-limited)."""
    data = _post(client, "/responses/input_tokens", {
        "model":        get_settings().minimax_model,
        "input":        content,
        "instructions": instructions,
    })
    return data["input_tokens"]


def _strip_outer_fence(text: str) -> str:
    """Remove an outer ```/```text wrapper the model sometimes adds."""
    stripped = text.strip()
    lines = stripped.splitlines()
    if not lines or not re.match(r"^```(?:\w+)?\s*$", lines[0]):
        return stripped
    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _ensure_complete_response(data: dict) -> None:
    status = str(data.get("status", "")).lower()
    if status in {"failed", "error"}:
        raise CommitError(f"MiniMax error: {data.get('error')}")
    if status in {"incomplete", "cancelled"}:
        raise CommitError(
            f"MiniMax response incomplete: {data.get('incomplete_details') or data.get('error')}"
        )


def call_model(client: httpx.Client, instructions: str, content: str) -> str:
    settings = get_settings()
    data = _post(client, "/responses", {
        "model":             settings.minimax_model,
        "input":             content,
        "instructions":      instructions,
        "max_output_tokens": settings.minimax_max_output_tokens,
        "reasoning":         {"effort": settings.minimax_reasoning_effort},
        "temperature":       0.7,
        "top_p":             0.95,
    })
    _ensure_complete_response(data)
    return _strip_outer_fence(data.get("output_text", ""))


# ── Diff content builder ──────────────────────────────────────────────────────

def _file_block(path: str, patch: str) -> str:
    return f"### {path}\n```diff\n{patch}\n```\n\n"


def build_diff_content(
    client: httpx.Client, instructions: str, files: list[str], repo_root: Path
) -> str:
    """Return diff content for the model, falling back to per-file stats if the full
    diff exceeds the token budget. Never drops a file silently — files that don't fit
    are still listed with their +insertions/-deletions instead of a full patch.
    """
    token_budget = get_settings().minimax_token_budget
    full_diff = staged_diff(repo_root)
    content = _file_block("full staged diff", full_diff)
    if count_tokens(client, content, instructions) <= token_budget:
        return content

    console.print(
        f"[yellow]Staged diff is large; including full patches within the "
        f"{token_budget}-token budget and stats-only for the rest.[/yellow]"
    )
    return _budgeted_diff_content(files, repo_root)


def _budgeted_diff_content(files: list[str], repo_root: Path) -> str:
    settings = get_settings()
    stats = staged_numstat(repo_root)
    budget_chars = int(settings.minimax_token_budget * settings.minimax_chars_per_token)
    included: list[str] = []
    overflow: list[str] = []
    used_chars = 0

    for path in files:
        block = _file_block(path, staged_file_diff(repo_root, path))
        if included and used_chars + len(block) > budget_chars:
            overflow.append(path)
            continue
        included.append(block)
        used_chars += len(block)

    parts = ["### Staged file patches (within token budget)\n\n", *included]
    if overflow:
        parts.append("### Additional staged files (patch omitted — stats only)\n")
        for path in overflow:
            insertions, deletions = stats.get(path, ("?", "?"))
            parts.append(f"- `{path}` (+{insertions}/-{deletions})\n")
    return "".join(parts)


# ── Prompt ─────────────────────────────────────────────────────────────────────

COMMIT_INSTRUCTIONS = f"""\
You are a senior software engineer writing a git commit message for the staged \
changes provided below, following the Conventional Commits specification \
(https://www.conventionalcommits.org).

Format exactly as:

<type>[optional scope]: <short imperative summary, at most 72 characters>

<body: 1-3 short paragraphs or bullet points summarizing what changed>

Business Impact:
<a dedicated block, on its own paragraph, with 1-2 sentences on the likely \
business/user impact of this change, inferred only from the diff content shown \
— e.g. what breaks, what improves, who is affected>

Rules:
- <type> must be one of: {", ".join(COMMIT_TYPES)}.
- Base every claim strictly on the diff provided — never invent file names, \
features, or business impact not evidenced by the changes.
- Use imperative mood ("add", "fix", "remove"), not past tense.
- If changes span unrelated concerns, pick the dominant type/scope and mention \
the rest in the body.
- The "Business Impact:" block is MANDATORY and must appear in every commit \
message, as its own paragraph with the header on its own line. Never omit it. \
If the change is purely internal (tests, tooling, refactors with no behavior \
change), write "No direct business/user impact; internal-only change." under \
the header rather than skipping the block.
- Return ONLY the commit message text — no preamble, no markdown code fences, \
no commentary.
"""

REVISION_ADDENDUM = """\

You are revising the previous draft below using explicit feedback from the commit's \
author (the "### User feedback on the previous draft" section). Treat that feedback \
as a direct instruction to apply, not as a mistake to correct — apply it precisely \
and literally, even if it is informal or stylistically unconventional. Where the \
feedback conflicts with a formatting rule above, the feedback wins, EXCEPT you must \
still keep the <type>[scope]: header line and the "Business Impact:" block.
"""


_BUSINESS_IMPACT_RE = re.compile(r"(?im)^business impact\s*:")

_FALLBACK_BUSINESS_IMPACT = (
    "Business Impact:\n"
    "Not assessed by the generator — review the diff and describe the business/user impact before committing."
)


def _ensure_business_impact_block(message: str) -> str:
    """Guarantee a dedicated Business Impact block is present, regardless of model compliance."""
    if _BUSINESS_IMPACT_RE.search(message):
        return message
    return f"{message.rstrip()}\n\n{_FALLBACK_BUSINESS_IMPACT}"


@dataclass(frozen=True)
class CommitContext:
    """Diff content gathered once per commit, reused across regeneration rounds
    so revising a message never has to re-walk or re-budget the staged diff."""
    diff_content: str
    files: list[str]
    branch: str | None


def build_prompt_content(
    diff_content: str,
    files: list[str],
    branch: str | None,
    *,
    previous_message: str | None = None,
    guidance: str | None = None,
) -> str:
    branch_line = f"Current branch: {branch}\n" if branch else ""
    file_list = "\n".join(f"- {f}" for f in files)
    content = f"{branch_line}Staged files ({len(files)}):\n{file_list}\n\n{diff_content}"
    if previous_message and guidance:
        content += (
            "\n\n### Previous draft commit message\n"
            f"{previous_message}\n\n"
            "### User feedback on the previous draft\n"
            f"{guidance}\n\n"
            "Revise the commit message to address this feedback, while still following "
            "the format and rules above."
        )
    return content


def build_commit_context(client: httpx.Client, repo_root: Path) -> CommitContext:
    files = staged_files(repo_root)
    if not files:
        raise CommitError("No staged changes found. Stage files with `git add` first.")

    diff_content = build_diff_content(client, COMMIT_INSTRUCTIONS, files, repo_root)
    return CommitContext(diff_content=diff_content, files=files, branch=current_branch(repo_root))


def generate_commit_message(client: httpx.Client, context: CommitContext) -> str:
    content = build_prompt_content(context.diff_content, context.files, context.branch)
    message = call_model(client, COMMIT_INSTRUCTIONS, content)
    if not message.strip():
        raise CommitError("MiniMax returned an empty commit message.")
    return _ensure_business_impact_block(message.strip())


def regenerate_commit_message(
    client: httpx.Client, context: CommitContext, previous_message: str, guidance: str
) -> str:
    content = build_prompt_content(
        context.diff_content, context.files, context.branch,
        previous_message=previous_message, guidance=guidance,
    )
    message = call_model(client, COMMIT_INSTRUCTIONS + REVISION_ADDENDUM, content)
    if not message.strip():
        raise CommitError("MiniMax returned an empty commit message.")
    return _ensure_business_impact_block(message.strip())


# ── Interactive review ────────────────────────────────────────────────────────

def render_message(message: str) -> None:
    console.print(Panel(message, title="Generated commit message", border_style="cyan"))


def review_loop(client: httpx.Client, context: CommitContext, message: str) -> str | None:
    """Interactively accept, revise via the model, or abort. Returns the finalized
    message, or None on abort. Revising sends the previous draft plus the user's
    guidance back to the model, together with the original diff context — it never
    lets the user hand-edit the text directly.
    """
    while True:
        render_message(message)
        choice = Prompt.ask("[a]accept, [e]edit, [q]quit", choices=["a", "e", "q"], default="a")
        if choice == "a":
            return message
        if choice == "q":
            return None
        if choice == "e":
            guidance = Prompt.ask(
                "Describe what you'd like changed "
                "(e.g. \"mention the security fix\", \"make it shorter\")"
            ).strip()
            if not guidance:
                console.print("[yellow]No guidance provided; keeping the current message.[/yellow]")
                continue
            try:
                with console.status("[cyan]Revising commit message…[/cyan]"):
                    message = regenerate_commit_message(client, context, message, guidance)
            except (httpx.HTTPError, CommitError) as e:
                console.print(f"[red]Error revising commit message:[/red] {e}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def ai_commit_command() -> None:
    """Generate an AI commit message for staged changes and create the commit."""
    try:
        repo_root = find_repo_root()
    except CommitError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    api_key = get_settings().minimax_api_key
    if not api_key:
        console.print("[red]Error:[/red] MINIMAX_API_KEY not set. Add it to your environment or a .env file.")
        raise typer.Exit(1)

    try:
        if not staged_files(repo_root):
            console.print("[yellow]No staged changes.[/yellow] Stage files with `git add` first.")
            raise typer.Exit(1)
    except CommitError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1) from e

    client = make_client(api_key)
    try:
        try:
            with console.status("[cyan]Analyzing staged changes…[/cyan]"):
                context = build_commit_context(client, repo_root)
                message = generate_commit_message(client, context)
        except (httpx.HTTPError, CommitError) as e:
            console.print(f"[red]Error generating commit message:[/red] {e}")
            raise typer.Exit(1) from e

        try:
            final_message = review_loop(client, context, message)
        except KeyboardInterrupt:
            console.print("\n[yellow]Aborted.[/yellow] No commit was created.")
            raise typer.Exit(130) from None
    finally:
        client.close()

    if final_message is None:
        console.print("[yellow]Aborted.[/yellow] No commit was created.")
        raise typer.Exit(130)

    try:
        output = commit_with_message(repo_root, final_message)
    except CommitError as e:
        console.print(f"[red]Commit failed:[/red] {e}")
        raise typer.Exit(1) from e

    console.print("[green]Commit created.[/green]")
    if output:
        console.print(output)
