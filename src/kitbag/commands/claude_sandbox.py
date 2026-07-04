"""`claude-sandbox` — Run Claude Code inside a locked-down Docker container so it can
work on a repo with full autonomy while it stays fenced off from your host and the
wider network.

What "safe" means here (defense in depth):
  - Claude runs as a non-root user with `--dangerously-skip-permissions`, so it has full
    control *inside* the box but cannot touch your host beyond the mounted workspace.
  - Egress firewall (iptables) drops all outbound traffic except an allowlist of domains
    (Anthropic API + the git/package hosts a coding agent needs). See --allow-domain.
  - Pushing to remotes is disabled three ways: a root-owned `settings.json` `deny` rule,
    a root-owned PreToolUse hook (exit 2), and a root-owned git `pre-push` hook — none of
    which the non-root `claude` user can edit. No git push credentials are injected either.

Two input modes:
  - Give a REPO_URL → it is cloned fresh *inside* the container (ephemeral; nothing on
    your host is touched).
  - Omit REPO_URL → the git repo you are standing in is bind-mounted read-write (uid
    matched), so Claude's edits persist for you to review and commit afterwards.

Two run modes:
  - Pass --prompt → headless one-shot (`claude -p`), prints the result and exits. It runs
    quietly by default (just the final answer); add --stream for a live activity log of
    tool calls and output as Claude works.
  - Omit --prompt → drops you into an interactive Claude session inside the container (this
    is already fully live — you watch and steer it in real time).

Each run has a name (--name, or an auto-generated one). Its Claude session history is
persisted on the host under ~/.kitbag/claude-sandbox/sessions/<name>/, so transcripts
survive the throwaway container and re-running the same name resumes that history. While a
sandbox is running you can open a second shell inside it with `--attach <name>`. Add
--notify to get a bell + macOS notification when a headless run finishes.

Requires:
    - Docker installed and the daemon running.
    - ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN in your environment (see
      `claude setup-token` for a long-lived token).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm

from kitbag.config import Settings, get_settings
from kitbag.console import console

# Container name = this prefix + the (validated) session name. Also used to discover and
# attach to running sandboxes.
CONTAINER_PREFIX = "kitbag-claude-"

# Docker container names and our session dir names share this charset; validating up front
# also keeps user input out of the osascript notification string.
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")

# ── Docker build context (generated at runtime under ~/.kitbag/claude-sandbox) ──────────
#
# We generate the whole build context from these constants rather than shipping loose
# files, so the image is reproducible wherever kitbag is installed. The image tag embeds a
# hash of this content, so editing any asset below transparently triggers a rebuild.

DOCKERFILE = """\
FROM node:22-bookworm-slim

# Tools a coding agent commonly needs, plus the firewall + privilege-drop helpers.
RUN apt-get update && apt-get install -y --no-install-recommends \\
        git ca-certificates curl jq iptables ipset dnsutils gosu \\
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI.
RUN npm install -g @anthropic-ai/claude-code

# Non-root user Claude runs as. --dangerously-skip-permissions refuses to run as root,
# and running unprivileged is what keeps a bypassed session off the host.
RUN useradd -m -s /bin/bash claude

# Enforced Claude config: root-owned and read-only, so the claude user cannot weaken it.
# deny rules and PreToolUse hooks both apply under --dangerously-skip-permissions.
RUN mkdir -p /etc/claude
COPY settings.json /etc/claude/settings.json
COPY block-push-hook.sh /etc/claude/block-push-hook.sh
RUN chown -R root:root /etc/claude \\
    && chmod 0444 /etc/claude/settings.json \\
    && chmod 0555 /etc/claude/block-push-hook.sh

# Root-owned git hook path: a pre-push hook that rejects every push, as a backstop that
# does not depend on Claude's tooling at all.
RUN mkdir -p /etc/git-hooks
COPY pre-push /etc/git-hooks/pre-push
RUN chown -R root:root /etc/git-hooks \\
    && chmod 0555 /etc/git-hooks/pre-push \\
    && git config --system core.hooksPath /etc/git-hooks

COPY init-firewall.sh /usr/local/bin/init-firewall.sh
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0555 /usr/local/bin/init-firewall.sh /usr/local/bin/entrypoint.sh

WORKDIR /workspace
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
"""

# Runs as root: firewall, optional clone, uid alignment, then drops to the claude user.
ENTRYPOINT_SH = """\
#!/usr/bin/env bash
set -euo pipefail

log() { echo "[kitbag-sandbox] $*" >&2; }

if [ "${KITBAG_FIREWALL:-1}" = "1" ]; then
    log "Restricting egress to: ${KITBAG_ALLOWED_DOMAINS:-<none>}"
    /usr/local/bin/init-firewall.sh
else
    log "Egress firewall DISABLED (--no-firewall)"
fi

# Align the claude user with the host uid/gid so edits to a bind-mounted workspace land
# with the right ownership on the host (no root-owned files left behind).
if [ -n "${KITBAG_HOST_UID:-}" ]; then
    groupmod -o -g "${KITBAG_HOST_GID}" claude 2>/dev/null || true
    usermod -o -u "${KITBAG_HOST_UID}" -g "${KITBAG_HOST_GID}" claude 2>/dev/null || true
    chown -R "${KITBAG_HOST_UID}:${KITBAG_HOST_GID}" /home/claude 2>/dev/null || true
fi

if [ -n "${KITBAG_REPO_URL:-}" ]; then
    log "Cloning ${KITBAG_REPO_URL} into /workspace"
    find /workspace -mindepth 1 -delete 2>/dev/null || true
    git clone --depth 1 ${KITBAG_REPO_BRANCH:+--branch "${KITBAG_REPO_BRANCH}"} \\
        "${KITBAG_REPO_URL}" /workspace
    chown -R claude:claude /workspace 2>/dev/null || true
fi

exec gosu claude "$@"
"""

# Drop everything outbound except DNS and the resolved allowlist IPs on 80/443.
# NOTE: the allowlist is resolved once at startup, so hosts behind rotating CDN IPs may
# occasionally miss; re-run to re-resolve. DNS (53) is left open for resolution.
INIT_FIREWALL_SH = """\
#!/usr/bin/env bash
set -euo pipefail

iptables -F
iptables -X
iptables -P INPUT DROP
iptables -P FORWARD DROP
iptables -P OUTPUT DROP

# Loopback and already-established connections.
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A INPUT  -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# DNS, needed to resolve the allowlist.
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT

IFS=',' read -ra DOMAINS <<< "${KITBAG_ALLOWED_DOMAINS:-}"
for raw in "${DOMAINS[@]}"; do
    domain="$(echo "$raw" | xargs)"
    [ -z "$domain" ] && continue
    for ip in $(dig +short "$domain" A | grep -E '^[0-9.]+$'); do
        iptables -A OUTPUT -d "$ip" -p tcp --dport 443 -j ACCEPT
        iptables -A OUTPUT -d "$ip" -p tcp --dport 80  -j ACCEPT
    done
done
"""

# PreToolUse hook. Reads the tool call on stdin; exit 2 blocks it (works in bypass mode).
BLOCK_PUSH_HOOK_SH = """\
#!/usr/bin/env bash
set -euo pipefail

command="$(cat | jq -r '.tool_input.command // ""')"

# Catch `git push`, `git -C x push`, and chained forms like `foo && git push`.
if printf '%s' "$command" | grep -Eq 'git( +[^;&|]+)* +push( |$)'; then
    echo "kitbag sandbox: 'git push' is disabled — this container cannot push to remotes." >&2
    exit 2
fi
exit 0
"""

PRE_PUSH_HOOK = """\
#!/usr/bin/env bash
echo "kitbag sandbox: pushing to remotes is disabled in this container." >&2
exit 1
"""

# Enforced Claude settings: block git push, and register the PreToolUse hook as backup.
SETTINGS_JSON = """\
{
  "permissions": {
    "deny": ["Bash(git push:*)"]
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "/etc/claude/block-push-hook.sh" }
        ]
      }
    ]
  }
}
"""

# filename → contents. Order-independent; hashed together to tag the image.
_CONTEXT_FILES: dict[str, str] = {
    "Dockerfile": DOCKERFILE,
    "entrypoint.sh": ENTRYPOINT_SH,
    "init-firewall.sh": INIT_FIREWALL_SH,
    "block-push-hook.sh": BLOCK_PUSH_HOOK_SH,
    "pre-push": PRE_PUSH_HOOK,
    "settings.json": SETTINGS_JSON,
}

IMAGE_REPO = "kitbag-claude-sandbox"


# ── Docker helpers ──────────────────────────────────────────────────────────────────────

def _print_docker_setup_help() -> None:
    console.print(
        Panel.fit(
            "[bold red]Docker is not installed or not on PATH.[/bold red]\n\n"
            "Install Docker Desktop:\n\n"
            "[bold]brew install --cask docker[/bold]\n\n"
            "Then start it and verify the daemon is running:\n\n"
            "[bold]docker info[/bold]",
            title="Missing docker",
        )
    )


def _require_docker() -> None:
    import shutil

    if shutil.which("docker") is None:
        _print_docker_setup_help()
        raise typer.Exit(1)

    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode != 0:
        console.print(
            Panel.fit(
                "[bold red]The Docker daemon is not responding.[/bold red]\n\n"
                "Start Docker Desktop (or your engine) and try again.\n\n"
                f"[dim]{probe.stderr.strip()}[/dim]",
                title="Docker not running",
            )
        )
        raise typer.Exit(1)


def _resolve_auth_env(settings: Settings) -> tuple[str, str]:
    """Return (env_var_name, value) for whichever Claude credential is configured.

    Reads from Settings, so the credential can come from a real env var *or* kitbag's .env
    files (real env var wins, then cwd .env, then ~/.kitbag/.env) — same as every other setting.
    """
    for name, value in (
        ("ANTHROPIC_API_KEY", settings.anthropic_api_key),
        ("CLAUDE_CODE_OAUTH_TOKEN", settings.claude_code_oauth_token),
    ):
        if value:
            return name, value

    console.print(
        Panel.fit(
            "[bold red]No Claude credentials found.[/bold red]\n\n"
            "Set one of these — as an environment variable, or in a [bold].env[/bold] "
            "(the current directory's, or [bold]~/.kitbag/.env[/bold]):\n\n"
            "[bold]ANTHROPIC_API_KEY=sk-ant-...[/bold]\n\n"
            "or generate a long-lived token (with [bold]claude setup-token[/bold]) and set:\n\n"
            "[bold]CLAUDE_CODE_OAUTH_TOKEN=...[/bold]",
            title="Missing credentials",
        )
    )
    raise typer.Exit(1)


def _resolve_cwd_repo_root() -> Path:
    """The git repo root of the current directory, like plain `git` resolves it."""
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        console.print(
            "[red]Not inside a git repository.[/red] "
            "Run this from a repo, or pass a REPO_URL to clone one inside the sandbox."
        )
        raise typer.Exit(1)
    return Path(result.stdout.strip())


def _write_context(context_dir: Path) -> str:
    """Write the build context to disk and return a short content hash for the image tag."""
    context_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    for name in sorted(_CONTEXT_FILES):
        content = _CONTEXT_FILES[name]
        digest.update(name.encode())
        digest.update(content.encode())
        (context_dir / name).write_text(content, encoding="utf-8")
    return digest.hexdigest()[:12]


def _image_exists(tag: str) -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
        ).returncode
        == 0
    )


def _ensure_image(context_dir: Path, rebuild: bool) -> str:
    """Build the sandbox image if missing (or forced) and return its full tag."""
    content_hash = _write_context(context_dir)
    tag = f"{IMAGE_REPO}:{content_hash}"

    if _image_exists(tag) and not rebuild:
        return tag

    console.print(f"[cyan]Building sandbox image[/cyan] {tag} [dim](one-time; cached after)[/dim]")
    build = subprocess.run(
        ["docker", "build", "-t", tag, str(context_dir)],
        text=True,
    )
    if build.returncode != 0:
        console.print("[red]Failed to build the sandbox image.[/red]")
        raise typer.Exit(build.returncode)
    return tag


# ── Session naming, discovery, notification ───────────────────────────────────────────────

def _resolve_name(name: str | None) -> str:
    """Validate an explicit --name, or mint a fresh auto-name."""
    if name is None:
        return f"sbx-{uuid.uuid4().hex[:8]}"
    if not _NAME_RE.match(name):
        console.print(
            f"[red]Invalid --name[/red] '{name}'. Use letters, digits, '.', '_' or '-' "
            "(starting with a letter or digit), up to 63 chars."
        )
        raise typer.Exit(1)
    return name


def _container_exists(container: str, *, running_only: bool) -> bool:
    args = ["docker", "ps", "--filter", f"name=^{container}$", "--format", "{{.Names}}"]
    if not running_only:
        args.insert(2, "-a")
    out = subprocess.run(args, capture_output=True, text=True).stdout
    return container in out.split()


def _running_sandboxes() -> list[str]:
    """Names (without prefix) of currently running kitbag sandboxes."""
    out = subprocess.run(
        ["docker", "ps", "--filter", f"name={CONTAINER_PREFIX}", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
    ).stdout
    return [n[len(CONTAINER_PREFIX):] for n in out.split() if n.startswith(CONTAINER_PREFIX)]


def _attach_shell(name: str) -> None:
    """Open an interactive shell inside a running sandbox (as the claude user)."""
    container = CONTAINER_PREFIX + name
    if not _container_exists(container, running_only=True):
        others = _running_sandboxes()
        hint = ("Running sandboxes: " + ", ".join(others)) if others else "No sandboxes are running."
        console.print(f"[red]No running sandbox named[/red] '{name}'. [dim]{hint}[/dim]")
        raise typer.Exit(1)
    console.print(
        f"[green]Attaching to[/green] {name} "
        "[dim](Ctrl-D to leave — the sandbox keeps running)[/dim]"
    )
    # The container's egress firewall and git push blocks apply to this shell too.
    result = subprocess.run(["docker", "exec", "-it", "-u", "claude", container, "bash", "-l"])
    raise typer.Exit(result.returncode)


def _notify(success: bool, name: str) -> None:
    """Bell + (on macOS) a desktop notification that a headless run finished."""
    status = "finished" if success else "failed"
    icon = "✅" if success else "❌"
    console.print(f"[bold]{icon} Claude sandbox '{name}' {status}.[/bold]")
    sys.stdout.write("\a")
    sys.stdout.flush()
    if sys.platform == "darwin" and shutil.which("osascript"):
        # `name` is charset-validated, so it is safe to interpolate into the script.
        script = f'display notification "Sandbox \'{name}\' {status}" with title "kitbag claude-sandbox"'
        subprocess.run(["osascript", "-e", script], capture_output=True)


# ── Live streaming of a headless run (`--stream`) ─────────────────────────────────────────
#
# With --stream we run `claude -p … --output-format stream-json --verbose`, which emits one
# JSON event per line as the agent works. We pretty-print those into a readable activity log
# so you can watch tool calls and output in real time instead of waiting for the final answer.

def _truncate(text: str, limit: int = 100) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_summary(name: str, tool_input: dict) -> str:
    """A one-line gist of a tool call, e.g. the command for Bash or the path for Edit."""
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return _truncate(value)
    return _truncate(json.dumps(tool_input)) if tool_input else ""


def _result_text(content: Any) -> str:
    """Flatten a tool_result's content (string, or a list of text blocks) to one line."""
    if isinstance(content, list):
        content = " ".join(
            block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    first_line = str(content).strip().splitlines()[0] if str(content).strip() else ""
    return _truncate(first_line)


def _print_event(line: str) -> None:
    """Render a single stream-json line. Best-effort: unknown shapes degrade gracefully.

    All model-supplied text is escaped before printing — Claude's output and shell commands
    routinely contain '[...]', which Rich would otherwise treat as (or choke on as) markup.
    """
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        console.print(line, markup=False)  # not JSON (e.g. an early log line) — print verbatim
        return

    etype = event.get("type")
    if etype == "system" and event.get("subtype") == "init":
        console.print(f"[dim]● session started[/dim] [cyan]{escape(str(event.get('model', '')))}[/cyan]")
    elif etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "text" and block.get("text", "").strip():
                console.print(escape(block["text"].strip()))
            elif block.get("type") == "tool_use":
                name = escape(str(block.get("name", "tool")))
                summary = escape(_tool_summary(block.get("name", ""), block.get("input", {})))
                console.print(f"[yellow]→ {name}[/yellow] [dim]{summary}[/dim]")
    elif etype == "user":
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_result":
                mark = "[red]✗[/red]" if block.get("is_error") else "[green]✓[/green]"
                console.print(f"  {mark} [dim]{escape(_result_text(block.get('content')))}[/dim]")
    elif etype == "result":
        if event.get("result"):
            console.print()
            console.print(escape(str(event["result"])))
        meta = []
        if (turns := event.get("num_turns")) is not None:
            meta.append(f"{turns} turns")
        if (ms := event.get("duration_ms")) is not None:
            meta.append(f"{ms / 1000:.1f}s")
        if (cost := event.get("total_cost_usd")) is not None:
            meta.append(f"${cost:.4f}")
        if meta:
            console.print("[dim]" + " · ".join(meta) + "[/dim]")


def _run_streaming(docker_cmd: list[str]) -> int:
    """Run the container, pretty-printing stream-json events live. Returns the exit code."""
    proc = subprocess.Popen(docker_cmd, stdout=subprocess.PIPE, text=True, bufsize=1)
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if line.strip():
                _print_event(line.rstrip("\n"))
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return proc.wait()


# ── Command ─────────────────────────────────────────────────────────────────────────────

def claude_sandbox_command(
    repo_url: str | None = typer.Argument(
        None,
        help="GitHub repo URL to clone *inside* the sandbox. Omit to use the current git repo.",
    ),
    prompt: str | None = typer.Option(
        None,
        "--prompt",
        "-p",
        help="Run headless with this prompt. Omit for an interactive Claude session.",
    ),
    branch: str | None = typer.Option(
        None,
        "--branch",
        "-b",
        help="Branch to check out when cloning a REPO_URL.",
    ),
    allow_domain: list[str] | None = typer.Option(
        None,
        "--allow-domain",
        help="Extra domain the container may reach (repeatable). Adds to the configured allowlist.",
    ),
    firewall: bool | None = typer.Option(
        None,
        "--firewall/--no-firewall",
        help="Restrict container egress to the allowlist. [default: from settings, SANDBOX_FIREWALL]",
    ),
    output_format: str | None = typer.Option(
        None,
        "--output-format",
        help="Headless output format: text | json | stream-json. [default: from settings]",
    ),
    stream: bool = typer.Option(
        False,
        "--stream",
        help="Headless: show a live, human-readable activity log (tool calls + output) as Claude works.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Claude model (alias or full name), e.g. sonnet. [default: from settings, SANDBOX_MODEL]",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Name for the sandbox (container + persisted history). [default: auto-generated]",
    ),
    attach: str | None = typer.Option(
        None,
        "--attach",
        help="Open a shell inside the running sandbox with this name, instead of launching a new one.",
    ),
    notify: bool = typer.Option(
        False,
        "--notify",
        help="Ring the bell / show a macOS notification when a headless run finishes.",
    ),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Force a rebuild of the sandbox Docker image.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt.",
    ),
) -> None:
    """Run Claude Code with full autonomy inside a network-restricted Docker sandbox."""
    settings = get_settings()
    firewall = settings.sandbox_firewall if firewall is None else firewall
    output_format = output_format or settings.sandbox_output_format
    model = model or settings.sandbox_model  # empty → let Claude pick its default

    _require_docker()

    # --attach short-circuits: jump into a running sandbox instead of launching one.
    if attach is not None:
        _attach_shell(attach)
        return

    auth_name, auth_value = _resolve_auth_env(settings)

    session_name = _resolve_name(name)
    container = CONTAINER_PREFIX + session_name
    if _container_exists(container, running_only=False):
        console.print(
            f"[red]A sandbox named[/red] '{session_name}' [red]already exists.[/red]\n"
            f"[dim]Attach to it with[/dim]  kitbag claude-sandbox --attach {session_name}"
            "[dim], or pick another --name.[/dim]"
        )
        raise typer.Exit(1)

    # Persisted Claude home for this session: mounted at /home/claude/.claude so transcripts
    # (and trust/onboarding state) survive the --rm container and can be resumed by re-running
    # the same --name.
    session_dir = settings.sandbox_sessions_dir / session_name
    session_dir.mkdir(parents=True, exist_ok=True)

    # Resolve the workspace: clone a URL inside the box, or bind-mount the current repo.
    repo_root: Path | None = None
    if repo_url is None:
        repo_root = _resolve_cwd_repo_root()

    domains = [d.strip() for d in settings.sandbox_allowed_domains.split(",") if d.strip()]
    domains.extend(d.strip() for d in (allow_domain or []) if d.strip())
    allowed_domains = ",".join(dict.fromkeys(domains))  # de-dupe, keep order

    interactive = prompt is None
    mode_desc = "interactive session" if interactive else "headless one-shot"
    if repo_url:
        target_desc = f"clone {repo_url}" + (f" @ {branch}" if branch else "")
    else:
        target_desc = f"{repo_root} (bind-mounted)"

    console.print(
        Panel.fit(
            f"[bold]Name:[/bold] {session_name}\n"
            f"[bold]Target:[/bold] {target_desc}\n"
            f"[bold]Mode:[/bold] {mode_desc}\n"
            f"[bold]Model:[/bold] {model or 'Claude default'}\n"
            f"[bold]Auth:[/bold] {auth_name}\n"
            f"[bold]Egress:[/bold] "
            + ("allowlist only" if firewall else "[red]unrestricted (--no-firewall)[/red]")
            + "\n[bold]Push to remotes:[/bold] blocked\n"
            f"[bold]History:[/bold] {session_dir}",
            title="Claude Sandbox",
        )
    )
    if not yes and not Confirm.ask("Launch the sandbox?", default=True):
        raise typer.Exit(0)

    tag = _ensure_image(settings.sandbox_context_dir, rebuild=rebuild)

    # Assemble `docker run`. The container is named so a second shell can --attach to it,
    # and Claude's home is persisted so history outlives the --rm container.
    docker_cmd: list[str] = ["docker", "run", "--rm", "--name", container]
    if interactive:
        docker_cmd.append("-it")

    if firewall:
        docker_cmd += ["--cap-add=NET_ADMIN", "--cap-add=NET_RAW"]

    docker_cmd += [
        "-v",
        f"{session_dir}:/home/claude/.claude",
        "-e",
        f"{auth_name}={auth_value}",
        "-e",
        f"KITBAG_FIREWALL={'1' if firewall else '0'}",
        "-e",
        f"KITBAG_ALLOWED_DOMAINS={allowed_domains}",
        "-e",
        f"KITBAG_HOST_UID={os.getuid()}",
        "-e",
        f"KITBAG_HOST_GID={os.getgid()}",
    ]

    if repo_url is not None:
        docker_cmd += ["-e", f"KITBAG_REPO_URL={repo_url}"]
        if branch:
            docker_cmd += ["-e", f"KITBAG_REPO_BRANCH={branch}"]
    else:
        assert repo_root is not None
        docker_cmd += ["-v", f"{repo_root}:/workspace"]

    docker_cmd.append(tag)

    # --stream only applies to headless runs (interactive is already a live session).
    streaming = stream and not interactive

    # The command handed to the entrypoint, run as the non-root claude user.
    claude_cmd = ["claude", "--settings", "/etc/claude/settings.json", "--dangerously-skip-permissions"]
    if model:
        claude_cmd += ["--model", model]
    if not interactive:
        claude_cmd += ["-p", prompt or ""]
        if streaming:
            # stream-json emits per-event JSON we pretty-print live; --verbose is required with it.
            claude_cmd += ["--output-format", "stream-json", "--verbose"]
        else:
            claude_cmd += ["--output-format", output_format]
    docker_cmd += claude_cmd

    console.print("[green]Starting sandbox…[/green] [dim](Ctrl-C to stop)[/dim]")
    if interactive:
        console.print(f"[dim]From another terminal:[/dim] kitbag claude-sandbox --attach {session_name}")

    returncode = _run_streaming(docker_cmd) if streaming else subprocess.run(docker_cmd).returncode

    if not interactive and notify:
        _notify(returncode == 0, session_name)
    console.print(f"[dim]Session history saved under[/dim] {session_dir}")
    if returncode != 0:
        raise typer.Exit(returncode)
