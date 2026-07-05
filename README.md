# kitbag

A collection of developer utilities wired into a single CLI.

## Install

Install it globally as a uv tool so `kitbag` is on your PATH and works from any
directory:

```sh
uv tool install kitbag
kitbag --help
```

Upgrade to the latest release later with:

```sh
uv tool upgrade kitbag
```

Or run it ad-hoc without installing:

```sh
uvx kitbag --help
```

<details>
<summary>Installing from source (development)</summary>

```sh
uv tool install .        # from a clone, or `uv tool install <git-url>`
uvx --from . kitbag --help
uv sync && uv run kitbag ...
```

</details>

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
| `ANTHROPIC_API_KEY`         | `claude-sandbox` | —                               | Claude API key (or use the token below) |
| `CLAUDE_CODE_OAUTH_TOKEN`   | `claude-sandbox` | —                               | Long-lived Claude token (`claude setup-token`) |
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
| `SANDBOX_ALLOWED_DOMAINS`   | `claude-sandbox` | Anthropic + git/package hosts   | Egress allowlist (comma-separated)      |
| `SANDBOX_FIREWALL`          | `claude-sandbox` | `true`                          | Restrict container egress to the allowlist |
| `SANDBOX_MODEL`             | `claude-sandbox` | — (Claude's default)            | Default Claude model (alias or full id), overridable with `--model` |
| `SANDBOX_OUTPUT_FORMAT`     | `claude-sandbox` | `text`                          | Headless format: text / json / stream-json |
| `SANDBOX_SESSIONS_DIR`      | `claude-sandbox` | `~/.kitbag/claude-sandbox/sessions` | Where per-session Claude history is kept |
| `KITBAG_HOME`          | all          | `~/.kitbag`                    | Root for all app data (affects the above) |

`claude-sandbox` needs a Claude credential — `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`
(these are Claude Code's own env-var names). It's passed into the container so Claude can
authenticate, and like every other setting it resolves from a real env var, then a cwd
`.env`, then `~/.kitbag/.env` — so you can keep it in a `.env` instead of exporting it.

## Commands

| Command          | Does                                                                     |
| ---------------- | ------------------------------------------------------------------------ |
| `ai-commit`      | Generate an AI commit message for staged changes and create the commit   |
| `temp-clone`     | Clone a GitHub repo into a temp dir, open it in VS Code, auto-clean later |
| `claude-sandbox` | Run Claude Code with full autonomy inside a network-restricted Docker box |

### `ai-commit` — AI commit messages

Generates a Conventional Commits message for your staged changes via MiniMax, lets you
review or revise it interactively, then creates the commit. Runs against whatever git
repo you're currently in.

```sh
git add -p
kitbag ai-commit
```

### `temp-clone` — throwaway GitHub clones

Clones a repo into a temp directory, opens it in VS Code, and schedules automatic cleanup
via macOS launchd. Requires the `gh` CLI (authenticated) and, for `--open`, the `code` CLI.

```sh
kitbag temp-clone https://github.com/org/repo
kitbag temp-clone https://github.com/org/repo --no-open --cleanup-hours 4
```

### `claude-sandbox` — autonomous Claude Code in a locked-down container

Runs [Claude Code](https://claude.com/claude-code) inside a Docker container so it can
work with full autonomy (`--dangerously-skip-permissions`) while staying fenced off from
your host and the wider network. Requires Docker running and `ANTHROPIC_API_KEY` (or a
`CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`) in your environment.

```sh
# Interactive session on the repo you're standing in (edits persist for review):
kitbag claude-sandbox

# Headless one-shot on a freshly cloned repo (ephemeral — nothing on your host is touched):
kitbag claude-sandbox https://github.com/org/repo -p "Fix the failing tests"

# Headless, but watch a live activity log of tool calls + output as Claude works:
kitbag claude-sandbox -p "Fix the failing tests" --stream

# Name the session, and get pinged when a headless run finishes:
kitbag claude-sandbox -n refactor -p "Split the god object" --notify

# From another terminal, open a shell inside the running "refactor" sandbox:
kitbag claude-sandbox --attach refactor

# Allow an extra domain through the egress firewall:
kitbag claude-sandbox -p "Update deps" --allow-domain deb.debian.org
```

**Input** — pass a repo URL to clone it *inside* the container (ephemeral), or omit it to
bind-mount the current git repo read-write (uid-matched, so Claude's edits land on your
host for you to review and commit). **Run mode** — pass `--prompt/-p` for a headless
one-shot, or omit it for an interactive session.

**Seeing what Claude is doing:** an interactive session is fully live (the normal Claude
Code TUI). A headless run is quiet by default — it prints only the final result — which
suits scripting/CI. Add `--stream` to a headless run for a live, human-readable activity
log (each tool call, its result, and Claude's text as it goes, then a turns/time/cost
summary at the end).

**Sessions, history, and control:**

- Each run has a **name** (`--name/-n`, or an auto-generated `sbx-…`). Its Claude session
  history is persisted on the host under `~/.kitbag/claude-sandbox/sessions/<name>/`, so
  transcripts survive the throwaway container — and re-running the same name resumes that
  history.
- While a sandbox is running, `--attach <name>` opens a second shell **inside** it (subject
  to the same egress firewall and push blocks). Interactive runs print the exact attach
  command to use.
- `--notify` rings the terminal bell and (on macOS) shows a desktop notification when a
  headless run finishes.
- The run is foreground: watch it live, and **Ctrl-C** stops it (the `--rm` container is
  cleaned up).

**How it's fenced in** (defense in depth):

- Claude runs as a **non-root user**, so a bypassed session can't touch your host beyond
  the mounted workspace.
- An **egress firewall** (iptables) drops all outbound traffic except an allowlist of
  domains (Anthropic API + common git/package hosts). Extend it with `--allow-domain` or
  `SANDBOX_ALLOWED_DOMAINS`; disable with `--no-firewall`.
- **Pushing to remotes is blocked** three independent ways — a root-owned `settings.json`
  `deny` rule, a root-owned PreToolUse hook, and a root-owned git `pre-push` hook, none of
  which the `claude` user can edit — and no git push credentials are injected.

The container image is built once (cached, keyed to a content hash; `--rebuild` forces a
rebuild). The generated Docker build context lives under `~/.kitbag/claude-sandbox/`.

> **Caveats.** The egress allowlist is resolved to IPs at container start, so hosts behind
> rotating CDN IPs may occasionally need a re-run, and it isn't TLS-inspected — a broad
> allowlisted domain can still be a data path. When you bind-mount the current repo, Claude
> can freely modify anything in it (that's the point); the sandbox protects your host
> *outside* the mount and your network, not the mounted files themselves.
