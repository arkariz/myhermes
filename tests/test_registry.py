"""The project registry -- config/projects.yaml."""

import pytest

from agentic_dev.adapters.registry import ProjectAlreadyExists, ProjectNotFound, ProjectRegistry


def test_register_then_get_round_trips(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    reg.register("toy", host_path="D:/Projects/toy", state_path="D:/Projects/.agentic-dev/toy")

    entry = reg.get("toy")
    assert entry.name == "toy"
    assert entry.host_path == "D:/Projects/toy"
    assert entry.platform == "flutter"


def test_get_unknown_project_raises(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    with pytest.raises(ProjectNotFound):
        reg.get("ghost")


def test_registering_twice_raises(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    reg.register("toy", host_path="a", state_path="b")
    with pytest.raises(ProjectAlreadyExists):
        reg.register("toy", host_path="c", state_path="d")


def test_list_returns_sorted_project_ids(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    reg.register("zebra", host_path="a", state_path="b")
    reg.register("apple", host_path="c", state_path="d")
    assert reg.list() == ["apple", "zebra"]


def test_registry_persists_across_instances(tmp_path):
    path = tmp_path / "projects.yaml"
    ProjectRegistry(path).register("toy", host_path="a", state_path="b")
    reloaded = ProjectRegistry(path)
    assert reloaded.get("toy").host_path == "a"


def test_missing_file_starts_empty(tmp_path):
    reg = ProjectRegistry(tmp_path / "does-not-exist.yaml")
    assert reg.list() == []


def test_new_entry_has_no_telegram_link(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    reg.register("toy", host_path="a", state_path="b")
    entry = reg.get("toy")
    assert entry.telegram_chat_id is None
    assert entry.telegram_thread_id is None


def test_link_telegram_then_get_round_trips(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    reg.register("toy", host_path="a", state_path="b")
    reg.link_telegram("toy", chat_id=123, thread_id=7)

    entry = reg.get("toy")
    assert entry.telegram_chat_id == 123
    assert entry.telegram_thread_id == 7


def test_link_telegram_unknown_project_raises(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    with pytest.raises(ProjectNotFound):
        reg.link_telegram("ghost", chat_id=1, thread_id=None)


def test_find_by_telegram_matches_chat_and_thread(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    reg.register("toy", host_path="a", state_path="b")
    reg.link_telegram("toy", chat_id=123, thread_id=7)

    assert reg.find_by_telegram(chat_id=123, thread_id=7).name == "toy"
    assert reg.find_by_telegram(chat_id=123, thread_id=None) is None
    assert reg.find_by_telegram(chat_id=999, thread_id=7) is None


def test_find_by_telegram_with_no_thread_id(tmp_path):
    """A regular (non-forum) chat has no message_thread_id at all."""
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    reg.register("toy", host_path="a", state_path="b")
    reg.link_telegram("toy", chat_id=123, thread_id=None)

    assert reg.find_by_telegram(chat_id=123, thread_id=None).name == "toy"
