"""One-time migration of plaintext credentials out of a `.env` file into the store.

Backs `kitbag secrets import`. For every *known* credential found with a non-empty value
in the target file, the value is written to the secure store and the original line is
commented out (not deleted) so the change is reversible. Unknown keys and blank values
are left untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from kitbag.secrets.registry import BY_NAME
from kitbag.secrets.store import SecretStore

# A `.env` assignment line: optional leading whitespace, KEY, '=', value. Already-commented
# lines start with '#' and won't match, so re-running import is a no-op.
_ASSIGN_RE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

_MOVED_MARKER = "moved to kitbag secure store"


@dataclass
class MigrationResult:
    """Outcome of migrating a single credential line."""

    name: str
    moved: bool
    note: str = ""


def _unquote(value: str) -> str:
    """Strip a matching pair of surrounding quotes, mirroring dotenv parsing."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def migrate_env_file(path: Path, store: SecretStore) -> list[MigrationResult]:
    """Move known credentials from `path` into `store`, commenting out their lines.

    Returns one `MigrationResult` per credential that was present with a value. Does
    nothing and returns an empty list if the file doesn't exist.
    """
    try:
        original = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []

    results: list[MigrationResult] = []
    out_lines: list[str] = []
    for line in original.splitlines():
        match = _ASSIGN_RE.match(line)
        if not match:
            out_lines.append(line)
            continue

        key = match.group(2).upper()
        cred = BY_NAME.get(key)
        value = _unquote(match.group(3))
        if cred is None or not value:
            # Not a known credential, or empty — leave the line exactly as-is.
            out_lines.append(line)
            continue

        store.set(cred.name, value)
        out_lines.append(f"# {line.rstrip()}   # {_MOVED_MARKER}")
        results.append(MigrationResult(name=cred.name, moved=True))

    if any(r.moved for r in results):
        path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    return results
