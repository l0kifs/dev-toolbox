"""Unit tests for the `.env` → secure-store migration."""

from __future__ import annotations

import pytest

from kitbag.secrets.migrate import migrate_env_file
from kitbag.secrets.store import SecretStore, validate_name

pytestmark = pytest.mark.unit


class InMemoryStore(SecretStore):
    name = "memory"

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def get(self, name):
        return self._data.get(validate_name(name))

    def set(self, name, value) -> None:
        self._data[validate_name(name)] = value

    def delete(self, name) -> bool:
        return self._data.pop(validate_name(name), None) is not None

    def list_names(self):
        return sorted(self._data)


def test_migrate_moves_known_credentials_and_comments_lines(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "MINIMAX_API_KEY=mmx-123\n"
        "ANTHROPIC_API_KEY='sk-ant-xyz'\n"
        "UNKNOWN_KEY=keep-me\n"
        "# already a comment\n",
        encoding="utf-8",
    )
    store = InMemoryStore()

    results = migrate_env_file(env, store)

    moved = {r.name for r in results if r.moved}
    assert moved == {"MINIMAX_API_KEY", "ANTHROPIC_API_KEY"}
    assert store.get("MINIMAX_API_KEY") == "mmx-123"
    assert store.get("ANTHROPIC_API_KEY") == "sk-ant-xyz"  # quotes stripped

    text = env.read_text(encoding="utf-8")
    assert "# MINIMAX_API_KEY=mmx-123" in text
    assert "# ANTHROPIC_API_KEY='sk-ant-xyz'" in text
    assert "UNKNOWN_KEY=keep-me" in text  # untouched


def test_migrate_skips_empty_values(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("MINIMAX_API_KEY=\n", encoding="utf-8")
    store = InMemoryStore()

    results = migrate_env_file(env, store)

    assert results == []
    assert store.list_names() == []
    # File is left untouched when nothing moved.
    assert env.read_text(encoding="utf-8") == "MINIMAX_API_KEY=\n"


def test_migrate_is_idempotent(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("MINIMAX_API_KEY=mmx-123\n", encoding="utf-8")
    store = InMemoryStore()

    migrate_env_file(env, store)
    # Second run sees only the commented-out line and moves nothing new.
    assert migrate_env_file(env, store) == []


def test_migrate_missing_file_returns_empty(tmp_path) -> None:
    assert migrate_env_file(tmp_path / "nope.env", InMemoryStore()) == []
