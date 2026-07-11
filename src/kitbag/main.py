"""kitbag — a single CLI wiring together developer utilities.

Subcommands:
  ai-commit       Generate an AI Conventional Commits message for staged changes (MiniMax).
  temp-clone      Clone a GitHub repo into a temp dir, open it in VS Code, auto-clean later.
  claude-sandbox  Run Claude Code with full autonomy inside a network-restricted Docker sandbox.
  branch-clean    Delete local branches that are merged or whose remote branch is gone.
  configure       Interactively store the credentials the commands need (secure storage).
  secrets         Manage credentials in local secure storage (set/get/list/delete/import).
"""

from __future__ import annotations

import typer

from kitbag.commands.ai_commit import ai_commit_command
from kitbag.commands.branch_clean import branch_clean_command
from kitbag.commands.claude_sandbox import claude_sandbox_command
from kitbag.commands.configure import configure_command
from kitbag.commands.secrets import secrets_app
from kitbag.commands.temp_clone import temp_clone_command

app = typer.Typer(
    help="A collection of tools for developers.",
    no_args_is_help=True,
    add_completion=True,  # adds --install-completion / --show-completion for shell tab-completion
)

app.command("ai-commit")(ai_commit_command)
app.command("temp-clone")(temp_clone_command)
app.command("claude-sandbox")(claude_sandbox_command)
app.command("branch-clean")(branch_clean_command)
app.command("configure")(configure_command)
app.add_typer(secrets_app, name="secrets")


if __name__ == "__main__":
    app()
