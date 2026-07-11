"""Integration tests for `branch-clean` against real, throwaway git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from kitbag.commands.branch_clean import (
    BranchCleanError,
    current_branch,
    delete_branch,
    detect_base_branch,
    find_repo_root,
    gather_candidates,
    gone_branches,
    merged_branches,
)

pytestmark = pytest.mark.integration


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "initial commit")
    return repo


def test_find_repo_root_resolves_toplevel(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    (repo / "sub").mkdir()
    import os

    cwd = os.getcwd()
    os.chdir(repo / "sub")
    try:
        assert find_repo_root().resolve() == repo.resolve()
    finally:
        os.chdir(cwd)


def test_find_repo_root_outside_git_raises(tmp_path: Path) -> None:
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        with pytest.raises(BranchCleanError):
            find_repo_root()
    finally:
        os.chdir(cwd)


def test_merged_branches_detects_fast_forwardable_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "feature-merged")
    _git(repo, "checkout", "-q", "main")

    merged = merged_branches(repo, "main")

    assert "feature-merged" in merged
    assert "main" in merged  # a branch is trivially merged into itself


def test_merged_branches_excludes_unmerged_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature-ahead")
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "extra commit")
    _git(repo, "checkout", "-q", "main")

    assert "feature-ahead" not in merged_branches(repo, "main")


def test_gone_branches_detects_deleted_remote(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))

    repo = _init_repo(tmp_path)
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-q", "-u", "origin", "main")
    _git(repo, "checkout", "-q", "-b", "squash-merged")
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "work")
    _git(repo, "push", "-q", "-u", "origin", "squash-merged")
    _git(repo, "push", "-q", "origin", "--delete", "squash-merged")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "fetch", "-q", "--prune", "origin")

    gone = gone_branches(repo)

    assert "squash-merged" in gone
    assert "main" not in gone


def test_detect_base_branch_falls_back_to_local_main(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert detect_base_branch(repo, "origin") == "main"


def test_gather_candidates_excludes_protected(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "feature-merged")

    candidates = gather_candidates(repo, "main", protected={"main", "feature-merged"})

    assert candidates == []


def test_gather_candidates_marks_merged_as_safe(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "feature-merged")

    candidates = gather_candidates(repo, "main", protected={"main"})

    assert len(candidates) == 1
    assert candidates[0].name == "feature-merged"
    assert candidates[0].reason == "merged"
    assert candidates[0].force is False


def test_delete_branch_removes_merged_branch_safely(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "branch", "feature-merged")

    ok, _ = delete_branch(repo, "feature-merged", force=False)

    assert ok
    assert "feature-merged" not in {b.strip() for b in subprocess.run(
        ["git", "branch", "--format=%(refname:short)"], cwd=repo, capture_output=True, text=True,
    ).stdout.splitlines()}


def test_delete_branch_safe_mode_refuses_unmerged(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "feature-ahead")
    (repo / "new.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "extra commit")
    _git(repo, "checkout", "-q", "main")

    ok, message = delete_branch(repo, "feature-ahead", force=False)

    assert not ok
    assert message


def test_current_branch_reports_checked_out_branch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    assert current_branch(repo) == "main"
