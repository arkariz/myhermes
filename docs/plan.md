# Agentic Dev — Pang Redesign: Architecture + Phase 1–2 Implementation Plan

## Context

`C:\Work Stuff Personal\Project\hermes` is empty. This is a greenfield build of **Agentic Dev**, a local-first, Dockerized agentic engineering workspace that preserves the strongest ideas from [`isfaaghyth/pang`](https://github.com/isfaaghyth/pang) — deterministic lifecycle orchestration around narrowly-scoped agents, Markdown-as-project-memory, human approval gates, configurable model routing — while removing what's coupled to the author's server (`~/ippang` paths, Caddy/Cloudflare, VPS-first deploy, mandatory CouchDB/LiveSync, host-installed runtimes, Android/KMP assumptions).

---

## What Pang actually does (read from source, not summaries)

This matters because it changed three decisions below.

**Context — Pang sends file *paths*, never file *contents*.** Every prompt is 5–10 lines. From `project-tick.py`:

```python
f"Project '{slug}' is in PLANNING state.\n"
f"Note: {note}\n\n"
"Read the concept and constraints from 00-context.md.\n"
"Generate: PRD, technical plan, and acceptance criteria.\n"
"Write them to the vault project subtree.\n"
"Then set status to awaiting-approval.\n"
```

There is no context builder, no manifest, no budget, no token accounting. The agent is told *where to look* and reads selectively with its own tools.

**Memory** — Obsidian vault Markdown per project (`00-context.md`, `10-architecture.md`, `20-task-log.md`, `30-api.md`, `40-reports.md`, `decisions.md`), plus a Hermes profile per project (`eng-<slug>`) holding conversational history, plus one long-lived `engineer` orchestrator profile. Hard rule from `obsidian-vault/SKILL.md`: secrets and verbose build logs stay on disk; only summaries enter the vault.

**Invocation** — `hermes -p <profile> -z <prompt>`, detached via `start_new_session=True` so a ten-minute run doesn't block the next cron tick. Retries live in frontmatter (`attempts` / `max_attempts` → `blocked`). `awaiting-approval` and `awaiting-merge` advance only by manual note editing.

**Cache** — effectively none on the input side: `npx graphify . --output graph.json`, no staleness check, no revision tracking. The real token optimization is elsewhere — **RTK (Rust Token Killer)**, a CLI proxy that compresses *tool output* (git, test runners, build tools, grep) by 50–90% before it re-enters context.

**The single best line in the repo**, from `handle_qa`:

```
Read ONLY: {note} (concept + acceptance criteria) and the git diff.
Do NOT read tech-plan.md, task-log.md, or builder reasoning.
```

This is context restriction as a **correctness** guarantee, not a cost measure: QA is deliberately blinded to the builder's reasoning so it verifies against acceptance criteria instead of being anchored by how the builder thought. It becomes a first-class config field below.

### Three findings from the wider research

1. **Graphify cannot parse Dart.** 37 tree-sitter grammars (`.py .ts .js .go .rs .java .c .cpp .rb .cs .kt .scala .php`); Dart is absent, and adding a language means forking (`extract.py`, `detect.py`, `watch.py`, `pyproject.toml`). A Flutter-first system cannot rely on it for its primary language. Its schema normalizes cleanly though: `{nodes:[{id,label,source_file,source_location}], edges:[{source,target,relation,confidence}]}`.
2. **Hermes exposes the controls needed for a hybrid memory model:** `-z` one-shot, `-r <session-id>` resume, `--usage-file` (`input_tokens`, `output_tokens`, **`cache_read_tokens`**, `estimated_cost_usd`, `model`, `provider`, `session_id`), `--toolsets`, `--ignore-rules`, `-p <bot>` profiles under `~/.hermes/profiles/<name>/`.
3. **KeiRouter is not a public project**, and Docker is not installed on this host (`docker` MISSING; `python` is the WindowsApps stub). Both become explicit prerequisites/ports.

### Decisions taken (confirmed with user)

| Decision | Choice |
|---|---|
| Dart indexing | Native Dart indexer using `package:analyzer` behind an **Indexer Port**; Graphify as a second adapter for non-Dart repos |
| Agent runtime | **Hermes Agent in Docker** |
| Memory model | **Hybrid** — resume the Hermes session within a workflow state, full rebuild at boundaries |
| Context strategy | **Split by role** — pre-assembled for document roles, guided retrieval for code roles |
| Reproducibility | **Mandatory at gates and autonomous turns**; continuation turns record a delta manifest |
| Environment | Docker-first; Docker Desktop + WSL2 backend is a Step 0 prerequisite |
| Planning scope | Architecture for all phases; executable detail for Phase 1–2 |

---

## Architecture

### Context strategy — two modes, chosen per role

An earlier draft pre-assembled context for every role. Reading Pang showed why that's wrong for code work: pre-assembly is a *prediction*, and predictions about which source files a builder will need are frequently wrong in both directions — wasted tokens on files it ignored, a stalled turn on the file we didn't include.

| | **Pre-assembled** | **Guided retrieval** |
|---|---|---|
| Roles | planner, product-designer, architect | builder, reviewer, qa |
| Input | full document content, budgeted | paths + index + tight instruction |
| Reading | done by the builder, ahead of time | done by the agent, with its own tools |
| Auditability | manifest of what we *predicted* | manifest of what it *actually read* |
| Bounded by | token budget | **read budget** (max files, max bytes, max tool calls) |

Document roles work over a small, predictable set — PRD, decisions, design artifact — where pre-assembly is accurate and reproducibility matters because these turns produce approvals. Code roles need to explore; give them the index, the paths, and a hard read budget, then record what they actually touched. That manifest is *observed rather than assumed*, which is the more honest artifact.

Both modes emit a `manifest.yaml`. Guided-retrieval manifests are reconstructed from the tool-call log (`file_read`, `rg`, `git diff` invocations) after the turn completes.

### Output-side compression — the gap the input-side design missed

For roles with `terminal` in their toolset, accumulated **tool output** often outweighs the initial prompt: `flutter test`, `flutter analyze`, `gradle build`, and `rg` dumps all flow back into context. RTK reports a 30-minute agent session burning ~118k tokens on shell output alone.

**RTK ships in the agent-runtime image and wraps the builder/QA toolchain.** For those roles this is plausibly a larger win than the entire input-side apparatus, and it costs one binary plus a wrapper. Compression ratios are recorded per turn alongside token usage so the claim stays measurable rather than assumed.

### Context denylists — restriction as correctness

Preserved directly from Pang, promoted to config in `config/agents.yaml`:

```yaml
roles:
  qa:
    context_allowlist: [artifacts/prd.md#acceptance-criteria, git-diff, test-output]
    context_denylist:  [artifacts/tech-plan.md, task-log.md, conversations/implementation/]
    reason: "QA must verify against acceptance criteria, not be anchored by builder reasoning"
  reviewer:
    context_denylist:  [conversations/implementation/]
```

Enforced by the context builder for pre-assembled roles, and by toolset/path restrictions for guided-retrieval roles. A denylist violation fails the turn — it's a correctness invariant, not a preference.

### Memory model — two systems, different jobs

An earlier draft made Hermes fully stateless. That was wrong on cost: rebuilding the prompt every turn busts the provider's prefix cache, so it sends *fewer* tokens but pays **full price** for all of them, while a resumed session sends more tokens at ~cached rates.

| | Hermes session memory | Agent-state (ours) |
|---|---|---|
| Answers | "what did we just say" | "what does the project currently believe" |
| Lifetime | one workflow state | permanent |
| Form | opaque transcript | Markdown: artifacts, decisions, summaries |
| Auditable / reproducible | no | yes |

Not competitors: Hermes has no durable project-belief layer; we have no cheap conversational continuity. The brief names the distinction (§16: *"Conversations explain how a decision was reached. Decisions represent what the project currently believes."*).

**Two kinds of turn:**

- **Continuation turn** — same project, state, role; live session; nothing invalidating since. `hermes -r <session_id>` with a **delta package**: the new human message plus whatever changed.
- **Boundary turn** — everything else. Fresh session, full rebuild, complete manifest.

**Boundary triggers** (any one invalidates `hermes_session_id`): state changed · role changed · approval granted or artifact revision bumped · decision recorded · index revision changed · `max_session_turns`/`max_session_age` exceeded · previous turn failed · human sent `/refresh`.

```yaml
session:
  hermes_session_id: abc123
  role: planner
  state: planning
  opened_at_turn: planning-08
  turns: 3
  artifact_revision: 7
  index_revision: a1b2c3d
```

**Reproducibility policy.** Full rebuild with a complete manifest is mandatory at every approval gate and every autonomous turn. Continuation turns record `resumed_from` plus a delta manifest and are explicit that they do not describe the full model input. Every *decision point* therefore has a complete, reproducible manifest.

Every turn persists to `agent-state/` regardless of kind, so recovery never depends on Hermes.

**Telegram is ours, not Hermes's.** Hermes ships a Telegram gateway, but using it would put an LLM in charge of state transitions, violating "deterministic workflow, probabilistic agents". We run our own `python-telegram-bot`; Hermes never sees Telegram.

> **Phase 1 spike — RUN, against real `hermes` v0.20.5 + OpenRouter (`openai/gpt-4o-mini`), not simulated.**
>
> | # | Measurement | Result |
> |---|---|---|
> | 1 | Fresh `-z`/`chat -q --safe-mode`, no resume: turn A plants a distinctive secret, turn B asks about it | **LEAKED.** A background "self-improvement" writer autonomously saves user statements to `~/.hermes/memories/MEMORY.md` and re-injects it into every later turn. Neither `--ignore-rules`, `--safe-mode`, nor `memory.memory_enabled: false` stops it — none of these flags are even wired into the `-z` code path (`hermes_cli/oneshot.py::run_oneshot` only accepts `model/provider/toolsets/skills/usage_file`), and `--safe-mode` on `chat -q` didn't stop it either. |
> | 2 | Same, but turn B uses `--resume <session_id>` | **CONFIRMED.** Genuinely recalls prior turns ("↻ Resumed session ... 4 total messages"), not just a summary. |
> | 3 | Two different `HERMES_PROFILE` values under one `HERMES_HOME` | **LEAKED.** `MEMORY.md` lives at `~/.hermes/memories/`, scoped to `HERMES_HOME`, not to the active profile. `proj-beta` correctly recalled a secret only ever told to `proj-alpha`. |
> | 4 | Repeat an identical prompt, read `cache_read_tokens` | **CONFIRMED.** Non-zero on both calls (6016, then 18048) — the stable-prefix layout below earns real cache hits. |
>
> **Load-bearing conclusion:** `HERMES_PROFILE` is not an isolation boundary; **`HERMES_HOME` is**. The original design (`profile_scope: role_project`, one shared `hermes-profiles` volume) is wrong and has been corrected in `config/agents.yaml` / `orchestrator/sessions.py`: every project now gets its **own `HERMES_HOME` directory** (`SessionPolicy.home_dir`), mandatory for correctness, not an optimization — omitting it lets one project's content leak into another's. `HERMES_PROFILE` is retained only for role-scoped model/skills/toolset config *within* one project's already-isolated home, and now defaults to `role` scope since sharing a profile name across projects costs nothing once `home_dir` does the isolating.
>
> This also means "boundary turn = fresh session" was an incomplete definition: a fresh session with no `--resume` is **not** sufficient for isolation on its own. Isolation requires a fresh, project-scoped `HERMES_HOME`. This is now reflected in the runtime invocation contract: every call sets `HERMES_HOME=<home_dir(project_id)>`, not just a profile flag.

### Cache-aware prompt layout

Two independent axes, previously conflated:

- **Selection** — *what gets in*: §17.2 priority under the role's budget.
- **Ordering** — *where it sits*: volatility, most stable first, so the prefix cache can hit.

```
[STABLE PREFIX — byte-identical across turns within a state]   ← cacheable
  role soul
  project identity, vision, target platform, conventions       (Layer 1)
[VOLATILE SUFFIX]
  workflow state and objective                                 (Layer 2)
  relevant decisions, artifact sections                        (Layer 2–3)
  codebase map, task paths and symbols                         (Layer 3)
  recent summary, recent turns, current human request          (Layer 4)
```

Layer 1 renders from a revision-stamped template, changing only on an explicit revision bump. Cache effectiveness is measured via `cache_read_tokens`, not assumed.

### Container topology (no Docker socket, least privilege)

The orchestrator cannot `docker exec` into the runtime — that needs the Docker socket, forbidden by §6. **agent-runtime exposes a small internal HTTP API** and is the only container with the heavy toolchain.

```
compose network (internal)

orchestrator                      agent-runtime
├─ state machine                  ├─ POST /run    → rtk-wrapped hermes -z | chat --resume
├─ context builder                ├─ POST /index  → dart_indexer | graphify
├─ session manager                ├─ POST /exec   → rtk flutter analyze/test/build
├─ telegram bot (MVP: same proc)  ├─ Flutter+Dart+Android SDK+JDK, Hermes, RTK, rg, jq
├─ event log, summarizer          │
├─ /workspace/project        (ro) ├─ /workspace/project        (rw)
└─ /workspace/agent-state    (rw) └─ /workspace/agent-state    (rw)
                                  └─ /workspace/agent-state/<project_id>/.hermes-home
                                       mounted as HERMES_HOME for every call
                                       touching that project (§ spike, verified)

  gateway (LiteLLM or KeiRouter)  ── OpenAI-compatible base URL
```

The read-only project mount for the orchestrator is deliberate: the context builder must *read* source, but only the builder role may *write*. **`HERMES_HOME` is per-project, not a shared volume** — this is the verified isolation boundary (spike measurement 3), not `HERMES_PROFILE`. Each project's `.hermes-home` subdirectory lives inside its own `agent-state` tree, so it's backed up, inspected, and recovered exactly like the rest of that project's state, and two projects can never share one `MEMORY.md`.

**Host layout** — agent state is a sibling of the project, never inside it:

```
D:\Projects\bonked              → /workspace/project
D:\Projects\.agentic-dev\bonked → /workspace/agent-state
```

> **Performance callout:** bind-mounting a Windows drive into a Linux container goes through 9p/virtiofs. Gradle and `flutter build` are slow on it and inotify watchers don't fire reliably. If build times hurt, relocate the workspace into the WSL2 filesystem. Not required to get the loop working.

### Workflow state machine — data, not code

States and transitions live in `config/workflow.yaml`, validated at load. An LLM can never choose the next state.

```yaml
states:
  planning:
    kind: collaborative        # autonomous | collaborative | gate
    role: planner
    artifact: prd.md
    completion: approval       # APPROVE_PRD
    next: product-design
  implementation:
    kind: autonomous
    role: builder
    max_attempts: 2
    on_failure: blocked
    next: review
  awaiting-approval:
    kind: gate
    approval_type: APPROVE_ARCHITECTURE
    next: implementation
```

- **autonomous** — dispatch, run to completion, advance. Always a boundary turn.
- **collaborative** — agent ↔ human, indefinitely, until an explicit approval. An invocation ending is *not* completion. Continuation turns allowed.
- **gate** — no agent runs; only a typed approval advances.

Plus `blocked`, `cancelled`, `failed`.

### Context builder

```python
@dataclass
class ContextItem:
    key: str        # "artifacts/prd.md#acceptance-criteria"
    layer: int      # 1 stable | 2 workflow | 3 task | 4 recent
    priority: int   # §17.2 — governs selection
    volatility: int # governs placement, stable first
    reason: str     # "defines target screen"
    tokens: int
    content: str | None   # None for guided-retrieval (path handed over instead)

@dataclass
class ContextPackage:
    prompt: str
    mode: str                        # "assembled" | "guided"
    kind: str                        # "full" | "delta"
    resumed_from: str | None
    manifest: list[ContextItem]
    read_budget: ReadBudget | None   # guided mode: max files/bytes/tool-calls
    estimated_tokens: int
    omitted: list[tuple[str, str]]   # (key, why)
    source_revision: str
```

Pipeline: **providers collect candidates → dedupe by content hash → enforce denylist → select by priority within budget → order by volatility → record omissions with reasons → render → persist**. Providers are small independent classes (`RoleSoulProvider`, `ProjectIdentityProvider`, `DecisionsProvider`, `ArtifactSectionProvider`, `RecentTurnsProvider`, `CodebaseProvider`, `DiffProvider`), so Phase 4 adds retrieval by adding a provider.

Delta packages run providers in `changed_since(session.opened_at_turn)` mode.

Token counting uses `tiktoken` `o200k_base` with a configurable `chars_per_token` fallback. Every turn records **both** our `estimated_tokens` and Hermes's reported `input_tokens`, logging the drift — the estimate is honest about being an estimate.

### Indexer port (resolves the Graphify/Dart conflict)

```python
class CodebaseIndexer(Protocol):
    def supports(self, project: Project) -> bool: ...
    def build(self, project: Project, since_revision: str | None) -> IndexResult: ...
```

Normalized schema, a superset of Graphify's so normalization is lossless-in:

```
nodes: {id, kind, name, file, line, module, lang}
edges: {src, dst, relation, confidence}
```

- **`DartAnalyzerIndexer` (primary).** A Dart CLI at `tools/dart_indexer/` using `package:analyzer` — the same engine as the Dart LSP, so imports, call targets and class hierarchies are *resolved*, not guessed. Needs `flutter pub get` to have produced `.dart_tool/package_config.json`. Walks `lib/` and `test/`; nodes for files, classes, mixins, enums, extensions, top-level functions, methods, widgets (classes extending `StatelessWidget`/`StatefulWidget`); edges for `imports`, `declares`, `extends`, `implements`, `with`, `calls`, `instantiates`, `tests`. Resolved analysis of a large app takes tens of seconds, so a `--unresolved` parse-only fast mode is the fallback.
- **`GraphifyIndexer` (secondary).** `graphify extract --code-only --update`, then normalize `graphify-out/graph.json`.

Freshness by git commit (`metadata.yaml: {tool, version, source_revision, generated_at, status}`); incremental via `git diff --name-only`. Never rebuilt per turn — the gap Pang left open. An index revision change is a session boundary.

### Model routing

`config/models.yaml` maps role → `provider/model` + context-window preferences. The orchestrator passes `--provider/--model` per invocation; workflow code names a *role*, never a provider. Gateway is a port: **LiteLLM self-hosted by default** (OpenAI-compatible), KeiRouter droppable in via base-URL config. If context exceeds the model's window, the builder reduces before invoking.

### Persistence — filesystem-first

```
/workspace/agent-state/
├── state.yaml              # state, session, pending_action, attempts, artifact revisions
├── events.jsonl            # append-only event log (§27)
├── conversations/<state>/  # NNN-agent.md, NNN-human.md, summary.md
├── decisions/              # NNN-slug.md (frontmatter + rationale)
├── artifacts/              # prd.md, product-design.md, architecture.md, tech-plan.md
├── turns/<turn_id>/        # prompt.md, manifest.yaml, response.md, usage.json, tool-log.jsonl
├── indexes/                # nodes.json, edges.json, metadata.yaml
├── summaries/
└── task-log.md
```

Human-inspectable throughout. Pang's rule is kept: **secrets and verbose build logs never enter agent-state — only summaries.** SQLite deferred to Phase 7 for dashboard queries only, never authoritative.

### Human input semantics

`state.yaml` carries a `pending_action`. Free text is **always** feedback. An approval event is produced *only* by an inline-button callback or an exact `/approve` command **while** `pending_action.type == "approval"`. "looks good" never advances a gate. Approvals record `{artifact, artifact_revision, source_turn, approver, timestamp}`, write a decision record, and close the Hermes session.

Decisions are captured deterministically: agents emit a fenced ` ```decision ` YAML block, parsed by the orchestrator. No second LLM call.

---

## Phase 1 — Core engine (executable detail)

**Goal:** one project, driven from a CLI, completes a planning turn *and* a continuation turn, with budgeted context, manifests, real token accounting, and measured cache hits. No Telegram.

**Step 0 — prerequisites.** Docker Desktop with the WSL2 backend; a real Python 3.12 (current `python` is the Store stub); `uv`.

**Step 1 — repo skeleton.** Brief §34 layout, adapted:

```
agentic-dev/
├── compose.yaml
├── .env.example
├── docker/{orchestrator,agent-runtime}/Dockerfile
├── config/{agents,models,workflow,context,projects}.yaml
├── orchestrator/
│   ├── main.py  cli.py  state_machine.py  events.py
│   ├── jobs.py  sessions.py  store.py  summarizer.py
│   └── context/{builder.py,providers.py,budget.py,tokens.py,manifest.py,denylist.py}
├── runtime/            # FastAPI service inside agent-runtime
│   ├── server.py       # POST /run, /index, /exec
│   ├── hermes.py       # invocation + usage parsing + session id capture
│   └── rtk.py          # tool-output compression wrapper + ratio metrics
├── indexing/{port.py,dart_adapter.py,graphify_adapter.py,freshness.py}
├── tools/dart_indexer/ # Dart CLI, package:analyzer
├── souls/              # orchestrator, planner, product-designer, architect,
│                       # builder, reviewer, qa   (rendered INTO prompts)
├── skills/{flutter,testing,git,indexing,lifecycle}/
├── scripts/{init.sh,start.sh,new-project.sh,index-project.sh,healthcheck.sh}
└── tests/
```

**Step 2 — the Hermes spike.** Build `docker/agent-runtime/Dockerfile` (Flutter SDK, Dart, Android SDK, JDK, Git, ripgrep, jq, RTK, Hermes, Python). Run the four measurements. **Gate: the profile naming scheme and the continuation-turn design both depend on the answers.**

**Step 3 — state, sessions, events.** `store.py` (atomic YAML/JSONL writes via tempfile + `os.replace` — Pang's LiveSync-safe pattern), `events.py` (§27 events plus `SESSION_OPENED`/`SESSION_INVALIDATED`), `state_machine.py`, `sessions.py` (owns `hermes_session_id` and the eight boundary triggers). Pure, unit-testable, zero LLM calls.

**Step 4 — context builder.** `tokens.py`, `budget.py`, `providers.py`, `denylist.py`, `builder.py`, `manifest.py`; assembled and guided modes, full and delta kinds, volatility-ordered rendering. Phase 1 providers stay simple per §35: role soul, project identity, current artifact, relevant decisions, last three turns, explicitly referenced paths.

**Step 5 — runtime service + job execution.** `runtime/server.py` exposes `POST /run`; `hermes.py` builds argv (`-z` or `-r <session>`, `--ignore-rules`, `-p <profile>`, `--provider/--model`, `--toolsets`, `--usage-file`), streams the prompt on stdin, parses `usage.json`, and **captures the returned `session_id`**. `rtk.py` wraps terminal tools and records compression ratios. `orchestrator/jobs.py` runs turns asynchronously with `max_attempts` — bounded, never infinite. A failed turn invalidates the session so the retry is a clean boundary turn.

**Step 6 — CLI driver + incremental summaries.** `python -m orchestrator.cli project new|turn|approve|refresh|status`. `summarizer.py` implements §17.3: `summarize(previous_summary + new_turn)` as its own cheap one-shot, preserving decisions, open questions, constraints, rejected alternatives, artifact status, next actions. Summaries matter most at boundaries, where the session resets and the summary carries continuity across it.

---

## Phase 2 — Telegram (executable detail)

- `telegram/bot.py` — `python-telegram-bot` v21, long-polling, in the orchestrator process (§24 permits this for MVP).
- `telegram/routing.py` — forum topic/thread ID → `project_id`, so the user never prefixes messages with a project name.
- `telegram/handlers.py` — inline keyboards for Approve / Request Changes / Regenerate / View. `callback_data` is capped at 64 bytes, so it carries a short opaque id into a callback table, not an encoded payload.
- **Strictly async:** a handler enqueues to the inbox and returns immediately; it must never hold a network request open while an agent runs for minutes. Pang solved this with `start_new_session=True` detachment; we use an inbox worker plus an outbox writer.
- Rapid back-and-forth in one state naturally becomes continuation turns; **Approve** closes the session and forces the next turn to rebuild.

---

## Phases 3–7 (direction only)

| Phase | Adds |
|---|---|
| 3 — Product workflow | discovery/PRD/product-design/architecture states, artifact versioning via git, decision extraction, approval gates |
| 4 — Codebase intelligence | `DartAnalyzerIndexer`, freshness/incremental indexing, symbol+file retrieval, dependency expansion, relevance ranking — as new context providers feeding guided-retrieval paths |
| 5 — Engineering workflow | builder/reviewer/qa roles, denylist enforcement, diff-aware reviewer context, retry→blocked, git integration |
| 6 — Flutter toolchain | `POST /exec` → RTK-wrapped `flutter pub get / analyze / test / build apk`; device runs stay host-side |
| 7 — Optional infra | SQLite event index, dashboard, cost analytics. **Qdrant only if deterministic retrieval demonstrably fails** |

---

## Verification

**Unit (pure, no LLM, fast):**
- State machine: every transition in `workflow.yaml`; illegal transitions rejected; gates refuse to advance without a typed approval; `max_attempts` exhaustion lands in `blocked`.
- **Session manager: each of the eight boundary triggers invalidates the session, and nothing else does.** Highest-value test in the suite — a missed trigger silently leaks stale context into a new state.
- **Denylist enforcement: a QA package containing `tech-plan.md` or implementation conversation fails the turn.** This is a correctness invariant, not a preference.
- Context builder: §17.2 priority governs selection; volatility governs ordering; budget overflow omits lowest-priority items *and* records a reason for each; dedupe across layers; a full rebuild is byte-identical given identical inputs.
- Delta packages contain only what changed since `session.opened_at_turn`.
- Guided-mode manifests reconstruct correctly from a synthetic `tool-log.jsonl`.
- Human-input semantics: `"looks good"` does **not** approve; `/approve` with no `pending_action` does **not** approve; a callback with a stale artifact revision is rejected.

**Integration:**
- The four spike measurements (Step 2), re-run as regression tests when the Hermes version bumps.
- `POST /run` returns a parseable `usage.json` and a reusable `session_id`.
- **RTK compression: run `flutter test` on the toy app with and without the wrapper; assert a recorded, non-trivial reduction ratio.** If it doesn't pay off here, drop it rather than carrying it on faith.
- `DartAnalyzerIndexer` against a `flutter create` toy app: a known widget class node exists with correct file/line and its `imports` edges resolve.

**End-to-end (the Phase 1 demo):**
```bash
docker compose up -d
./scripts/new-project.sh --name toy --path /d/Projects/toy
python -m orchestrator.cli turn toy "Build a habit tracker for one user."
python -m orchestrator.cli turn toy "Make it support two users instead."
python -m orchestrator.cli approve toy APPROVE_PRD
```
Assert on disk:
- `events.jsonl` contains `PROJECT_CREATED`, `CONTEXT_BUILT`, `SESSION_OPENED`, `AGENT_TURN_COMPLETED`, `APPROVAL_GRANTED`, `SESSION_INVALIDATED`
- Turn 1 is `kind: full` with a complete manifest; **turn 2 is `kind: delta`, `resumed_from` set, materially fewer `input_tokens`**
- **Turn 2's `usage.json` shows non-zero `cache_read_tokens`** — the stable-prefix layout is earning its keep
- The post-approval turn is `kind: full` again with a complete manifest
- `estimated_tokens` ≤ the planner budget; estimate-vs-actual drift logged
- `artifacts/prd.md` exists and revised between turns; `conversations/planning/` persisted
- Re-running turn 1 at the same source revision reproduces a byte-identical prompt

Then the Phase 2 loop: feedback via Telegram → PRD revises as a continuation turn → **Approve** → session closes, state advances to `product-design`, decision written, approval references the artifact revision.

**Acceptance:** for any gate or autonomous turn, the system can answer *why was this file included, why was that one omitted, which index revision was used, how many tokens were sent and at what cache rate, how much tool output was compressed, and can this be reproduced at the same source revision?*
