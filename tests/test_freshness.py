"""indexing.freshness -- real git subprocess calls (same "no mocking"
reasoning as test_artifact_versioning.py: the property under test is git's
own behavior), with a fake in-memory indexer standing in for
DartAnalyzerIndexer so these tests need no Dart SDK at all.
"""

from __future__ import annotations

import subprocess

from indexing.freshness import current_git_revision, ensure_fresh, load_metadata
from indexing.port import IndexEdge, IndexNode, IndexResult


class FakeIndexer:
    """Counts how many times build() actually ran -- the thing these tests
    need to observe that a real indexer's return value wouldn't show."""

    def __init__(self):
        self.build_count = 0

    def supports(self, project_root):
        return True

    def build(self, project_root):
        self.build_count += 1
        return IndexResult(
            nodes=(IndexNode(id=f"build-{self.build_count}", kind="file",
                              name="x", file="x", line=0),),
            edges=(),
        )


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _commit(path, filename, content, message):
    (path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)


def test_current_git_revision_is_none_outside_a_repo(tmp_path):
    assert current_git_revision(tmp_path) is None


def test_current_git_revision_is_none_for_a_directory_that_does_not_exist_yet(tmp_path):
    # A project just /create'd from Telegram, before any source has been
    # dropped into its host_path -- a real case, not an error. Regression
    # test: subprocess.run raises before this guard existed.
    assert current_git_revision(tmp_path / "does-not-exist-yet") is None


def test_current_git_revision_matches_head(tmp_path):
    _init_git_repo(tmp_path)
    _commit(tmp_path, "a.txt", "1", "first")
    rev = current_git_revision(tmp_path)
    assert rev is not None and len(rev) == 40


def test_ensure_fresh_builds_on_first_call(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    _commit(project, "a.dart", "class A {}", "first")

    index_dir = tmp_path / "indexes"
    indexer = FakeIndexer()

    graph, revision = ensure_fresh(indexer, project, index_dir)

    assert indexer.build_count == 1
    assert revision == current_git_revision(project)
    assert graph.nodes[0].id == "build-1"


def test_ensure_fresh_reuses_the_cache_when_unchanged(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    _commit(project, "a.dart", "class A {}", "first")

    index_dir = tmp_path / "indexes"
    indexer = FakeIndexer()

    ensure_fresh(indexer, project, index_dir)
    graph, revision = ensure_fresh(indexer, project, index_dir)  # no new commit

    assert indexer.build_count == 1  # not rebuilt
    assert graph.nodes[0].id == "build-1"  # cached result, not a fresh one


def test_ensure_fresh_rebuilds_after_a_new_commit(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    _commit(project, "a.dart", "class A {}", "first")

    index_dir = tmp_path / "indexes"
    indexer = FakeIndexer()

    _, first_rev = ensure_fresh(indexer, project, index_dir)
    _commit(project, "a.dart", "class A { int x; }", "second")
    graph, second_rev = ensure_fresh(indexer, project, index_dir)

    assert indexer.build_count == 2
    assert first_rev != second_rev
    assert graph.nodes[0].id == "build-2"


def test_ensure_fresh_persists_metadata(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _init_git_repo(project)
    _commit(project, "a.dart", "class A {}", "first")

    index_dir = tmp_path / "indexes"
    ensure_fresh(FakeIndexer(), project, index_dir)

    meta = load_metadata(index_dir)
    assert meta is not None
    assert meta.tool == "FakeIndexer"
    assert meta.source_revision == current_git_revision(project)


def test_ensure_fresh_without_git_always_rebuilds(tmp_path):
    project = tmp_path / "project"  # never git-initialized
    project.mkdir()
    index_dir = tmp_path / "indexes"
    indexer = FakeIndexer()

    ensure_fresh(indexer, project, index_dir)
    ensure_fresh(indexer, project, index_dir)

    assert indexer.build_count == 2  # no revision to compare against -> always fresh
