# Progress checklist

Tracks `docs/plan.md` against what's actually built and verified. "Verified
live" means run for real against the Hermes CLI / OpenRouter API / real
Telegram Bot API, not just passing against a mock in a unit test — see
`README.md` for the specific runs. Updated as work lands; not a historical
log (that's what git history is for).

## Phase 0 — Prerequisites

- [x] Docker Desktop + WSL2 backend
- [x] Real Python 3.12 (`.venv`), `uv`/`pip install -e ".[dev]"`

## Phase 1 — Core engine

- [x] **Step 1 — repo skeleton.** Adjusted from the original plan during
      the tidy pass: `souls/` moved under `config/souls/`; empty Phase 2+
      scaffold dirs (`indexing/`, `scripts/`, `skills/`, `tools/`) removed
      until they have real content.
- [x] **Step 2 — Hermes spike.** Verified live. Two real bugs found and
      fixed: `HERMES_PROFILE` doesn't isolate memory (`HERMES_HOME` does);
      `--usage-file` and `--resume` aren't available on the same
      invocation path (`-z` vs `chat -q --resume` split).
- [x] **Step 3 — state, sessions, events.** `state_machine.py`,
      `sessions.py` (8 boundary triggers), `store.py`, `events.py`. Pure,
      unit-tested, zero LLM calls.
- [x] **Step 4 — context builder.** `tokens.py`, `budget.py`,
      `providers.py`, `denylist.py`, `builder.py`, `manifest.py`. Assembled
      + guided modes, full + delta kinds, volatility-ordered rendering.
- [x] **Step 5 — runtime invocation + job execution.** `runtime/hermes.py`
      (verified live), `orchestrator/jobs.py` (`TurnRunner`, verified
      live), bounded `max_attempts` retries.
- [x] **Step 6 — CLI driver + incremental summaries.** `orchestrator/cli.py`
      (verified live); `orchestrator/summarizer.py` (§17.3) — the one part
      of this step that was scoped but not built until later; done now,
      wired into `TurnRunner`, optional via `models.yaml`'s `summarizer`
      route.

## Phase 2 — Telegram

- [x] `telegram_bot/bot.py`, `routing.py`, `handlers.py` — drives the same
      `TurnRunner` as the CLI. Verified live against the real Bot API and
      a real Telegram group.
- [x] `orchestrator/approval_flow.py` — `apply_approval()` extracted so
      CLI and Telegram enforce identical §31 semantics (no duplicate
      approval logic to drift apart).
- [x] Inline-button approvals, including the plan's own acceptance test:
      a stale-artifact-revision callback is rejected. Verified live.
- [x] **Beyond the original plan:** `/create` — a project can be created
      entirely from Telegram (name it, host path defaults, forum topic
      auto-created and linked). Also available from the CLI via
      `TELEGRAM_FORUM_CHAT_ID`. Verified live.
- [x] Command-suggestion menu (`setMyCommands`) for `/create`, `/link`,
      `/status`. Verified live against `getMyCommands` across every scope.
- [ ] Async inbox/outbox is a single global in-process queue, not the
      inbox-table/outbox-table split the plan sketches for durability
      across a process restart. Fine at current traffic; revisit if a
      restart mid-turn becomes a real problem.

## Cross-cutting verification (from `docs/plan.md`'s own criteria)

- [x] State machine: every transition validated, illegal transitions
      rejected, gates refuse to advance without a typed approval,
      `max_attempts` exhaustion lands in `blocked`.
- [x] Session manager: each of the 8 boundary triggers invalidates the
      session, and nothing else does.
- [x] Denylist enforcement: a QA package containing a denylisted artifact
      fails to build. Also demonstrated end-to-end in `benchmark/`.
- [x] Context builder: priority governs selection, volatility governs
      ordering, budget overflow omits with a recorded reason, dedupe
      across layers, reproducible rebuild.
- [x] Delta packages contain only what changed since the session opened.
- [x] Human-input semantics: free text never approves; `/approve` with no
      pending action doesn't approve; a stale callback is rejected.
- [x] E2E Phase 1 demo (project → discovery → planning, full + delta
      turns, real `cache_read_tokens`). Verified live.
- [x] E2E Phase 2 Telegram loop (chat → turn → approve → state advances →
      session closes). Verified live, including `/create`'s full loop.
- [ ] Guided-mode manifest reconstruction from a real `tool-log.jsonl`.
      Not applicable yet — no guided-retrieval role (`builder`/`reviewer`/
      `qa`) has a real toolset to log calls from; `ReferencedFilesProvider`
      only handles the static/explicit-paths half of guided mode so far.
- [ ] The four Hermes spike measurements as an automated regression suite
      (re-run when the Hermes version bumps). Currently a one-time live
      run recorded in `docs/plan.md`, not a repeatable check.
- [x] `POST /run` returning a parseable `usage.json` + `session_id` over
      HTTP. `runtime/server.py`, verified live (real server, real HTTP
      call, real 502 mapping when the `hermes` binary isn't present). Not
      yet called *by* `orchestrator/jobs.py` — see below.
- [ ] RTK compression ratio, measured (`flutter test` with/without the
      wrapper). RTK isn't integrated at all yet.
- [ ] `DartAnalyzerIndexer` against a toy Flutter app. Not started.

## Phase 3 — Product workflow (partially done, ahead of schedule)

The plan scoped this as architecture-only for now, but the state machine
work in Phase 1 already covers most of it:

- [x] `discovery` / `planning` / `product-design` / `architecture` states,
      each with its own role, artifact, and approval gate.
- [x] Decision extraction (```decision``` fenced blocks → `decisions/`).
- [x] Approval gates enforcing exact-typed approval.
- [ ] Artifact versioning via git (currently a plain integer
      `artifact_revision` counter, not actual git history/diffing of
      artifact content).

## Phase 4 — Codebase intelligence

- [ ] Not started. `indexing/port.py` (the `CodebaseIndexer` protocol),
      `DartAnalyzerIndexer`, `GraphifyIndexer`, freshness/incremental
      indexing by git commit — none of it exists yet. `builder`/`reviewer`/
      `qa` roles are configured for guided context mode in `agents.yaml`
      but have no index to guide retrieval with.

## Phase 5 — Engineering workflow (partially done, ahead of schedule)

- [x] `builder` / `reviewer` / `qa` roles configured (guided context mode,
      read budgets, denylists) in `config/agents.yaml`.
- [x] Denylist enforcement as a correctness invariant (tested, demoed).
- [x] Retry → `blocked` on exhausted `max_attempts` (autonomous states).
- [x] `implementation → review → qa`, with `on_reject: implementation`
      already in `workflow.yaml`.
- [ ] Diff-aware reviewer context (needs git integration — reading an
      actual diff — which doesn't exist yet).
- [ ] Real git integration generally (checking out branches, reading
      diffs, committing builder output).

## Phase 6 — Flutter toolchain

- [ ] Not started. `POST /exec`, RTK-wrapped `flutter pub get / analyze /
      test / build apk`. The real `docker/agent-runtime/Dockerfile` (with
      Flutter/Android SDK/JDK) doesn't exist yet — `docker/spike/Dockerfile`
      only proves the base Hermes+OpenRouter image works.

## Phase 7 — Optional infra

- [ ] Not started, and explicitly optional per the plan: SQLite event
      index, dashboard, cost analytics. Qdrant only if deterministic
      retrieval demonstrably fails elsewhere — no signal yet that it will.

## Named gaps not yet tied to a specific phase

- [x] `runtime/server.py` — FastAPI HTTP wrapper around `runtime/hermes.py`,
      for the container-topology design where orchestrator and
      agent-runtime are separate containers. Verified live.
- [ ] `orchestrator/jobs.py` still calls `runtime.hermes.run()` in-process,
      not through `runtime/server.py` over HTTP — which is why Phase 1/2
      could be verified live without Docker networking, and why the
      container topology isn't real yet even though the HTTP boundary
      exists. Needs an `httpx`-based client swapped in for the current
      `hermes_run` import, touching every existing monkeypatch test.
- [ ] `runtime/rtk.py` — tool-output compression wrapper + ratio metrics.
- [ ] `compose.yaml` — no container topology defined yet; everything
      verified live so far ran directly on the host or in the standalone
      spike container.
