"""Application settings and on-disk layout.

This tool is meant to be installed globally (e.g. `uv tool install`) and run from any
directory, so configuration must not depend on the current working directory. All app
data lives under a single root, `~/.kitbag/` (override with `KITBAG_HOME`).

Settings are resolved in priority order:

  1. explicit constructor args,
  2. environment variables (e.g. `MINIMAX_API_KEY`),
  3. a per-project `.env` in the current directory (optional override),
  4. the user config file `~/.kitbag/.env` (the stable base).

Field names map to upper-case env vars. Both env files are optional; missing ones are
ignored. Anchoring paths at DATA_DIR keeps everything pointing at the same place no
matter where the command is invoked.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Single root for everything this tool writes: config, temp clones, logs, and
# launch-agent plists. Overridable so tests and power users can relocate it.
DATA_DIR = Path(os.environ.get("KITBAG_HOME") or (Path.home() / ".kitbag"))

# Stable, cwd-independent config file. A cwd `.env` still works and, being listed last,
# wins over this file for per-project overrides.
USER_CONFIG_ENV = DATA_DIR / ".env"

# Per-command data roots. Each command keeps its files under its own subdirectory.
TEMP_CLONE_DIR = DATA_DIR / "temp-clone"
CLAUDE_SANDBOX_DIR = DATA_DIR / "claude-sandbox"


class Settings(BaseSettings):
    """Single source of truth for every default value in the app.

    No command hardcodes tunables — they all read them from here, and each field can be
    overridden via an env var (e.g. `MINIMAX_MODEL`) or the `.env` files above. `DATA_DIR`
    itself is not a field because it must be known *before* settings load (it's where the
    config file lives); everything derived from it is a field.
    """

    model_config = SettingsConfigDict(
        env_file=(USER_CONFIG_ENV, ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── commit — MiniMax API ──────────────────────────────────────────────────
    minimax_api_key: str = ""
    minimax_api_base: str = "https://api.minimax.io/v1"
    minimax_model: str = "MiniMax-M2.7"
    minimax_token_budget: int = 100_000          # ceiling for diff content sent to the model
    minimax_max_output_tokens: int = 2_000
    minimax_reasoning_effort: str = "minimal"    # none | minimal | low | medium | high
    minimax_api_timeout: int = 120               # seconds
    minimax_http_retries: int = 4                # total attempts for 429/5xx (delays: 5s, 10s, …)
    minimax_chars_per_token: float = 3.5         # conservative char→token estimate

    # ── temp-clone — temp clones & scheduled cleanup ──────────────────────────
    clone_open_vscode: bool = True               # open the clone in VS Code by default
    clone_cleanup_hours: int = 12                # delete the temp clone after this many hours
    clones_dir: Path = TEMP_CLONE_DIR / "clones"
    logs_dir: Path = TEMP_CLONE_DIR / "logs"
    launch_agents_dir: Path = TEMP_CLONE_DIR / "launch-agents"

    # ── claude-sandbox — dockerized, egress-restricted Claude Code ─────────────
    # Claude Code's own credentials, passed into the container so Claude can authenticate.
    # These field names map to Claude's env vars (ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN),
    # so a real env var wins, then a cwd .env, then ~/.kitbag/.env. Set whichever you use.
    anthropic_api_key: str = ""
    claude_code_oauth_token: str = ""
    # Domains the container may reach when the egress firewall is on. Everything else is
    # dropped. Kept deliberately tight: Anthropic API + the git/package hosts a coding
    # agent normally needs. Comma-separated; extend via env or the --allow-domain flag.
    sandbox_allowed_domains: str = (
        "api.anthropic.com,claude.ai,statsig.anthropic.com,sentry.io,"
        "github.com,api.github.com,codeload.github.com,objects.githubusercontent.com,"
        "raw.githubusercontent.com,registry.npmjs.org,pypi.org,files.pythonhosted.org"
    )
    sandbox_firewall: bool = True                # restrict container egress to the allowlist
    sandbox_model: str = ""                       # Claude model (alias or full id); "" = Claude's default
    sandbox_output_format: str = "text"          # headless --output-format: text | json | stream-json
    sandbox_context_dir: Path = CLAUDE_SANDBOX_DIR / "context"    # generated docker build context
    sandbox_sessions_dir: Path = CLAUDE_SANDBOX_DIR / "sessions"  # persisted Claude history, per session


@lru_cache
def get_settings() -> Settings:
    return Settings()
