from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from livegraph.history.extractor import iter_commits


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> None:
    base_env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00",
                "GIT_COMMITTER_DATE": "2026-01-01T00:00:00",
                "EMAIL": "default@example.com",
                "GIT_AUTHOR_NAME": "Default", "GIT_AUTHOR_EMAIL": "default@example.com",
                "GIT_COMMITTER_NAME": "Default",
                "GIT_COMMITTER_EMAIL": "default@example.com"}
    if env:
        base_env.update(env)
    subprocess.run(["git", *args], cwd=repo, env=base_env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest.fixture()
def tiny_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "alice@example.com")
    _git(tmp_path, "config", "user.name", "Alice")
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "add foo",
         env={"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.com",
              "GIT_COMMITTER_NAME": "Alice",
              "GIT_COMMITTER_EMAIL": "alice@example.com"})
    (tmp_path / "a.py").write_text("def foo():\n    return 2\n")
    (tmp_path / "b.py").write_text("def bar():\n    return 1\n")
    _git(tmp_path, "add", "a.py", "b.py")
    _git(tmp_path, "commit", "-q", "-m", "tweak foo + add bar",
         env={"GIT_AUTHOR_NAME": "Bob", "GIT_AUTHOR_EMAIL": "bob@example.com",
              "GIT_COMMITTER_NAME": "Bob",
              "GIT_COMMITTER_EMAIL": "bob@example.com"})
    return tmp_path


def test_iter_commits_returns_commits_newest_first(tiny_repo):
    commits = list(iter_commits(str(tiny_repo)))
    assert len(commits) == 2
    assert commits[0].message == "tweak foo + add bar"
    assert commits[1].message == "add foo"


def test_commit_carries_author_email_and_name(tiny_repo):
    commits = list(iter_commits(str(tiny_repo)))
    assert commits[0].author_email == "bob@example.com"
    assert commits[0].author_name == "Bob"
    assert commits[1].author_email == "alice@example.com"


def test_commit_carries_file_changes_with_hunks(tiny_repo):
    commits = list(iter_commits(str(tiny_repo)))
    head = commits[0]
    paths = {f.path for f in head.files}
    assert paths == {"a.py", "b.py"}
    a = next(f for f in head.files if f.path == "a.py")
    # Modifying line 2 of a 2-line file: hunk should cover at least line 2.
    assert a.hunks and any(h.start <= 2 <= h.end for h in a.hunks)
    assert a.additions >= 1 and a.deletions >= 1


def test_since_argument_walks_only_newer_commits(tiny_repo):
    all_commits = list(iter_commits(str(tiny_repo)))
    older_sha = all_commits[-1].sha
    newer_only = list(iter_commits(str(tiny_repo), since=older_sha))
    assert [c.message for c in newer_only] == ["tweak foo + add bar"]


def test_max_commits_caps_the_walk(tiny_repo):
    commits = list(iter_commits(str(tiny_repo), max_commits=1))
    assert len(commits) == 1
    assert commits[0].message == "tweak foo + add bar"
