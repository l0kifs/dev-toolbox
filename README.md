# kitbag

A collection of developer utilities wired into a single CLI.

## Install

Install it globally as a uv tool so `kitbag` is on your PATH and works from any
directory:

```sh
uv tool install .            # from a clone, or `uv tool install <git-url>`
kitbag --help
```

Or run it ad-hoc without installing:

```sh
uvx --from . kitbag --help
```

For local development, `uv sync` + `uv run kitbag ...` works too.

## Data directory

All app data lives under a single root, `~/.kitbag/` (override with the
`KITBAG_HOME` environment variable):

```
~/.kitbag/
├── .env                 # your config (see below)
└── temp-clone/          # everything the `temp-clone` command writes
    ├── clones/          # throwaway clones
    ├── logs/            # cleanup logs
    └── launch-agents/   # scheduled-cleanup plists
```

## Configuration

Because the tool is meant to run from anywhere, put your standing config in the
user-level file (create the directory if needed):

```sh
mkdir -p ~/.kitbag
cp .env.example ~/.kitbag/.env   # then edit
```

Resolution order (highest priority first): environment variables → a `.env` in the
current directory (per-project override) → `~/.kitbag/.env` (stable base).

Every default lives in `Settings` ([config.py](src/kitbag/config.py)) and is
overridable by the matching upper-case env var. `MINIMAX_API_KEY` is the only one you
normally need to set; the rest have sensible defaults.

| Variable                    | Used by      | Default                             | Purpose                                 |
| --------------------------- | ------------ | ----------------------------------- | --------------------------------------- |
| `MINIMAX_API_KEY`           | `ai-commit`  | —                                   | MiniMax API key (required)              |
| `MINIMAX_MODEL`             | `ai-commit`  | `MiniMax-M2.7`                      | Model used to generate messages         |
| `MINIMAX_API_BASE`          | `ai-commit`  | `https://api.minimax.io/v1`         | API base URL                            |
| `MINIMAX_TOKEN_BUDGET`      | `ai-commit`  | `100000`                            | Max diff tokens sent to the model       |
| `MINIMAX_MAX_OUTPUT_TOKENS` | `ai-commit`  | `2000`                              | Max tokens in the generated message     |
| `MINIMAX_REASONING_EFFORT`  | `ai-commit`  | `minimal`                           | none / minimal / low / medium / high    |
| `MINIMAX_API_TIMEOUT`       | `ai-commit`  | `120`                               | Request timeout (seconds)               |
| `MINIMAX_HTTP_RETRIES`      | `ai-commit`  | `4`                                 | Retries on 429/5xx                      |
| `CLONE_OPEN_VSCODE`         | `temp-clone` | `true`                              | Open the clone in VS Code by default    |
| `CLONE_CLEANUP_HOURS`       | `temp-clone` | `12`                                | Auto-delete the temp clone after N hours |
| `CLONES_DIR`                | `temp-clone` | `~/.kitbag/temp-clone/clones`  | Where temp clones are placed            |
| `LOGS_DIR`                  | `temp-clone` | `~/.kitbag/temp-clone/logs`    | Cleanup log location                    |
| `LAUNCH_AGENTS_DIR`         | `temp-clone` | `~/.kitbag/temp-clone/launch-agents` | Where cleanup plists are written  |
| `KITBAG_HOME`          | all          | `~/.kitbag`                    | Root for all app data (affects the above) |

## Commands

| Command      | Does                                                                     |
| ------------ | ------------------------------------------------------------------------ |
| `ai-commit`  | Generate an AI commit message for staged changes and create the commit   |
| `temp-clone` | Clone a GitHub repo into a temp dir, open it in VS Code, auto-clean later |

### `ai-commit` — AI commit messages

Generates a Conventional Commits message for your staged changes via MiniMax, lets you
review or revise it interactively, then creates the commit. Runs against whatever git
repo you're currently in.

```sh
git add -p
uv run kitbag ai-commit
```

### `temp-clone` — throwaway GitHub clones

Clones a repo into a temp directory, opens it in VS Code, and schedules automatic cleanup
via macOS launchd. Requires the `gh` CLI (authenticated) and, for `--open`, the `code` CLI.

```sh
uv run kitbag temp-clone https://github.com/org/repo
uv run kitbag temp-clone https://github.com/org/repo --no-open --cleanup-hours 4
```
