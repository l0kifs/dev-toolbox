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
from typing import TYPE_CHECKING, Any

from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

# Single root for everything this tool writes: config, temp clones, logs, and
# launch-agent plists. Overridable so tests and power users can relocate it.
DATA_DIR = Path(os.environ.get("KITBAG_HOME") or (Path.home() / ".kitbag"))

# Stable, cwd-independent config file. A cwd `.env` still works and, being listed last,
# wins over this file for per-project overrides.
USER_CONFIG_ENV = DATA_DIR / ".env"

# Per-command data roots. Each command keeps its files under its own subdirectory.
TEMP_CLONE_DIR = DATA_DIR / "temp-clone"
CLAUDE_SANDBOX_DIR = DATA_DIR / "claude-sandbox"

# ── Secure credential storage ─────────────────────────────────────────────────
# These configure the secrets backend and, like DATA_DIR, must be known *before*
# Settings loads — the secrets store is itself one of Settings' sources, so it can't
# read its own configuration back out of Settings without recursing. They're resolved
# from the environment here instead.
#
#   KITBAG_SECRETS_BACKEND   auto | keyring | file   (default: auto)
#   KITBAG_SECRETS_SERVICE   keychain service name   (default: kitbag)
#   KITBAG_MASTER_PASSPHRASE passphrase for the encrypted-file backend (optional)
SECRETS_BACKEND = (os.environ.get("KITBAG_SECRETS_BACKEND") or "auto").strip().lower()
SECRETS_SERVICE = os.environ.get("KITBAG_SECRETS_SERVICE") or "kitbag"
SECRETS_INDEX_PATH = DATA_DIR / "secrets.index.json"
SECRETS_FILE_PATH = DATA_DIR / "secrets.enc"
MASTER_PASSPHRASE_ENV = "KITBAG_MASTER_PASSPHRASE"


class SecretsSettingsSource(PydanticBaseSettingsSource):
    """Feed the app's known credentials from the secure store into `Settings`.

    Only the registered credential fields (`MINIMAX_API_KEY`, `ANTHROPIC_API_KEY`,
    `CLAUDE_CODE_OAUTH_TOKEN`) are pulled from the store; arbitrary user secrets stay out
    of `Settings`. The store is consulted lazily via its names index so an unset secret
    costs no backend round-trip, and every failure is swallowed: if the store is
    unavailable or (for the file backend) locked with no passphrase in the environment,
    this source contributes nothing and resolution falls through to the `.env` files. It
    must never raise or prompt — it runs on every command startup.
    """

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Unused: we override __call__ to bulk-load from the store in one pass. Required
        # by the abstract base; returning "not found" keeps the default contract.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        # Imported lazily: the secrets backend imports this module, so a top-level import
        # would create a cycle at load time.
        from kitbag.secrets.backend import try_get_store
        from kitbag.secrets.registry import KNOWN_CREDENTIALS

        store = try_get_store()
        if store is None:
            return {}
        try:
            stored = set(store.list_names())
        except Exception:
            return {}

        values: dict[str, Any] = {}
        for cred in KNOWN_CREDENTIALS:
            if cred.name not in stored:
                continue
            try:
                value = store.get(cred.name)
            except Exception:
                continue
            if value:
                values[cred.field] = value
        return values


class Settings(BaseSettings):
    """Single source of truth for every default value in the app.

    No command hardcodes tunables — they all read them from here, and each field can be
    overridden via an env var (e.g. `MINIMAX_MODEL`) or the `.env` files above. `DATA_DIR`
    itself is not a field because it must be known *before* settings load (it's where the
    config file lives); everything derived from it is a field.

    Credential resolution order (highest priority first):

      1. real environment variables,
      2. a per-project `.env` in the current directory,
      3. the secure store (OS keychain or encrypted file),
      4. the user config file `~/.kitbag/.env`.

    So a real env var or a project `.env` still overrides a stored secret (handy for CI
    and one-off overrides), while the secure store supersedes the plaintext user `.env`.
    """

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The default `dotenv_settings` bundles both env files into one source, which
        # would leave no seam to insert the secure store *between* them. Split them into
        # two so the store can sit below the cwd `.env` but above the user `.env`.
        cwd_dotenv = DotEnvSettingsSource(settings_cls, env_file=".env", env_file_encoding="utf-8")
        user_dotenv = DotEnvSettingsSource(
            settings_cls, env_file=USER_CONFIG_ENV, env_file_encoding="utf-8"
        )
        return (
            init_settings,
            env_settings,
            cwd_dotenv,
            SecretsSettingsSource(settings_cls),
            user_dotenv,
            file_secret_settings,
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
    # so a real env var wins, then a cwd .env, then the secure store, then ~/.kitbag/.env.
    # Prefer `kitbag secrets set` / `kitbag configure` over the plaintext file.
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
