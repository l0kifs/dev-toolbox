"""dev-toolbox — a single CLI wiring together developer utilities.

Subcommands:
  ai-commit    Generate an AI Conventional Commits message for staged changes (MiniMax).
  temp-clone   Clone a GitHub repo into a temp dir, open it in VS Code, auto-clean later.
"""

from __future__ import annotations

import typer

from dev_toolbox.commands.ai_commit import ai_commit_command
from dev_toolbox.commands.temp_clone import temp_clone_command

app = typer.Typer(
    help="A collection of tools for developers.",
    no_args_is_help=True,
    add_completion=False,
)

app.command("ai-commit")(ai_commit_command)
app.command("temp-clone")(temp_clone_command)


if __name__ == "__main__":
    app()
