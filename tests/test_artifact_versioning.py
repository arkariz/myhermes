"""orchestrator.artifact_versioning -- real git subprocess calls against
tmp_path, same "no mocking" philosophy as test_store.py: the property under
test IS git's own behavior (init, diff-detection, empty-commit avoidance),
so faking it would test nothing real. git is fast and deterministic enough
locally that this costs nothing meaningful in test runtime.
"""

from __future__ import annotations

from agentic_dev.adapters.git.artifacts import commit, diff, ensure_repo, log


def test_ensure_repo_creates_a_git_repo(tmp_path):
    ensure_repo(tmp_path)
    assert (tmp_path / ".git").is_dir()


def test_ensure_repo_is_idempotent(tmp_path):
    ensure_repo(tmp_path)
    ensure_repo(tmp_path)  # must not raise on a repo that already exists


def test_commit_with_new_content_returns_a_hash(tmp_path):
    (tmp_path / "prd.md").write_text("# PRD v1", encoding="utf-8")
    commit_hash = commit(tmp_path, "planning / planning-01")
    assert commit_hash is not None
    assert len(commit_hash) == 40  # a real git SHA-1


def test_commit_with_no_changes_returns_none(tmp_path):
    (tmp_path / "prd.md").write_text("# PRD v1", encoding="utf-8")
    first = commit(tmp_path, "first")
    second = commit(tmp_path, "second -- nothing actually changed")
    assert first is not None
    assert second is None


def test_commit_after_an_edit_returns_a_new_hash(tmp_path):
    (tmp_path / "prd.md").write_text("# PRD v1", encoding="utf-8")
    first = commit(tmp_path, "v1")

    (tmp_path / "prd.md").write_text("# PRD v2 -- added acceptance criteria", encoding="utf-8")
    second = commit(tmp_path, "v2")

    assert second is not None
    assert second != first


def test_log_is_empty_for_a_project_with_no_commits_yet(tmp_path):
    assert log(tmp_path) == []


def test_log_returns_commits_newest_first(tmp_path):
    (tmp_path / "prd.md").write_text("v1", encoding="utf-8")
    commit(tmp_path, "first commit")
    (tmp_path / "prd.md").write_text("v2", encoding="utf-8")
    commit(tmp_path, "second commit")

    entries = log(tmp_path)
    assert [e["message"] for e in entries] == ["second commit", "first commit"]


def test_log_can_scope_to_one_artifact(tmp_path):
    (tmp_path / "prd.md").write_text("v1", encoding="utf-8")
    commit(tmp_path, "touches prd.md")
    (tmp_path / "architecture.md").write_text("v1", encoding="utf-8")
    commit(tmp_path, "touches architecture.md")

    entries = log(tmp_path, artifact_name="prd.md")
    assert [e["message"] for e in entries] == ["touches prd.md"]


def test_diff_shows_the_actual_content_change(tmp_path):
    (tmp_path / "prd.md").write_text("line one\n", encoding="utf-8")
    first = commit(tmp_path, "v1")
    (tmp_path / "prd.md").write_text("line one\nline two\n", encoding="utf-8")
    commit(tmp_path, "v2")

    result = diff(tmp_path, "prd.md", first)
    assert "+line two" in result
