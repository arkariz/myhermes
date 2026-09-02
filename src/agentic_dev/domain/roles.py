"""Loaders for config/agents.yaml and config/models.yaml.

Kept separate from state_machine.py's WorkflowDefinition because these two
files describe *roles* (budgets, context mode, model routing), not the state
graph -- different validation concerns, different lifetimes (workflow.yaml
almost never changes; models.yaml changes whenever a provider is swapped).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

from .context.budget import ReadBudget
from .sessions import SessionPolicy

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:-([^}]*))?\}")


def resolve_env_placeholders(value: str, env: Mapping[str, str]) -> str:
    """Expand `${VAR:-default}` -- models.yaml's own syntax, not YAML's.

    Plain yaml.safe_load leaves these as literal strings, so this is a
    small, deliberate second pass rather than a full templating engine.

    `env` is injected rather than read from `os.environ` directly -- this
    module is domain (L0): it may parse, it may not locate or reach outside
    itself for its own inputs. The caller (an entrypoint, or a test) decides
    what "the environment" means here.

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
        return env.get(var) or (default or "")
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
    # Which named skill files Hermes loads for this role (--skills, a
    # comma-separated CLI flag) -- distinct from `toolsets` containing the
    # literal string "skills" (the toolset that lets an agent load skill
    # files at all). Empty means Hermes's own default skill selection.
    skills: tuple[str, ...] = ()
    budget: RoleBudget = field(default_factory=lambda: RoleBudget(12000, 4000))
    read_budget: ReadBudget | None = None
    allowlist: tuple[str, ...] = ()
    denylist: tuple[str, ...] = ()
    denylist_reason: str | None = None


@dataclass(frozen=True)
class Timeouts:
    # How long one Hermes turn (a full boundary rebuild OR a resumed
    # continuation) may run before the invocation is killed. The single
    # timeout that's actually worth tuning per deployment -- a slower model
    # or a long guided-read turn can legitimately need more than the
    # default; everything else (index build, Telegram API calls) is a
    # fixed, rarely-tuned infra timeout that stays a plain constructor
    # default in its own adapter instead.
    hermes_turn_seconds: int = 600


@dataclass(frozen=True)
class AgentsConfig:
    roles: dict[str, RoleConfig]
    session_policy: SessionPolicy
    timeouts: Timeouts

    @classmethod
    def load(cls, path: Path | str) -> "AgentsConfig":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        defaults = raw.get("defaults") or {}
        roles: dict[str, RoleConfig] = {}
        for name, spec in (raw.get("roles") or {}).items():
            spec = spec or {}
            merged_toolsets = spec.get("toolsets", defaults.get("toolsets", []))
            merged_skills = spec.get("skills", defaults.get("skills", []))
            budget_spec = spec.get("budget") or {}
            read_budget_spec = spec.get("read_budget")
            roles[name] = RoleConfig(
                role=name,
                context_mode=spec.get("context_mode", defaults.get("context_mode", "assembled")),
                toolsets=tuple(merged_toolsets),
                skills=tuple(merged_skills),
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
            )

        sessions_spec = raw.get("sessions") or {}
        session_policy = SessionPolicy(
            max_session_turns=int(sessions_spec.get("max_session_turns", 12)),
            max_session_age_minutes=int(sessions_spec.get("max_session_age_minutes", 120)),
        )

        timeouts_spec = raw.get("timeouts") or {}
        timeouts = Timeouts(
            hermes_turn_seconds=int(timeouts_spec.get("hermes_turn_seconds", 600)),
        )

        return cls(roles=roles, session_policy=session_policy, timeouts=timeouts)

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
    def load(cls, path: Path | str, env: Mapping[str, str]) -> "ModelsConfig":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        routes: dict[str, ModelRoute] = {}
        for role, spec in (raw.get("routing") or {}).items():
            spec = spec or {}
            routes[role] = ModelRoute(
                provider=resolve_env_placeholders(str(spec.get("provider", "")), env),
                model=resolve_env_placeholders(str(spec.get("model", "")), env),
            )
        return cls(routes=routes)

    def get(self, role: str) -> ModelRoute:
        if role not in self.routes:
            raise KeyError(f"no model route for role {role!r} in models.yaml")
        return self.routes[role]
