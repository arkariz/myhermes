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
- [x] RTK compression ratio, measured — `runtime/rtk.py`'s own tests
      measure it against real repetitive subprocess output (>90%,
      99.66% via a real `POST /exec` call), and against real `flutter
      pub get` output through the real endpoint (see Phase 6). Not yet
      measured specifically on `flutter test` with/without the wrapper as
      an A/B comparison on the same real command — the ratio is real, but
      that specific side-by-side isn't done.
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
      default in both `orchestrator/cli.py` and `telegram_bot/handlers.py`
      (`project_source_root=entry.host_path`, `indexer=DartAnalyzerIndexer()`
      for host/CLI mode, or `RemoteIndexer(base_url=...)` over the same
      `AGENTIC_RUNTIME_URL` when set — see the container-topology gap
      below) — inert for a non-Dart project or a host with no Dart SDK,
      since `supports()`/failures degrade to "no index" rather than
      breaking the turn. **Verified live**: a real
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

## Phase 5 — Engineering workflow (core loop + diff-aware context done)

- [x] `builder` / `reviewer` / `qa` roles configured (guided context mode,
      read budgets, denylists) in `config/agents.yaml`.
- [x] Denylist enforcement as a correctness invariant (tested, demoed).
- [x] Retry → `blocked` on exhausted `max_attempts` (autonomous states).
- [x] `implementation → review → qa`, with `on_reject: implementation`
      already in `workflow.yaml`.
- [x] `config/souls/{builder,reviewer,qa}.md` — found missing while
      finishing this phase: three of `workflow.yaml`'s seven agent-running
      roles had been configured in `agents.yaml` with no persona file to
      match, which `RoleSoulProvider` degrades from silently (an empty
      contribution, not an error), so the gap had gone unnoticed. A new
      regression test, `tests/test_souls.py`, asserts every
      agent-running workflow role has a soul file so this can't recur
      silently again.
- [x] Real git integration against the **project's own source tree** —
      `orchestrator/project_git.py` (`is_git_repo`, `current_revision`,
      `commit_all`, `diff`). Deliberately never `git init`s a project —
      unlike `artifacts/` (this system's own bookkeeping tree,
      `artifact_versioning.ensure_repo()` may freely create), the project
      source is the user's; a non-git project source degrades to "no
      commit, no diff" rather than initializing one uninvited, the same
      convention `indexing/freshness.py` already established for
      `current_git_revision()`. 10 tests against real `git` subprocesses.
- [x] Diff-aware reviewer/QA context — `context/providers.py::DiffProvider`.
      `TurnRunner` now captures `state.yaml`'s `implementation_base_revision`
      once, the first time a project enters `implementation` (stable across
      a QA-reject retry loop back through it, so the diff stays cumulative
      from the true start rather than resetting per attempt), commits the
      builder's actual working-tree changes to the project's own git
      history after every successful `implementation` turn
      (`SOURCE_COMMITTED` event, no empty commits), and hands `reviewer`/
      `qa` a real embedded diff (key `git-diff`, matching qa's existing
      allowlist entry) instead of nothing. 9 new tests across
      `test_providers.py`/`test_jobs.py`. **Verified live**: a real toy
      Flutter-shaped git project, the real Dart indexer, a real
      builder → reviewer turn sequence (only `hermes_run` mocked) — the
      `SOURCE_COMMITTED` event fired, `implementation_base_revision` held
      the project's real pre-change commit, and the reviewer's actual
      rendered prompt embedded the real diff text (`+class Feature`).
- [ ] Checking out branches — not built; nothing in this system's workflow
      yet operates on more than the project's current branch/working tree
      (no multi-branch or PR-based flow exists to need it).

## Phase 6 — Flutter toolchain (endpoint + Flutter SDK done; Android/JDK not started)

- [x] `POST /exec` (`runtime/server.py`) — runs a literal `argv` (no shell,
      so there's no string for a shell to reinterpret) in a given `cwd`
      and returns its `runtime/rtk.py`-compressed output plus the measured
      ratio. This is the endpoint the brief names for RTK-wrapped
      `flutter pub get / analyze / test / build apk`, `git`, `rg`, etc. --
      generic by design, since the endpoint doesn't need to know which
      specific tool it's running, only how to run and compress *a*
      command. 5 new tests in `tests/test_server.py`. **Verified live**
      twice: FastAPI's `TestClient` (in-process) and a real `uvicorn`
      subprocess reached over a real HTTP call from a separate process --
      500 repeated lines came back compressed to one, 99.66% measured
      reduction, matching `runtime/rtk.py`'s own live-verified ratio.
- [x] `docker/agent-runtime/Dockerfile` — added a real Flutter SDK: a
      shallow clone of the `stable` channel to `/opt/flutter`, then
      `flutter precache --no-android --no-ios` (host-platform engine
      artifacts only, skipping the Android/iOS toolchains — see below).
      Also fixed a real bug found while doing this: the image's `COPY`
      never included `runtime/rtk.py`, so `runtime/server.py`'s new
      `/exec` route (added in the same pass as the endpoint itself) would
      have failed to import the moment this image was actually built —
      caught here rather than at first real use. **Verified live, twice**:
      first `docker run` a shell directly against the built image
      (`flutter create` + `flutter pub get` + `flutter analyze` +
      `flutter test` against a scratch project, all real — 0 analyzer
      issues, the counter smoke test passed); then the full intended
      path — a real HTTP `POST /exec` against a running container,
      running real `flutter create` and `flutter pub get` over the wire,
      RTK-compressed output round-tripping correctly.
      **Found live**: the first build attempt failed outright --
      `flutter precache` needs `unzip` to extract the Dart SDK, which
      wasn't in the base image's package list; added it.
- [ ] Android SDK + JDK -- not installed. Enough is in the image now for
      `flutter pub get`/`analyze`/`test` against a project's own source
      (verified above), but not `flutter build apk`, which needs both. A
      genuinely large, separate download (multiple gigabytes) left for
      its own pass rather than folded into this one.
- [ ] Not called by anything yet from a real turn. No role's toolset
      currently routes a terminal command through `/exec` instead of
      running inside Hermes's own CLI sandbox -- that wiring, plus
      whatever `builder`'s `toolsets: [terminal]` actually resolves to
      today, is unexplored.

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
- [x] Project-source volume in `compose.yaml` — both `orchestrator` and
      `agent-runtime` now mount `${PROJECTS_DIR:-./data/projects}` at
      `/workspace/projects` (rw on both sides: the builder role's own
      tools need to write it, not just read it). Closing this surfaced
      three real container-topology bugs, none visible from host/CLI-mode
      testing alone, all found and fixed by actually running the compose
      stack rather than trusting that "it works on the host" implied "it
      works in the container":
      1. `orchestrator/cli.py` imports `indexing.dart_adapter` at module
         level, but `docker/orchestrator/Dockerfile` never copied
         `indexing/` — the orchestrator container couldn't even start
         `project new`. Fixed by adding the `COPY`.
      2. Even fixed, `DartAnalyzerIndexer` would have shelled out to a
         local Dart CLI that doesn't exist in the orchestrator image (no
         toolchain there, by design) and isn't copied there either. Fixed
         properly rather than papered over: built the `POST /index`
         endpoint the plan's original container diagram names
         (`runtime/server.py`), backed by `indexing/remote.py::RemoteIndexer`
         -- same `CodebaseIndexer` shape as `DartAnalyzerIndexer`, so
         `TurnRunner`/`IndexProvider` don't know or care which one they
         hold. `orchestrator/cli.py`/`telegram_bot/handlers.py` now pick
         `RemoteIndexer` over `DartAnalyzerIndexer` based on whether
         `AGENTIC_RUNTIME_URL` is set, exactly how they already pick
         between `runtime.hermes.run()` and `runtime.client.run()`.
      3. `docker/orchestrator/Dockerfile`'s base image (`python:3.12-slim`)
         has no `git` at all -- meaning `artifact_versioning.py` (Phase 3),
         `project_git.py` (Phase 5), and `indexing/freshness.py`'s own
         `current_git_revision()` (Phase 4) would all have failed the
         moment any of them ran inside that container. Added `git` to the
         image.
      4. `orchestrator/summarizer.py` always called Hermes in-process, with
         no `runtime_url` support at all -- since `config/models.yaml`
         configures a `summarizer` route by default, every successful turn
         in the orchestrator container (which has no Hermes CLI, on
         purpose) would have crashed there. Added the same
         `runtime_url` → `runtime/client.py` routing `jobs.py`'s own
         `_invoke_hermes()` already has.
      12 new tests (`tests/test_remote_indexer.py`,
      `tests/test_server.py`'s `/index` tests, `tests/test_summarizer.py`'s
      and `tests/test_jobs.py`'s `runtime_url` tests). **Verified live, in
      full**: real `docker compose build` of both images; a real toy
      Flutter-shaped git project written directly into the shared volume;
      `RemoteIndexer` on the orchestrator side calling agent-runtime's real
      `/index` endpoint, which ran the real Dart CLI and returned real
      nodes; a real `TurnRunner.run_turn()` inside the orchestrator
      container against that project (`hermes_run` mocked for the main
      call only) with the builder's file write, `SOURCE_COMMITTED`, and
      `git-diff` manifest entry all working exactly as in host-mode
      testing; and finally, with nothing mocked at all, `python -m
      orchestrator.cli turn toy "..."` inside the real container producing
      a clean `"No LLM provider configured"` failure via the real HTTP
      round trip to agent-runtime's real `hermes` subprocess -- proof the
      whole chain is wired correctly, with only a real API key missing.

## Layered-package refactor (Phases 0-8) — complete

Everything above this line describes the codebase as it was built,
under the flat `orchestrator/`/`runtime/`/`indexing/`/`telegram_bot/`
layout -- left as-is, since it's a record of what actually happened at
the time, not a description of the current tree. What's true now:

- [x] **Structural move.** All application code now lives under one
      installable package, `src/agentic_dev/`, split into five ranked
      layers (`domain → ports → adapters → app → entrypoints`). Every
      module named in the phase entries above by its old flat path
      (`orchestrator/jobs.py`, `runtime/hermes.py`, `telegram_bot/topics.py`,
      `indexing/dart_adapter.py`, ...) moved to the equivalent layered
      location -- see `README.md`'s "What's implemented" table for the
      current path of each, and `docs/architecture.md` for the layering
      contract itself.
- [x] **The one real import cycle is gone.** `orchestrator/cli.py`
      importing `telegram_bot.topics` doesn't exist anymore -- the
      Telegram topic adapter moved to `adapters/telegram/topics.py`, a
      same-or-lower-rank import from `entrypoints/cli.py`.
- [x] **Guard test.** `tests/test_architecture.py` -- an AST walk, no
      third-party dependency -- enforces downward-only imports, a pure
      `domain/`, and that only `settings.py` ever evaluates `__file__`.
      Runs as part of the normal suite.
- [x] **Workspace extracted to a separate sibling repo,** `../agentic-workspace`.
      `config/` is tracked there; `agent-state/` and `projects/` are
      gitignored. `agentic init-workspace <path>` bootstraps a fresh one
      from a template shipped inside the package. This retires the
      tracked-and-mutated `config/projects.yaml` problem called out in
      Phase 0 above -- the file that gets mutated at runtime now lives in
      a repo whose entire purpose is to hold the user's own local state.
- [x] **Secrets down to one file.** `secrets/` and `docker/spike/`
      (including `docker/spike/secrets/`) are deleted outright; the only
      remaining secrets file is this repo's own gitignored `.env`. Kept
      out of the workspace repo on purpose, since that repo is meant to be
      pushed -- see `docs/adr/0001-layered-package.md`.
- [x] **Docker images rebuilt from `pyproject.toml` extras.** Dockerfiles
      moved to `deploy/{orchestrator,agent-runtime}/Dockerfile`, each now
      does `pip install ".[orchestrator]"` / `".[runtime]"` instead of a
      hand-duplicated dependency list, plus an import-smoke `RUN` line
      that turns a selective-COPY-style bug (the class that once shipped
      `runtime/rtk.py` missing from an image) into a build failure instead
      of a first-request failure. `compose.yaml` mounts one workspace
      volume (`${AGENTIC_WORKSPACE:-../agentic-workspace}:/workspace`) on
      both services instead of three separate bind mounts. Rebuilt and
      live-smoke-tested against the real containers.
- [x] **Docs brought in line with the above** (this refactor's own Phase
      7): `docs/architecture.md` and `docs/adr/0001-layered-package.md`
      added; `README.md`'s runnable commands, module-path references, and
      `.env.example` description updated to match; a "where does what
      live" table added covering the `.env` (code repo, secrets) vs.
      workspace `config/` (tracked, non-secret) vs. workspace
      `agent-state/`/`projects/` (gitignored, generated/user state) split.
- [x] **349/349 Python tests pass** (up from the flat-layout baseline
      recorded above), plus the 10 Dart tests in `tools/dart_indexer/`.
      Verified against the real Docker containers, not just the suite.

Full rationale and the options considered: `docs/adr/0001-layered-package.md`.
The plan this refactor followed: the planning-session output that produced
it (not tracked in this repo — see the refactor's own commit history on
`refactor/layered-layout`).
