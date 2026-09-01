"""telegram_bot.routing -- chat/topic -> project_id, backed by the real
ProjectRegistry against a temp projects.yaml."""

import pytest

from agentic_dev.adapters.registry import ProjectRegistry
from agentic_dev.entrypoints.telegram.routing import RoutingError, link_project, resolve_project


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry(tmp_path / "projects.yaml")
    reg.register("toy", host_path="a", state_path="b")
    return reg


def test_unlinked_chat_raises_routing_error(registry):
    with pytest.raises(RoutingError):
        resolve_project(registry, chat_id=123, thread_id=None)


def test_link_then_resolve_round_trips(registry):
    link_project(registry, "toy", chat_id=123, thread_id=None)
    entry = resolve_project(registry, chat_id=123, thread_id=None)
    assert entry.name == "toy"


def test_different_forum_topics_route_independently(registry):
    registry.register("other", host_path="c", state_path="d")
    link_project(registry, "toy", chat_id=100, thread_id=1)
    link_project(registry, "other", chat_id=100, thread_id=2)

    assert resolve_project(registry, chat_id=100, thread_id=1).name == "toy"
    assert resolve_project(registry, chat_id=100, thread_id=2).name == "other"
