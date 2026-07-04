"""kitbag — a single CLI wiring together developer utilities.

Subcommands:
  ai-commit       Generate an AI Conventional Commits message for staged changes (MiniMax).
  temp-clone      Clone a GitHub repo into a temp dir, open it in VS Code, auto-clean later.
  claude-sandbox  Run Claude Code with full autonomy inside a network-restricted Docker sandbox.
"""

from __future__ import annotations

import typer

from kitbag.commands.ai_commit import ai_commit_command
from kitbag.commands.claude_sandbox import claude_sandbox_command
from kitbag.commands.temp_clone import temp_clone_command

app = typer.Typer(
    help="A collection of tools for developers.",
    no_args_is_help=True,
    add_completion=True,  # adds --install-completion / --show-completion for shell tab-completion
)

app.command("ai-commit")(ai_commit_command)
app.command("temp-clone")(temp_clone_command)
app.command("claude-sandbox")(claude_sandbox_command)


if __name__ == "__main__":
    app()
