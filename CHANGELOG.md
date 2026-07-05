# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Secure credential storage. Credentials are kept in the OS keychain (macOS Keychain,
  Windows Credential Manager, Linux Secret Service) via `keyring`, with an encrypted-file
  fallback (`KITBAG_SECRETS_BACKEND=file`) for headless machines.
  - `kitbag configure`: interactive wizard to store the credentials the built-in commands
    need.
  - `kitbag secrets set/get/list/delete`: manage individual secrets (known credentials and
    arbitrary key/value secrets); values are entered via a hidden prompt and never echoed.
  - `kitbag secrets import`: move known credentials out of `~/.kitbag/.env` into the secure
    store, commenting out the originals (reversible).
  - Stored credentials feed into settings automatically. Resolution order: real env var →
    per-project `.env` → secure store → `~/.kitbag/.env`, so the store supersedes the
    plaintext user file while env vars and project `.env` still override for CI.

## [0.2.0] - 2026-07-04

### Added
- `claude-sandbox` command: run Claude Code with full autonomy inside a network-restricted
  Docker container. Clones a repo URL (ephemeral) or bind-mounts the current repo; runs
  headless with `--prompt` or interactively. Fenced in by a non-root user, an iptables
  egress allowlist, and triple-layered blocking of pushes to remotes.
  - Session history is persisted on the host per named session, surviving the throwaway
    container (`--name`, `SANDBOX_SESSIONS_DIR`).
  - `--attach <name>` opens a shell inside a running sandbox.
  - `--notify` rings the bell / shows a macOS notification when a headless run finishes.
  - `--stream` shows a live activity log (tool calls + output) during a headless run.
  - `--model` selects the Claude model, with a configurable default (`SANDBOX_MODEL`).
  - The Claude credential (`ANTHROPIC_API_KEY` / `CLAUDE_CODE_OAUTH_TOKEN`) can now be
    supplied via kitbag's `.env` files, not just a real environment variable.

## [0.1.0] - 2026-07-04

### Added
- Initial `kitbag` CLI.
- `ai-commit` command: generate an AI commit message for staged changes and create the commit.
- `temp-clone` command: clone a GitHub repo into a temp directory and optionally open it in VS Code.

[Unreleased]: https://github.com/l0kifs/kitbag/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/l0kifs/kitbag/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/l0kifs/kitbag/releases/tag/v0.1.0
