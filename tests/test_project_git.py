import subprocess

import pytest

from orchestrator.project_git import ProjectGitError, commit_all, current_revision, diff, is_git_repo


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "test@example.com"], path)
    _git(["config", "user.name", "Test"], path)
    return path


def test_is_git_repo_is_false_for_a_plain_directory(tmp_path):
    assert is_git_repo(tmp_path) is False


def test_is_git_repo_is_true_after_git_init(tmp_path):
    _init_repo(tmp_path)
    assert is_git_repo(tmp_path) is True


def test_current_revision_is_none_before_any_commit(tmp_path):
    _init_repo(tmp_path)
    assert current_revision(tmp_path) is None


def test_current_revision_is_none_for_a_non_git_directory(tmp_path):
    assert current_revision(tmp_path) is None


def test_commit_all_raises_for_a_non_git_directory(tmp_path):
    with pytest.raises(ProjectGitError):
        commit_all(tmp_path, "message")


def test_commit_all_returns_none_when_nothing_changed(tmp_path):
    _init_repo(tmp_path)
    assert commit_all(tmp_path, "nothing to commit") is None


def test_commit_all_commits_new_files_and_returns_a_real_hash(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "lib.dart").write_text("class A {}", encoding="utf-8")

    commit_hash = commit_all(tmp_path, "builder: turn 1")

    assert commit_hash is not None
    assert current_revision(tmp_path) == commit_hash


def test_diff_is_empty_for_a_non_git_directory(tmp_path):
    assert diff(tmp_path, None) == ""


def test_diff_against_none_base_shows_the_first_commit_as_entirely_new(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.dart").write_text("class A {}", encoding="utf-8")
    commit_hash = commit_all(tmp_path, "first")

    result = diff(tmp_path, None, to_revision=commit_hash)

    assert "+class A {}" in result


def test_diff_between_two_real_revisions_shows_only_the_change(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.dart").write_text("class A {}", encoding="utf-8")
    base = commit_all(tmp_path, "first")

    (tmp_path / "b.dart").write_text("class B {}", encoding="utf-8")
    head = commit_all(tmp_path, "second")

    result = diff(tmp_path, base, to_revision=head)

    assert "+class B {}" in result
    assert "class A" not in result
