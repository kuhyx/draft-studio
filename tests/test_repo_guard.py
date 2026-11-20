"""C19: no draft, history or token file can be committed to this public repo."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "guard_no_drafts.sh"
DATA_PATHS = [
    "drafts/abc/draft.md",
    "x/meta.json",
    "x/history.jsonl",
    "notes.draft.md",
    "trash/abc/draft.md",
    "data/whatever.md",
    "gmail.json",
    "gmail-fake.json",
    "oauth_token.json",
]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("path", DATA_PATHS)
def test_gitignore_matches_data_paths(path: str) -> None:
    result = _git(REPO, "check-ignore", "--no-index", "-q", path)
    assert result.returncode == 0, f"{path} is not ignored"


def test_no_tracked_file_is_user_data() -> None:
    tracked = _git(REPO, "ls-files").stdout.splitlines()
    result = subprocess.run(
        ["bash", str(GUARD), "--paths", *tracked], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("path", DATA_PATHS)
def test_guard_blocks_forced_add(tmp_path: Path, path: str) -> None:
    _git(tmp_path, "init", "-q")
    shutil.copy(REPO / ".gitignore", tmp_path / ".gitignore")
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("secret draft\n")
    assert _git(tmp_path, "add", "-f", path).returncode == 0
    result = subprocess.run(["bash", str(GUARD)], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert path in result.stderr


def test_guard_allows_code(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "app.py").write_text("x = 1\n")
    _git(tmp_path, "add", "app.py")
    result = subprocess.run(["bash", str(GUARD)], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
