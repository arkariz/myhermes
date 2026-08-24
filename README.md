# Agentic Dev

Local-first, Dockerized agentic engineering workspace — a redesign of
[isfaaghyth/pang](https://github.com/isfaaghyth/pang) with a hybrid Hermes
memory model, role-split context assembly, and a deterministic workflow
engine. Full architecture and rationale: `docs/plan.md` (mirrors the approved
planning-session output).

## Status

**Docker and Python are working. Phase 1 Steps 0–4 are done; Step 2 (the
Hermes spike) has been run for real** against `hermes` v0.20.5 + OpenRouter
(`openai/gpt-4o-mini`), not simulated — see `docs/plan.md` § "Phase 1 spike"
for the full results table.

```bash
pip install -e ".[dev]"
pytest
```

94/94 tests pass. The test suite is the actual specification of the
invariants below — read it if the prose and the code ever disagree.

### The spike found a real bug in the original design

`HERMES_PROFILE` does **not** isolate memory. Hermes runs a background writer
that persists user statements to `~/.hermes/memories/MEMORY.md` and
re-injects it into every later turn, regardless of `--ignore-rules`,
`--safe-mode`, or `memory.memory_enabled: false`. That file is scoped to
`HERMES_HOME`, not to the active profile — two different profiles under one
`HERMES_HOME` both recalled a secret planted under the other. This was
verified live (planted a distinct secret, confirmed cross-profile recall),
not assumed from documentation.

**Fixed:** every project now gets its own `HERMES_HOME` directory
(`ProjectStore.hermes_home()`, `SessionPolicy.home_dir()`) inside that
project's own `agent-state` tree. `HERMES_PROFILE` is retained only for
role-scoped model/skill/toolset config *within* one project's already-isolated
home, and now defaults to `role` scope rather than `role_project`, since
sharing a profile name across projects costs nothing once `home_dir` is doing
the actual isolating.

`--resume` and provider-side prompt caching were both confirmed working as
designed (non-zero `cache_read_tokens` on a repeated prompt; a resumed session
genuinely recalls prior turns).

## What's implemented

| Module | Covers |
|---|---|
| `config/workflow.yaml` | The state graph — autonomous / collaborative / gate states, retry bounds |
| `config/agents.yaml` | Role budgets, context mode (assembled vs guided), denylists |
| `orchestrator/state_machine.py` | Loads + validates the graph; `advance()` refuses anything but a typed approval |
| `orchestrator/sessions.py` | The 8 boundary triggers deciding resume vs. rebuild of a Hermes session |
| `orchestrator/context/` | Builder, budget/volatility ordering, denylist enforcement, manifest |
| `orchestrator/approvals.py` | §31 human-input semantics + `\`\`\`decision` block parsing |
| `orchestrator/store.py`, `events.py` | Atomic filesystem persistence, append-only event log |

## What's still a spec, not code

- `runtime/` (Hermes invocation with the corrected `HERMES_HOME` contract,
  `--usage-file` parsing, RTK wrapper) — ready to build now that the spike
  has settled the isolation model. `docker/spike/Dockerfile` proves the base
  image works (Hermes CLI + OpenRouter installed and callable); the real
  `docker/agent-runtime/Dockerfile` still needs Flutter/Android/JDK/RTK added
  on top of it.
- `telegram/` (Phase 2).
- `tools/dart_indexer/`, `indexing/graphify_adapter.py`.
- `compose.yaml`.

## Next: Phase 1 Steps 5–6

Build `runtime/hermes.py` around the verified CLI contract:
`hermes -z`/`chat -q --resume <id>`, `--provider`/`--model` explicit (config
defaults aren't reliable under `--safe-mode`), `--usage-file` for real
`input_tokens`/`cache_read_tokens`/`session_id`, and **`HERMES_HOME` set to
`ProjectStore.hermes_home()` on every call** — never a shared volume.
