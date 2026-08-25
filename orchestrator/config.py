"""Loaders for config/agents.yaml and config/models.yaml.

Kept separate from state_machine.py's WorkflowDefinition because these two
files describe *roles* (budgets, context mode, model routing), not the state
graph -- different validation concerns, different lifetimes (workflow.yaml
almost never changes; models.yaml changes whenever a provider is swapped).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .context.budget import ReadBudget

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")


def resolve_env_placeholders(value: str) -> str:
    """Expand `${VAR:-default}` -- models.yaml's own syntax, not YAML's.

    Plain yaml.safe_load leaves these as literal strings, so this is a
    small, deliberate second pass rather than a full templating engine.

    An env var that is SET BUT EMPTY still falls back to `default`, not to
    the empty string -- `env.get(var, default)` alone would return "" the
    moment the key merely exists, which is exactly the failure mode Docker
    Compose's own `${VAR:-}` substitution produces for anyone who left a
    var unset in `.env` (present in the container as `VAR=`, not absent).
    Found live: this was the reason compose.yaml had to duplicate every
    role's default model a second time, just to avoid the empty string
    silently winning over the real default.
    """
    def _sub(match: re.Match) -> str:
        var, _, default = match.groups()
        return os.environ.get(var) or (default or "")
    return _ENV_PLACEHOLDER.sub(_sub, value)


@dataclass(frozen=True)
class RoleBudget:
    max_input_tokens: int
    max_output_tokens: int


@dataclass(frozen=True)
class RoleConfig:
    role: str
    context_mode: str                     # "assembled" | "guided"
    toolsets: tuple[str, ...] = ()
    budget: RoleBudget = field(default_factory=lambda: RoleBudget(12000, 4000))
    read_budget: ReadBudget | None = None
    allowlist: tuple[str, ...] = ()
    denylist: tuple[str, ...] = ()
    denylist_reason: str | None = None
    rtk: bool = False


@dataclass(frozen=True)
class AgentsConfig:
    roles: dict[str, RoleConfig]
    session_policy_raw: dict[str, Any]

    @classmethod
    def load(cls, path: Path | str) -> "AgentsConfig":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        defaults = raw.get("defaults") or {}
        roles: dict[str, RoleConfig] = {}
        for name, spec in (raw.get("roles") or {}).items():
            spec = spec or {}
            merged_toolsets = spec.get("toolsets", defaults.get("toolsets", []))
            budget_spec = spec.get("budget") or {}
            read_budget_spec = spec.get("read_budget")
            roles[name] = RoleConfig(
                role=name,
                context_mode=spec.get("context_mode", defaults.get("context_mode", "assembled")),
                toolsets=tuple(merged_toolsets),
                budget=RoleBudget(
                    max_input_tokens=int(budget_spec.get("max_input_tokens", 12000)),
                    max_output_tokens=int(budget_spec.get("max_output_tokens", 4000)),
                ),
                read_budget=ReadBudget(
                    max_files=int(read_budget_spec.get("max_files", 40)),
                    max_bytes=int(read_budget_spec.get("max_bytes", 400_000)),
                    max_tool_calls=int(read_budget_spec.get("max_tool_calls", 120)),
                ) if read_budget_spec else None,
                allowlist=tuple(spec.get("context_allowlist", [])),
                denylist=tuple(spec.get("context_denylist", [])),
                denylist_reason=spec.get("denylist_reason"),
                rtk=bool(spec.get("rtk", False)),
            )
        return cls(roles=roles, session_policy_raw=raw.get("sessions") or {})

    def get(self, role: str) -> RoleConfig:
        if role not in self.roles:
            raise KeyError(f"no config for role {role!r} in agents.yaml")
        return self.roles[role]


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str


@dataclass(frozen=True)
class ModelsConfig:
    routes: dict[str, ModelRoute]

    @classmethod
    def load(cls, path: Path | str) -> "ModelsConfig":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        routes: dict[str, ModelRoute] = {}
        for role, spec in (raw.get("routing") or {}).items():
            spec = spec or {}
            routes[role] = ModelRoute(
                provider=resolve_env_placeholders(str(spec.get("provider", ""))),
                model=resolve_env_placeholders(str(spec.get("model", ""))),
            )
        return cls(routes=routes)

    def get(self, role: str) -> ModelRoute:
        if role not in self.routes:
            raise KeyError(f"no model route for role {role!r} in models.yaml")
        return self.routes[role]
