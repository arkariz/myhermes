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
      HTTP, and `orchestrator/jobs.py` actually calling it via
      `runtime/client.py` when `AGENTIC_RUNTIME_URL` is set. Verified live
      end to end, across real process boundaries — see below.
- [ ] RTK compression ratio, measured (`flutter test` with/without the
      wrapper). RTK isn't integrated at all yet.
- [x] `DartAnalyzerIndexer` against a toy Flutter-shaped app: a widget
      class node exists with correct file/line, and its `imports` edges
      are present. Verified live — see Phase 4 below.

## Phase 3 — Product workflow (done, ahead of schedule)

The plan scoped this as architecture-only for now, but the state machine
work in Phase 1 already covers most of it:

- [x] `discovery` / `planning` / `product-design` / `architecture` states,
      each with its own role, artifact, and approval gate.
- [x] Decision extraction (```decision``` fenced blocks → `decisions/`).
- [x] Approval gates enforcing exact-typed approval.
- [x] Artifact versioning via git — `orchestrator/artifact_versioning.py`.
      `artifacts/` is its own git repo; `TurnRunner` snapshots it after
      every successful turn, committing only if something actually
      changed (no empty commits). Scoped to `artifacts/` alone, not the
      whole agent-state tree, since `turns/`/`conversations/`/
      `events.jsonl` are already append-only. The plain integer
      `artifact_revision` counter stays too — it's what session-boundary
      logic keys on, and this doesn't replace that, it adds real diffable
      history alongside it. Tested against a real `git` subprocess, not
      mocked (same reasoning as `test_store.py`: the property under test
      is git's own behavior).

## Phase 4 — Codebase intelligence (core loop + retrieval done; Dart/AST scope, not full)

- [x] `indexing/port.py` — the `CodebaseIndexer` protocol (`IndexNode`,
      `IndexEdge`, `IndexResult`).
- [x] `DartAnalyzerIndexer` (`indexing/dart_adapter.py`) — shells out to a
      real Dart CLI, `tools/dart_indexer/`, built on `package:analyzer`
      (the same engine the Dart LSP uses). AST-only, not resolved
      analysis: no `flutter pub get` required first, which is what makes
      it fast enough to consider running per turn rather than once at
      setup. Detects files, classes, widgets (by superclass name —
      `StatelessWidget`/`StatefulWidget`/`State`), mixins, enums,
      extensions, top-level functions, and methods nested under their
      class; edges for `imports`, `declares`, `extends`, `implements`,
      `with`. 10 Dart-side tests (`tools/dart_indexer/test/`) + 9
      Python-side tests (`tests/test_dart_adapter.py`, subprocess mocked).
      Verified live twice: the Dart CLI directly against a toy
      Flutter-shaped file (correct widget/enum/mixin/function
      classification, correct line numbers), and the Python adapter
      calling that same CLI as a real subprocess and normalizing its JSON
      into `IndexNode`/`IndexEdge`. Found live: on Windows, `subprocess.run`
      needs `dart.bat`, not the extension-less `dart` shim
      (`DART_EXECUTABLE` env var), documented in `dart_adapter.py`.
- [x] Freshness/incremental indexing by git commit —
      `indexing/freshness.py::ensure_fresh()`. Rebuilds only when the
      project's current `git rev-parse HEAD` differs from the cached
      `metadata.json`'s `source_revision`; a project with no git repo
      (`current_git_revision()` returns `None`) always rebuilds, since
      there's no cheaper freshness signal available. 7 tests
      (`tests/test_freshness.py`) against a real git subprocess, with a
      fake in-memory indexer standing in for `DartAnalyzerIndexer` so no
      Dart SDK is needed to run them.
- [x] Wired into `TurnRunner`/context providers.
      `context/providers.py::IndexProvider` hands the index's file
      inventory to guided-retrieval roles as references (paths, not
      content — the agent reads with its own tools, same as
      `ReferencedFilesProvider`). `TurnRunner._current_index_revision()`
      computes the real value every turn for a guided role with a
      configured `project_source_root` + indexer, persists it to
      `state.yaml`, and feeds it to `SessionManager.decide()` — the
      `index_revision` boundary trigger is tested against a real indexer's
      output for the first time, not just a synthetic string. Wired by
      default in both `orchestrator/cli.py` and
      `telegram_bot/handlers.py` (`project_source_root=entry.host_path`,
      `indexer=DartAnalyzerIndexer()`) — inert for a non-Dart project or a
      host with no Dart SDK, since `supports()`/failures degrade to "no
      index" rather than breaking the turn. **Verified live**: a real
      `TurnRunner`, a real toy Flutter-shaped git project, the real Dart
      CLI — `state.yaml`'s `index_revision` held the project's actual
      commit hash, and the turn's real `manifest.yaml` listed `lib/app.dart`
      with `reason: present in the codebase index`. 6 new tests across
      `test_providers.py`/`test_jobs.py`.
- [ ] Resolved (not just AST) analysis — `calls`/`instantiates` edges,
      which need `flutter pub get` to have run and a real
      `AnalysisContextCollection`. Deliberately deferred: the plan itself
      frames this as slower, with unresolved/AST-only as the fast
      fallback — here it's the primary mode instead, since nothing yet
      needs a resolved call graph.
- [x] Dependency expansion and relevance ranking — the other two things
      named in the same plan sentence as "symbol+file retrieval." Both live
      in `IndexProvider` now, as two more priority tiers above the plain
      inventory (5 explicit reference · **7 one-hop import** ·
      **8 keyword match** · 9 plain inventory):
      - **Dependency expansion** follows only the *referenced* files' own
        `imports` edges, one hop — not a transitive closure over the whole
        graph, which would blur "expanded" into "everything." The Dart
        indexer stores import edges by raw URI (`dst: uri`), not a
        resolved file, so resolution happens here: relative imports
        resolve by path math against the importing file's directory;
        `package:<name>/...` self-imports resolve via the project's own
        `pubspec.yaml` name. Neither needs type resolution — that's still
        the deferred item below.
      - **Relevance ranking** is a plain keyword-in-path match against the
        task text, not embeddings or a second LLM call: cheap,
        deterministic (same task text always ranks the same way — a
        rebuild at the same source revision must still produce the same
        manifest), and enough to break ties in a large inventory.
      4 new tests (`tests/test_providers.py`), plus **verified live**
      against the real Dart CLI: a toy project with a relative import
      (`models/product.dart`) and a self-package import
      (`package:toy_live/checkout.dart`) from a referenced file — both
      resolved to real project files and were promoted to priority 7.
- [ ] Resolved (not just AST) analysis — `calls`/`instantiates` edges,
      which need `flutter pub get` to have run and a real
      `AnalysisContextCollection`. Still deferred, unchanged from before:
      the plan frames this as slower, with unresolved/AST-only as the fast
      fallback — here it's the primary mode instead, since nothing yet
      needs a resolved call graph. (Moved up from its old spot above the
      now-done expansion/ranking item.)
- [ ] `GraphifyIndexer` (non-Dart repos) — not started. Lower priority for
      this project's actual scope (Flutter/Dart) than the items above.

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
      test / build apk`. `docker/agent-runtime/Dockerfile` now exists and
      is verified live (serves `runtime/server.py`, real Hermes CLI inside)
      but has no Flutter/Android SDK/JDK on top of it yet.

## Phase 7 — Optional infra

- [ ] Not started, and explicitly optional per the plan: SQLite event
      index, dashboard, cost analytics. Qdrant only if deterministic
      retrieval demonstrably fails elsewhere — no signal yet that it will.

## Named gaps not yet tied to a specific phase

- [x] `runtime/server.py` — FastAPI HTTP wrapper around `runtime/hermes.py`,
      for the container-topology design where orchestrator and
      agent-runtime are separate containers. Verified live.
- [x] `runtime/client.py` + `orchestrator/jobs.py`'s `runtime_url` — routes
      `TurnRunner`'s Hermes calls through the HTTP server instead of the
      in-process function call, when `AGENTIC_RUNTIME_URL` is set (both
      `cli.py` and `telegram_bot/handlers.py` read it). Unset by default,
      so every prior live run stays reproducible with zero config change.
      Verified live across real process boundaries (separate CLI and
      server processes, real `httpx` call, real 502 propagated back as a
      `RuntimeError`).
- [x] `compose.yaml` + `docker/agent-runtime/Dockerfile` +
      `docker/orchestrator/Dockerfile` — the real container split. Smaller
      than the plan's original diagram on purpose: no shared Hermes-profile
      volume (would reintroduce the cross-project memory leak the spike
      fixed) and no gateway service (never implemented). Verified live:
      real `docker compose build`/`up`, `agent-runtime`'s `/health` and
      `hermes --version` confirmed from inside its own container, and a
      full `orchestrator` → HTTP → `agent-runtime` → real `hermes`
      subprocess round trip (failed on "No LLM provider configured" since
      no API key was set on purpose, not on a missing binary).
- [x] `runtime/rtk.py` — tool-output compression + ratio metrics. Real RTK
      is an external Rust binary this project doesn't vendor; this is a
      Python implementation of the same job: strip ANSI codes, collapse
      runs of ≥3 consecutive identical lines to one line plus a repeat
      count, and truncate an unbroken block over 60 lines to its first/last
      20. `run()` wraps a real subprocess (merged stdout+stderr, since a
      build tool interleaves them meaningfully) and reports a *measured*
      `CompressionResult.ratio`, never an assumed one — the plan is
      explicit RTK should be dropped if it doesn't pay off, which only a
      real number can decide. 8 tests, including one against a real
      subprocess producing genuinely repetitive output (>90% reduction).
      **Not wired into anything yet** — there is no real tool-output-
      producing toolchain in the system until Phase 6's `POST /exec`
      exists (`flutter test`/`analyze`/`build`), so there's nothing to
      wrap end-to-end until then. Ships now so Phase 6 only has to call it.
- [ ] No project-source volume in `compose.yaml` yet — nothing built so far
      reads or writes a project's actual code. Needed once Phase 4/5/6's
      guided-retrieval toolsets exist.
