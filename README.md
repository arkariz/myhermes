# Agentic Dev

Local-first, Dockerized agentic engineering workspace — a redesign of
[isfaaghyth/pang](https://github.com/isfaaghyth/pang) with a hybrid Hermes
memory model, role-split context assembly, and a deterministic workflow
engine. Full architecture and rationale: `docs/plan.md` (mirrors the approved
planning-session output).

## Status

**Docker and Python are working. Phase 1 Steps 0–5 are done.** The Hermes
spike (Step 2) and the invocation layer it gates (Step 5) were both run and
debugged for real against `hermes` v0.20.5 + OpenRouter (`openai/gpt-4o-mini`)
— see `docs/plan.md` § "Phase 1 spike" for the full results table.

```bash
pip install -e ".[dev]"
pytest
```

122/122 tests pass. The test suite is the actual specification of the
invariants below — read it if the prose and the code ever disagree.

### Two real bugs the spike found, both fixed

**1. `HERMES_PROFILE` does not isolate memory.** Hermes runs a background
writer that persists user statements to `~/.hermes/memories/MEMORY.md` and
re-injects it into every later turn, regardless of `--ignore-rules`,
`--safe-mode`, or `memory.memory_enabled: false`. That file is scoped to
`HERMES_HOME`, not to the active profile — two different profiles under one
`HERMES_HOME` both recalled a secret planted under the other. Verified live
(planted a distinct secret, confirmed cross-profile recall).

**Fixed:** every project now gets its own `HERMES_HOME` directory
(`ProjectStore.hermes_home()`, `SessionPolicy.home_dir()`) inside that
project's own `agent-state` tree. `HERMES_PROFILE` now defaults to `role`
scope rather than `role_project`, since sharing a profile name across
projects costs nothing once `home_dir` is doing the actual isolating.

**2. `--usage-file` and `--resume` don't work on the same invocation.**
`hermes -z`'s actual implementation (`hermes_cli/oneshot.py::run_oneshot`)
has no `resume` parameter at all — `-z --resume` is dead code even though
argparse silently accepts the flag. `chat -q --resume` genuinely resumes
(confirmed: recalled a fact from 3 turns back) but `chat` has no
`--usage-file` option (hard argparse error). A further wrinkle: on
`chat -q --resume`, the `session_id:` marker prints to **stderr**, so there's
no stdout separator between a CLI banner and the real response — an early
fix that looked for that marker on stdout let the banner leak straight into
the parsed response.

**Fixed** in `runtime/hermes.py`, split along the same seam the state
machine already uses: full turns (never resume) go through `-z` and get
`--usage-file` for real; continuation turns go through `chat -q --resume`
and recover usage after the fact via `hermes sessions export`, with response
text cleaned by known CLI status-icon prefixes rather than a marker that
doesn't reliably exist on that path.

Provider-side prompt caching was confirmed working as designed (non-zero
`cache_read_tokens`, growing turn over turn on a resumed session).

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
| `runtime/hermes.py` | Hermes invocation — verified live against real API calls, not just unit-tested against a fake subprocess |

## What's still a spec, not code

- `runtime/server.py` (the FastAPI service wrapping `runtime/hermes.py` for
  the orchestrator to call over HTTP) and an RTK output-compression wrapper.
  `docker/spike/Dockerfile` proves the base image works (Hermes CLI +
  OpenRouter installed and callable); the real `docker/agent-runtime/Dockerfile`
  still needs Flutter/Android/JDK/RTK added on top of it.
- `orchestrator/jobs.py` (Step 6 — async turn execution tying state machine +
  context builder + `runtime/hermes.py` together, with attempt bounding).
- `telegram/` (Phase 2).
- `tools/dart_indexer/`, `indexing/graphify_adapter.py`.
- `compose.yaml`.

## Next: Phase 1 Step 6

`orchestrator/jobs.py` — the piece that actually calls `runtime/hermes.py`
for a turn: pick full vs. continuation from `SessionManager.decide()`, hand
the resulting `HermesRequest` to `runtime.hermes.run()` with
`store.hermes_home()`, persist the turn via `ProjectStore`, and bound retries
with `state_machine.fail()`. Then the CLI driver (`orchestrator/cli.py`) to
run this Phase 1 demo end to end without Telegram.
