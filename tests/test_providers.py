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


# ---- ReferencedFilesProvider ------------------------------------------------


def test_referenced_files_are_handed_over_as_paths_not_content():
    provider = ReferencedFilesProvider()
    items = provider.collect(make_request(referenced_paths=("lib/main.dart", "test/main_test.dart")))
    assert {i.key for i in items} == {"lib/main.dart", "test/main_test.dart"}
    assert all(i.is_reference for i in items)
