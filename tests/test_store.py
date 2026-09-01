"""Filesystem store: atomic writes, event log, turn-sequence derivation.

No mocking -- everything here really touches tmp_path, because the property
under test IS the filesystem behaviour (os.replace atomicity, append-only
events, sequence numbers survive a crash).
"""

import json

from agentic_dev.adapters.storage.store import ProjectStore


def test_write_state_then_read_round_trips(tmp_path):
    store = ProjectStore(tmp_path)
    store.write_state({"workflow_state": "planning", "attempts": 0})
    assert store.read_state() == {"workflow_state": "planning", "attempts": 0}


def test_read_state_on_missing_file_returns_empty_dict(tmp_path):
    store = ProjectStore(tmp_path / "does-not-exist")
    assert store.read_state() == {}


def test_update_state_shallow_merges(tmp_path):
    store = ProjectStore(tmp_path)
    store.write_state({"a": 1, "b": 2})
    store.update_state(b=3, c=4)
    assert store.read_state() == {"a": 1, "b": 3, "c": 4}


def test_atomic_write_leaves_no_tmp_file_behind(tmp_path):
    store = ProjectStore(tmp_path)
    store.write_state({"a": 1})
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".state.yaml.")]
    assert leftovers == []


def test_events_append_and_read_back_in_order(tmp_path):
    store = ProjectStore(tmp_path)
    store.append_event({"type": "PROJECT_CREATED", "n": 1})
    store.append_event({"type": "STATE_CHANGED", "n": 2})
    events = list(store.read_events())
    assert [e["n"] for e in events] == [1, 2]


def test_events_file_is_valid_jsonl(tmp_path):
    store = ProjectStore(tmp_path)
    store.append_event({"type": "PROJECT_CREATED"})
    lines = store.events_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    json.loads(lines[0])  # must not raise


def test_turn_sequence_starts_at_one(tmp_path):
    store = ProjectStore(tmp_path)
    assert store.next_turn_sequence("planning") == 1


def test_turn_sequence_increments_from_disk_not_a_counter(tmp_path):
    store = ProjectStore(tmp_path)
    store.append_conversation_turn("planning", "agent", "first")
    store.append_conversation_turn("planning", "human", "second")
    assert store.next_turn_sequence("planning") == 3


def test_turn_sequence_is_independent_per_workflow_state(tmp_path):
    store = ProjectStore(tmp_path)
    store.append_conversation_turn("planning", "agent", "x")
    assert store.next_turn_sequence("architecture") == 1


def test_turn_artifacts_round_trip(tmp_path):
    store = ProjectStore(tmp_path)
    store.write_turn_artifact("planning-01", "prompt.md", "hello")
    assert store.read_turn_artifact("planning-01", "prompt.md") == "hello"
    assert store.read_turn_artifact("planning-01", "missing.md") is None


def test_ensure_layout_creates_expected_subdirectories(tmp_path):
    store = ProjectStore(tmp_path)
    store.ensure_layout()
    for sub in ("artifacts", "decisions", "conversations", "summaries", "turns", "indexes"):
        assert (tmp_path / sub).is_dir()


def test_hermes_home_is_inside_this_projects_own_tree(tmp_path):
    # Verified isolation boundary (Phase 1 spike): HERMES_HOME must live
    # inside the project's own agent-state tree so it can never be shared
    # with another project's tree by construction.
    store = ProjectStore(tmp_path)
    home = store.hermes_home()
    assert home.is_relative_to(tmp_path)
    assert home != tmp_path


def test_hermes_home_differs_across_projects(tmp_path):
    store_a = ProjectStore(tmp_path / "project-a")
    store_b = ProjectStore(tmp_path / "project-b")
    assert store_a.hermes_home() != store_b.hermes_home()


def test_summary_round_trips(tmp_path):
    store = ProjectStore(tmp_path)
    store.write_summary("planning", "Decided X, still open: Y.")
    assert store.read_summary("planning") == "Decided X, still open: Y."


def test_summary_missing_returns_none(tmp_path):
    store = ProjectStore(tmp_path)
    assert store.read_summary("planning") is None


def test_summary_is_independent_per_workflow_state(tmp_path):
    store = ProjectStore(tmp_path)
    store.write_summary("discovery", "discovery summary")
    store.write_summary("planning", "planning summary")
    assert store.read_summary("discovery") == "discovery summary"
    assert store.read_summary("planning") == "planning summary"
