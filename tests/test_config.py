"""Config loaders for agents.yaml / models.yaml, tested against both the
real shipped files and synthetic edge cases."""

import os

import pytest

from agentic_dev.domain.roles import AgentsConfig, ModelsConfig, resolve_env_placeholders


# ---- resolve_env_placeholders ----------------------------------------


def test_resolves_env_var_when_set(monkeypatch):
    monkeypatch.setenv("PLANNER_MODEL", "custom/model")
    assert resolve_env_placeholders("${PLANNER_MODEL:-anthropic/claude-sonnet-4.6}", os.environ) == "custom/model"


def test_falls_back_to_default_when_env_var_unset(monkeypatch):
    monkeypatch.delenv("PLANNER_MODEL", raising=False)
    assert resolve_env_placeholders("${PLANNER_MODEL:-anthropic/claude-sonnet-4.6}", os.environ) == "anthropic/claude-sonnet-4.6"


def test_leaves_plain_strings_untouched():
    assert resolve_env_placeholders("openrouter", os.environ) == "openrouter"


def test_falls_back_to_default_when_env_var_is_set_but_empty(monkeypatch):
    # Regression test: Docker Compose's own ${VAR:-} substitution sets an
    # unset var to VAR= in the container -- present, empty, not absent.
    # `os.environ.get(var, default)` alone returns "" the moment the key
    # merely exists, silently overriding the real default. This was the
    # reason compose.yaml had to duplicate every role's model a second
    # time; the fix here retires that duplication.
    monkeypatch.setenv("PLANNER_MODEL", "")
    assert resolve_env_placeholders("${PLANNER_MODEL:-anthropic/claude-sonnet-4.6}", os.environ) == "anthropic/claude-sonnet-4.6"


# ---- AgentsConfig, against the real shipped file -----------------------


def test_loads_the_real_agents_yaml(template_config_dir):
    cfg = AgentsConfig.load(template_config_dir / "agents.yaml")
    assert "planner" in cfg.roles
    assert "qa" in cfg.roles


def test_qa_denylist_matches_the_pang_derived_rule(template_config_dir):
    cfg = AgentsConfig.load(template_config_dir / "agents.yaml")
    qa = cfg.get("qa")
    assert "artifacts/tech-plan.md" in qa.denylist
    assert qa.denylist_reason is not None


def test_document_roles_are_assembled_and_code_roles_are_guided(template_config_dir):
    cfg = AgentsConfig.load(template_config_dir / "agents.yaml")
    assert cfg.get("planner").context_mode == "assembled"
    assert cfg.get("builder").context_mode == "guided"
    assert cfg.get("builder").read_budget is not None
    assert cfg.get("planner").read_budget is None


def test_unknown_role_raises(template_config_dir):
    cfg = AgentsConfig.load(template_config_dir / "agents.yaml")
    with pytest.raises(KeyError):
        cfg.get("not-a-role")


def test_defaults_apply_when_a_role_omits_toolsets(tmp_path):
    path = tmp_path / "agents.yaml"
    path.write_text("""
defaults:
  toolsets: [skills]
  context_mode: assembled
roles:
  planner: {}
""")
    cfg = AgentsConfig.load(path)
    assert cfg.get("planner").toolsets == ("skills",)
    assert cfg.get("planner").context_mode == "assembled"


# ---- ModelsConfig, against the real shipped file -----------------------


def test_loads_the_real_models_yaml(monkeypatch, template_config_dir):
    monkeypatch.delenv("PLANNER_MODEL", raising=False)
    cfg = ModelsConfig.load(template_config_dir / "models.yaml", env=os.environ)
    route = cfg.get("planner")
    assert route.provider == "openrouter"
    assert route.model  # resolved to the default, not left as a placeholder
    assert "${" not in route.model


def test_env_override_flows_through_models_yaml(monkeypatch, template_config_dir):
    monkeypatch.setenv("BUILDER_MODEL", "test/override-model")
    cfg = ModelsConfig.load(template_config_dir / "models.yaml", env=os.environ)
    assert cfg.get("builder").model == "test/override-model"


def test_unknown_role_raises_for_models_too(template_config_dir):
    cfg = ModelsConfig.load(template_config_dir / "models.yaml", env=os.environ)
    with pytest.raises(KeyError):
        cfg.get("not-a-role")
