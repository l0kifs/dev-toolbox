"""Registry of the app's own credentials.

These are the secrets kitbag's built-in commands need. Each maps a `Settings` field to
the secret name used in the store (and, since names are env-var-shaped, to the matching
environment variable). The `configure` wizard and `secrets import` iterate this list;
`get_secret_settings` reads these names back into `Settings`.

Arbitrary user-defined secrets (`kitbag secrets set FOO`) are *not* listed here — they
live in the same store but aren't surfaced through `Settings`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnownCredential:
    """A credential kitbag itself uses."""

    name: str  # store key / env var, e.g. "MINIMAX_API_KEY"
    field: str  # matching Settings field, e.g. "minimax_api_key"
    used_by: str  # command that needs it, for help text
    description: str

    @property
    def env_var(self) -> str:
        return self.name


KNOWN_CREDENTIALS: tuple[KnownCredential, ...] = (
    KnownCredential(
        name="MINIMAX_API_KEY",
        field="minimax_api_key",
        used_by="ai-commit",
        description="MiniMax API key used to generate commit messages.",
    ),
    KnownCredential(
        name="ANTHROPIC_API_KEY",
        field="anthropic_api_key",
        used_by="claude-sandbox",
        description="Anthropic API key passed into the Claude Code sandbox.",
    ),
    KnownCredential(
        name="CLAUDE_CODE_OAUTH_TOKEN",
        field="claude_code_oauth_token",
        used_by="claude-sandbox",
        description="Long-lived Claude token from `claude setup-token` (alternative to the API key).",
    ),
)

# Fast lookups by either identifier.
BY_NAME: dict[str, KnownCredential] = {c.name: c for c in KNOWN_CREDENTIALS}
BY_FIELD: dict[str, KnownCredential] = {c.field: c for c in KNOWN_CREDENTIALS}
