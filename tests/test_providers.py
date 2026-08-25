"""Concrete context providers -- Phase 1 scope.

Providers propose candidates; they never decide what survives (that's the
builder's job). These tests just check each provider reads the right thing
from disk and shapes it into a correct ContextItem, and that a missing
source is handled by returning nothing rather than crashing the turn.
"""

from orchestrator.context.builder import BuildRequest
from orchestrator.context.providers import (
    ArtifactSectionProvider,
    DecisionsProvider,
    DiffProvider,
    IndexProvider,
    ProjectIdentityProvider,
    ReferencedFilesProvider,
    RecentTurnsProvider,
    RoleSoulProvider,
    SummaryProvider,
)
from orchestrator.store import ProjectStore


def make_request(**overrides):
    defaults = dict(
        project_id="toy", role="planner", workflow_state="planning",
        task="do the thing", mode="assembled", kind="full",
        max_input_tokens=1000,
    )
    defaults.update(overrides)
    return BuildRequest(**defaults)


# ---- RoleSoulProvider -----------------------------------------------------


def test_role_soul_provider_reads_the_matching_file(tmp_path):
    (tmp_path / "planner.md").write_text("You are the planner.", encoding="utf-8")
    provider = RoleSoulProvider(souls_dir=tmp_path)
    items = provider.collect(make_request(role="planner"))
    assert items[0].content == "You are the planner."
    assert items[0].layer == 1 and items[0].volatility == 0


def test_role_soul_provider_returns_nothing_for_a_missing_soul(tmp_path):
    provider = RoleSoulProvider(souls_dir=tmp_path)
    assert provider.collect(make_request(role="ghost-role")) == []


# ---- ProjectIdentityProvider ------------------------------------------------


def test_project_identity_reads_context_md_when_present(tmp_path):
    store = ProjectStore(tmp_path)
    (tmp_path / "context.md").write_text("# Bonked\n\nA habit tracker.", encoding="utf-8")
    provider = ProjectIdentityProvider(store, project_id="bonked")
    items = provider.collect(make_request())
    assert "habit tracker" in items[0].content


def test_project_identity_falls_back_when_context_md_is_missing(tmp_path):
    store = ProjectStore(tmp_path)
    provider = ProjectIdentityProvider(store, project_id="bonked")
    items = provider.collect(make_request())
    assert "bonked" in items[0].content
    assert items[0].key == "context.md"


# ---- ArtifactSectionProvider ------------------------------------------------


def test_artifact_provider_reads_the_named_artifact(tmp_path):
    store = ProjectStore(tmp_path)
    store.artifact("prd.md").parent.mkdir(parents=True, exist_ok=True)
    store.artifact("prd.md").write_text("# PRD\n\nScope: v1.", encoding="utf-8")
    provider = ArtifactSectionProvider(store)
    items = provider.collect(make_request(artifact_name="prd.md"))
    assert items[0].content == "# PRD\n\nScope: v1."
    assert items[0].key == "artifacts/prd.md"


def test_artifact_provider_returns_nothing_without_an_artifact_name(tmp_path):
    store = ProjectStore(tmp_path)
    provider = ArtifactSectionProvider(store)
    assert provider.collect(make_request()) == []


def test_artifact_provider_returns_nothing_when_the_file_does_not_exist_yet(tmp_path):
    store = ProjectStore(tmp_path)
    provider = ArtifactSectionProvider(store)
    assert provider.collect(make_request(artifact_name="prd.md")) == []


# ---- DecisionsProvider -------------------------------------------------


def test_decisions_provider_reads_every_decision_file(tmp_path):
    store = ProjectStore(tmp_path)
    store.decisions_dir().mkdir(parents=True, exist_ok=True)
    (store.decisions_dir() / "001-scope.md").write_text("Single-child MVP.", encoding="utf-8")
    (store.decisions_dir() / "002-bilingual.md").write_text("Bilingual support.", encoding="utf-8")
    provider = DecisionsProvider(store)
    items = provider.collect(make_request())
    assert {i.key for i in items} == {"decisions/001-scope.md", "decisions/002-bilingual.md"}


def test_decisions_provider_returns_nothing_when_no_decisions_yet(tmp_path):
    store = ProjectStore(tmp_path)
    provider = DecisionsProvider(store)
    assert provider.collect(make_request()) == []


# ---- RecentTurnsProvider -------------------------------------------------


def test_recent_turns_provider_returns_only_the_last_n(tmp_path):
    store = ProjectStore(tmp_path)
    for i in range(1, 6):
        store.append_conversation_turn("planning", "agent", f"turn {i}")
    provider = RecentTurnsProvider(store, count=3)
    items = provider.collect(make_request(workflow_state="planning"))
    assert len(items) == 3
    assert "turn 4" in items[0].content or "turn 5" in items[-1].content


def test_recent_turns_provider_returns_nothing_before_any_conversation(tmp_path):
    store = ProjectStore(tmp_path)
    provider = RecentTurnsProvider(store)
    assert provider.collect(make_request()) == []


def test_recent_turns_provider_is_scoped_to_the_current_workflow_state(tmp_path):
    store = ProjectStore(tmp_path)
    store.append_conversation_turn("architecture", "agent", "unrelated state's turn")
    provider = RecentTurnsProvider(store)
    items = provider.collect(make_request(workflow_state="planning"))
    assert items == []


# ---- SummaryProvider -----------------------------------------------------


def test_summary_provider_reads_the_current_states_summary(tmp_path):
    store = ProjectStore(tmp_path)
    store.write_summary("planning", "Decided X. Open question: Y.")
    provider = SummaryProvider(store)
    items = provider.collect(make_request(workflow_state="planning"))
    assert items[0].content == "Decided X. Open question: Y."
    assert items[0].key == "summaries/planning.md"
    assert items[0].layer == 4


def test_summary_provider_returns_nothing_before_any_summary_exists(tmp_path):
    store = ProjectStore(tmp_path)
    provider = SummaryProvider(store)
    assert provider.collect(make_request()) == []


def test_summary_provider_is_scoped_to_the_current_workflow_state(tmp_path):
    store = ProjectStore(tmp_path)
    store.write_summary("architecture", "unrelated state's summary")
    provider = SummaryProvider(store)
    assert provider.collect(make_request(workflow_state="planning")) == []


# ---- IndexProvider ---------------------------------------------------------


def test_index_provider_returns_nothing_before_any_index_exists(tmp_path):
    store = ProjectStore(tmp_path)
    assert IndexProvider(store).collect(make_request()) == []


def test_index_provider_lists_files_from_a_saved_graph(tmp_path):
    from indexing.freshness import save_graph
    from indexing.port import IndexNode, IndexResult

    store = ProjectStore(tmp_path)
    save_graph(store.index_dir(), IndexResult(
        nodes=(
            IndexNode(id="lib/a.dart", kind="file", name="lib/a.dart", file="lib/a.dart", line=0),
            IndexNode(id="lib/a.dart#A", kind="class", name="A", file="lib/a.dart", line=1),
            IndexNode(id="lib/b.dart", kind="file", name="lib/b.dart", file="lib/b.dart", line=0),
        ),
        edges=(),
    ))

    items = IndexProvider(store).collect(make_request())

    assert {i.key for i in items} == {"lib/a.dart", "lib/b.dart"}
    assert all(i.is_reference for i in items)


def test_index_provider_deduplicates_against_explicit_references(tmp_path):
    from indexing.freshness import save_graph
    from indexing.port import IndexNode, IndexResult

    store = ProjectStore(tmp_path)
    save_graph(store.index_dir(), IndexResult(
        nodes=(IndexNode(id="lib/a.dart", kind="file", name="lib/a.dart", file="lib/a.dart", line=0),),
        edges=(),
    ))

    items = IndexProvider(store).collect(make_request(referenced_paths=("lib/a.dart",)))

    assert items == []


def test_index_provider_expands_a_referenced_files_relative_import(tmp_path):
    from indexing.freshness import save_graph
    from indexing.port import IndexEdge, IndexNode, IndexResult

    store = ProjectStore(tmp_path)
    save_graph(store.index_dir(), IndexResult(
        nodes=(
            IndexNode(id="lib/a.dart", kind="file", name="lib/a.dart", file="lib/a.dart", line=0),
            IndexNode(id="lib/models/b.dart", kind="file", name="lib/models/b.dart", file="lib/models/b.dart", line=0),
            IndexNode(id="lib/c.dart", kind="file", name="lib/c.dart", file="lib/c.dart", line=0),
        ),
        edges=(
            IndexEdge(src="lib/a.dart", dst="models/b.dart", relation="imports"),
            IndexEdge(src="lib/a.dart", dst="dart:core", relation="imports"),
        ),
    ))

    items = IndexProvider(store).collect(make_request(referenced_paths=("lib/a.dart",)))

    expanded = [i for i in items if i.key == "lib/models/b.dart"]
    assert len(expanded) == 1
    assert expanded[0].reason == "imported by a referenced file"
    assert expanded[0].priority == 7
    # lib/c.dart is untouched -- it's neither referenced nor imported.
    assert any(i.key == "lib/c.dart" and i.priority == 9 for i in items)


def test_index_provider_resolves_a_self_package_import_via_pubspec(tmp_path):
    from indexing.freshness import save_graph
    from indexing.port import IndexEdge, IndexNode, IndexResult

    (tmp_path / "pubspec.yaml").write_text("name: toy_app\nversion: 1.0.0\n", encoding="utf-8")

    store = ProjectStore(tmp_path / ".agentic-dev")
    save_graph(store.index_dir(), IndexResult(
        nodes=(
            IndexNode(id="lib/a.dart", kind="file", name="lib/a.dart", file="lib/a.dart", line=0),
            IndexNode(id="lib/widgets/b.dart", kind="file", name="lib/widgets/b.dart", file="lib/widgets/b.dart", line=0),
        ),
        edges=(
            IndexEdge(src="lib/a.dart", dst="package:toy_app/widgets/b.dart", relation="imports"),
        ),
    ))

    items = IndexProvider(store, project_source_root=tmp_path).collect(
        make_request(referenced_paths=("lib/a.dart",))
    )

    assert any(i.key == "lib/widgets/b.dart" and i.reason == "imported by a referenced file" for i in items)


def test_index_provider_ranks_files_matching_a_task_keyword_above_the_rest(tmp_path):
    from indexing.freshness import save_graph
    from indexing.port import IndexNode, IndexResult

    store = ProjectStore(tmp_path)
    save_graph(store.index_dir(), IndexResult(
        nodes=(
            IndexNode(id="lib/checkout.dart", kind="file", name="lib/checkout.dart", file="lib/checkout.dart", line=0),
            IndexNode(id="lib/unrelated.dart", kind="file", name="lib/unrelated.dart", file="lib/unrelated.dart", line=0),
        ),
        edges=(),
    ))

    items = IndexProvider(store).collect(make_request(task="Fix the checkout flow bug"))

    by_key = {i.key: i for i in items}
    assert by_key["lib/checkout.dart"].priority == 8
    assert "checkout" in by_key["lib/checkout.dart"].reason
    assert by_key["lib/unrelated.dart"].priority == 9


# ---- ReferencedFilesProvider ------------------------------------------------


def test_referenced_files_are_handed_over_as_paths_not_content():
    provider = ReferencedFilesProvider()
    items = provider.collect(make_request(referenced_paths=("lib/main.dart", "test/main_test.dart")))
    assert {i.key for i in items} == {"lib/main.dart", "test/main_test.dart"}
    assert all(i.is_reference for i in items)


# ---- DiffProvider ------------------------------------------------------------


def _git_repo(path):
    import subprocess
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _commit(path, filename, content, message):
    import subprocess
    (path / filename).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=path, check=True)


def test_diff_provider_returns_nothing_without_a_project_source_root(tmp_path):
    store = ProjectStore(tmp_path)
    assert DiffProvider(store, None).collect(make_request()) == []


def test_diff_provider_returns_nothing_for_a_non_git_project(tmp_path):
    store = ProjectStore(tmp_path / "state")
    project = tmp_path / "source"
    project.mkdir()
    assert DiffProvider(store, project).collect(make_request()) == []


def test_diff_provider_returns_nothing_before_a_base_revision_is_recorded(tmp_path):
    project = tmp_path / "source"
    _git_repo(project)
    _commit(project, "a.dart", "class A {}", "first")

    store = ProjectStore(tmp_path / "state")
    assert DiffProvider(store, project).collect(make_request()) == []


def test_diff_provider_shows_the_real_diff_since_the_base_revision(tmp_path):
    project = tmp_path / "source"
    _git_repo(project)
    _commit(project, "a.dart", "class A {}", "first")

    store = ProjectStore(tmp_path / "state")
    store.update_state(implementation_base_revision=None)
    _commit(project, "b.dart", "class B {}", "second")

    items = DiffProvider(store, project).collect(make_request())

    assert len(items) == 1
    assert items[0].key == "git-diff"
    assert not items[0].is_reference
    assert "+class B {}" in items[0].content


def test_diff_provider_returns_nothing_when_there_are_no_changes_since_the_base(tmp_path):
    project = tmp_path / "source"
    _git_repo(project)
    _commit(project, "a.dart", "class A {}", "first")

    store = ProjectStore(tmp_path / "state")
    from orchestrator.project_git import current_revision
    store.update_state(implementation_base_revision=current_revision(project))

    assert DiffProvider(store, project).collect(make_request()) == []
