import subprocess

import pytest

from agentic_dev.adapters.git.project import (
    InvalidRepositoryUrl,
    ProjectGitError,
    clone,
    commit_all,
    current_revision,
    diff,
    is_git_repo,
    repo_name_from_url,
)


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


def test_is_git_repo_is_false_for_a_directory_that_does_not_exist_yet(tmp_path):
    # A project just /create'd from Telegram, before any source has been
    # dropped into its host_path -- a real case, not an error. Regression
    # test: subprocess.run raises before this guard existed.
    assert is_git_repo(tmp_path / "does-not-exist-yet") is False


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


# ---- clone() / repo_name_from_url() -----------------------------------------


@pytest.mark.parametrize("url,expected_name", [
    ("https://github.com/owner/repo", "repo"),
    ("https://github.com/owner/repo.git", "repo"),
    ("https://github.com/owner/repo/", "repo"),
    ("https://github.com/owner/dot.dot.repo", "dot.dot.repo"),
    ("https://github.com/owner/repo-with-dashes_and_underscores", "repo-with-dashes_and_underscores"),
])
def test_repo_name_from_url_extracts_the_repo_name(url, expected_name):
    assert repo_name_from_url(url) == expected_name


@pytest.mark.parametrize("bad_url", [
    "not a url at all",
    "http://github.com/owner/repo",           # not https
    "https://gitlab.com/owner/repo",           # not github.com
    "https://github.com/owner",                # no repo segment
    "git@github.com:owner/repo.git",           # ssh form, out of scope
    "https://github.com/owner/repo; rm -rf /", # not a real repo path
    "--upload-pack=evil",                      # argv-injection shaped
])
def test_repo_name_from_url_rejects_anything_not_a_plain_github_https_url(bad_url):
    with pytest.raises(InvalidRepositoryUrl):
        repo_name_from_url(bad_url)


def test_clone_rejects_a_non_github_url_without_touching_the_filesystem(tmp_path):
    dest = tmp_path / "dest"
    with pytest.raises(InvalidRepositoryUrl):
        clone("https://gitlab.com/owner/repo", dest)
    assert not dest.exists()


def test_clone_rejects_an_argv_injection_shaped_url(tmp_path):
    dest = tmp_path / "dest"
    with pytest.raises(InvalidRepositoryUrl):
        clone("--upload-pack=touch /tmp/pwned", dest)
    assert not dest.exists()


def test_clone_refuses_to_overwrite_an_existing_destination(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "marker.txt").write_text("already here", encoding="utf-8")

    with pytest.raises(ProjectGitError, match="already exists"):
        clone("https://github.com/octocat/Hello-World", dest)

    # untouched -- the existence check must run before anything destructive
    assert (dest / "marker.txt").read_text(encoding="utf-8") == "already here"
