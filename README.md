# Agentic Dev

Local-first, Dockerized agentic engineering workspace — a redesign of
[isfaaghyth/pang](https://github.com/isfaaghyth/pang) with a hybrid Hermes
memory model, role-split context assembly, and a deterministic workflow
engine. Full architecture and rationale: `docs/plan.md` (mirrors the approved
planning-session output). What's actually done vs. still spec, phase by
phase: `docs/progress.md`.

## Status

**Docker and Python are working. Phase 1 Steps 0–5 are done.** The Hermes
spike (Step 2) and the invocation layer it gates (Step 5) were both run and
debugged for real against `hermes` v0.20.5 + OpenRouter (`openai/gpt-4o-mini`)
— see `docs/plan.md` § "Phase 1 spike" for the full results table.

```bash
pip install -e ".[dev]"
pytest
```

241/241 tests pass. The test suite is the actual specification of the
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
| `orchestrator/config.py` | Loads `agents.yaml`/`models.yaml`, resolves `${VAR:-default}` |
| `orchestrator/registry.py` | `config/projects.yaml` — project name → host path / state path |
| `orchestrator/jobs.py` | `TurnRunner` — one full turn, wired end to end, verified live |
| `orchestrator/cli.py` | `project new` / `turn` / `approve` / `status`, verified live end to end |
| `orchestrator/approval_flow.py` | `apply_approval()` — the §31 approval path shared by the CLI and Telegram, so neither reimplements it |
| `orchestrator/summarizer.py` | §17.3 incremental summarization — one cheap one-shot per successful turn, folded into a running per-state summary; optional (skipped with no `summarizer` route configured) |
| `telegram_bot/` | `/link`, `/status`, plain-text turns via an async inbox worker, inline-button approvals with stale-revision rejection, auto-created forum topics per project |
| `runtime/server.py` | `POST /run` — the HTTP boundary for the container topology; wraps `runtime/hermes.py` unchanged. Not yet called by `jobs.py` (see below) |
| `benchmark/` | Context/token effectiveness vs. a naive Hermes-default, plus a denylist correctness check |

## What's still a spec, not code

- An RTK output-compression wrapper (`runtime/rtk.py`).
  `docker/spike/Dockerfile` proves the base image works (Hermes CLI +
  OpenRouter installed and callable); the real agent-runtime Dockerfile
  still needs Flutter/Android/JDK/RTK added on top of it.
- Codebase intelligence (Dart indexer, Graphify adapter) — Phase 4.
- `compose.yaml`.

## Phase 1 is done and verified live

`orchestrator/jobs.py` (`TurnRunner`) ties state machine + sessions + context
builder + `runtime/hermes.py` + store into one turn. `orchestrator/cli.py`
drives it: `project new`, `turn`, `approve`, `status`. `orchestrator/config.py`
and `orchestrator/registry.py` load `agents.yaml`/`models.yaml`/`projects.yaml`.
169/169 tests pass, all with `hermes_run` monkeypatched — no subprocess, no
network call, no cost.

**Then the whole thing was run for real**, no mocks, against the actual
CLI and OpenRouter (`docker/spike/demo.py`): `project new` → `approve
START_PROJECT` → two discovery turns (the second one a genuine
`chat -q --resume`, confirmed by the response correctly incorporating the
first turn's requirement) → `approve APPROVE_DISCOVERY` → `status` showing
`session: (none)` (the boundary invalidation firing live, not just in a
unit test) → a fresh planning turn → `approve APPROVE_PRD`. Every state
transition, every session open/resume/invalidate, and the exact-typed-
approval rejection (a wrong approval type was correctly refused, exit 1)
behaved exactly as designed.

## Incremental summarization (§17.3)

The one piece of Phase 1's original Step 6 that was scoped but never built
until now: `orchestrator/summarizer.py`. After every successful turn (not
just at boundaries -- conditioning it on `SessionManager`'s own boundary
decision would couple two things that don't need to know about each other,
and it's one cheap call on a cheap model, `config/models.yaml` routes
`summarizer` to `deepseek-v3`), `TurnRunner` folds the new turn into a
running per-state summary: `summarize(previous_summary + new_turn)`,
preserving decisions, open questions, constraints, rejected alternatives,
artifact status, and next actions. `context/providers.py::SummaryProvider`
then feeds that summary into every later context build (layer 4, alongside
recent turns, per the brief's own cache-layout grouping).

This is what a session boundary actually loses without it: `SESSION_
INVALIDATED` means the next turn's rebuild has nothing but the artifact and
whatever raw conversation still fits in `RecentTurnsProvider`'s
3-turn window. The summary is what carries continuity across that reset
instead of relying on an opaque, now-discarded Hermes transcript. Optional
by construction — a project whose `models.yaml` has no `summarizer` route
just never calls this, same "absent config, unchanged behavior" pattern as
Telegram's forum-topic auto-creation.

## The runtime HTTP boundary

```bash
uvicorn runtime.server:app --port 8000
```

`runtime/server.py` — `POST /run` wraps `runtime.hermes.run()` behind HTTP,
unchanged: same argv selection, same `HERMES_HOME` env handling, same
response cleaning. This is the container-topology boundary the brief calls
for — the orchestrator has no Docker socket (least privilege), so it can't
`docker exec` into agent-runtime; agent-runtime exposes this instead.

Verified live: started the real server, hit `/health`, then `POST /run`
with no `hermes` binary installed on this host (it only exists inside
`docker/spike/`'s container) — got back a real `HTTP 502` with
`"failed to launch hermes: [WinError 2] The system cannot find the file
specified"`, proving the request parsing → invocation attempt → error
mapping path end to end, not just against a mock. 7 tests
(`tests/test_server.py`) cover the same paths with `hermes_run` mocked for
speed and repeatability.

**`orchestrator/jobs.py` now calls this over HTTP when configured to.**
`runtime/client.py`'s `run()` is the same contract as `runtime.hermes.run()`
(same request shape, same `HermesResult`), just reachable over HTTP.
`TurnRunner` takes an optional `runtime_url`; unset (the default), it
calls `runtime.hermes.run()` in-process exactly as before — every prior
live run stays reproducible with zero config. Set `AGENTIC_RUNTIME_URL`
(read by both `orchestrator/cli.py` and `telegram_bot/handlers.py`) and
every Hermes invocation routes through the HTTP server instead, no other
code changes needed.

Verified live end to end, across real process boundaries: started the real
server, ran `orchestrator.cli project new` → `approve` → `turn` in a
**separate CLI process** with `AGENTIC_RUNTIME_URL` pointing at it. The CLI
process's `httpx` call reached the server process, which attempted a real
(and here, absent) `hermes` launch, returned a real 502, which
`runtime/client.py` turned into `RuntimeClientError`, which `jobs.py`
re-raised as a `RuntimeError` that surfaced cleanly in the CLI's own
traceback — the full chain, not simulated in one process. 5 more tests
(`tests/test_runtime_client.py`) plus 3 in `test_jobs.py` cover the
in-process/HTTP branch selection and error mapping with things mocked.

## Benchmark: does the context/session design actually help?

```bash
python -m benchmark.run_benchmark
```

Replays a fixed 9-turn scenario (discovery → planning → product-design →
architecture, each state gated by an approval) through two paths:
**ours** (`TurnRunner` — real state machine, real session manager, real
`ContextBuilder`, `hermes_run` mocked) vs. a **naive Hermes-default**
(`benchmark/naive_baseline.py` — no orchestrator, no budget, nothing ever
removed from history). Same human messages, same canned responses, same
`TokenEstimator` on both sides.

Current result (`benchmark/report.md`, regenerated by the command above):
**40.6% fewer total tokens** — naive grows monotonically because there's
nothing to bound it, ours resets to a small budgeted rebuild at every
approval boundary and only ever resends the delta on continuation turns.
The gap widens with every state the project passes through (0.05x at turn 1
→ 3.70x by turn 8), which is the point: the win compounds over a project's
lifetime, not a single turn. It also runs a structural check — handed a
denylisted artifact, our QA context package refuses to build
(`DenylistViolation`); a naive prompt has no such mechanism and would
include it. Both claims are pinned as regression tests in
`tests/test_benchmark.py`.

**Not yet built:** a `--live` mode driving real Hermes/OpenRouter calls to
compare actual billed `input_tokens`/`cache_read_tokens`/cost between the
two paths. The mock above proves what each design *sends*, which is what's
under our control; a live run would additionally validate the provider's
own accounting, at the cost of real API spend.

## Phase 2: Telegram

```bash
export TELEGRAM_BOT_TOKEN=<from @BotFather>
export TELEGRAM_FORUM_CHAT_ID=<optional -- for CLI-side auto-topic-creation>
export TELEGRAM_DEFAULT_HOST_ROOT=<optional -- default: ~/agentic-dev-projects>
python -m telegram_bot.bot
```

Named `telegram_bot/`, not `telegram/` — the plan's original name collides
with the installed `python-telegram-bot` package, which imports as
`telegram`. A local `telegram/` directory on `sys.path` would have shadowed
it for every import in the process, including the bot's own
`from telegram import ...`. Caught before it shipped, not after.

`telegram_bot/bot.py` drives the exact same `TurnRunner` as the CLI, from a
different entry point:

- **`/create` — new project, new forum topic, both from inside Telegram.**
  Send `/create` anywhere in a Topics-enabled supergroup (the bot needs
  "Manage Topics" admin rights there); it asks for a name, then registers
  the project (default host path via `TELEGRAM_DEFAULT_HOST_ROOT`, since a
  chat message can't hand over a filesystem path the way `--host-path`
  does), auto-clears a leading gate state if the workflow has one, creates
  the topic via `telegram_bot/topics.py::create_forum_topic()` (plain
  synchronous `httpx` against the Bot API — no event loop needed for one
  REST call), links it, and posts a confirmation *inside the new topic*. A
  Telegram-side failure (chat not a forum, bot not an admin) is reported
  but never discards the project it already created.
- **Same automation from the CLI.** Set `TELEGRAM_FORUM_CHAT_ID` alongside
  `TELEGRAM_BOT_TOKEN` and `orchestrator/cli.py project new` does the same
  create-topic-and-link step for a project made outside Telegram. Optional
  either way: with neither env var set, project creation is unchanged.
- `/link <project_id>` still works for manual linking (or for a topic
  created by hand) — `telegram_bot/routing.py`, backed by the same
  `ProjectRegistry.link_telegram()`. No project-name prefix needed on later
  messages either way.
- `/create`, `/link`, `/status` show up as Telegram's own autocomplete
  suggestions when typing `/` — `telegram_bot/bot.py`'s `COMMANDS` list is
  the single source for both `CommandHandler` registration and a
  `setMyCommands` call in `post_init` (has to run there, not at build time:
  it's an API call, needs the bot's event loop already up). Verified live
  against `getMyCommands` directly, across every scope Telegram supports
  (default, all_group_chats, per-chat, chat_administrators, ...) — only
  `default` had anything, and it was exactly these three. If the menu looks
  stale or shows commands from a bot token reused for something else
  before, that's Telegram's own client-side cache, not the server state —
  force-closing and reopening the app clears it.
- Plain text enqueues a `TurnJob` and returns immediately; a single
  background `inbox_worker` task drains the queue via `asyncio.to_thread`,
  so a multi-minute Hermes call never blocks the bot's event loop or any
  other chat. `ProjectStore` requires per-project write serialization
  ("two writers to one project is a bug, not a case to merge") — one global
  queue is a safe superset of that.
- Every response in a state that requires approval gets an inline "Approve"
  button. Clicking it goes through the same `apply_approval()` the CLI uses
  (`orchestrator/approval_flow.py`, extracted so both entry points enforce
  identical §31 semantics), via `resolve_callback()` — which is what makes
  the plan's own acceptance test possible: **a button referencing a stale
  artifact revision is rejected**, not silently approved against whatever
  the project has moved on to.

Tested with fakes for `Update`/`context` (`tests/test_telegram_handlers.py`,
`tests/test_telegram_routing.py`) — no real Bot API or network call.

### Verified live against a real bot and a real chat

`docker/spike/telegram_live_test.py` runs the real bot (real long-polling
against api.telegram.org, real `/link`, real inline button) with `hermes_run`
mocked — Hermes itself was already verified live in Phase 1; the only new
surface here is Telegram, so that's the only thing worth spending a real
run on. `/link toy` → a plain-text message → a real click on the "Approve"
button all went through, confirmed from `events.jsonl` afterward, not just
from what Telegram showed on screen:

```
AGENT_TURN_STARTED  (discovery-01, kind: full)
AGENT_TURN_COMPLETED
SESSION_OPENED
AGENT_TURN_STARTED  (discovery-03, kind: delta -- a real continuation turn)
AGENT_TURN_COMPLETED
APPROVAL_GRANTED    approver: "telegram:<the tester's real Telegram user id>"
```

`session: null` after the approval confirms boundary invalidation fired on
the real Telegram path, not just in a unit test; the real Telegram user id
in `approver` confirms the inline button's click genuinely round-tripped
through `resolve_callback()`.

**One real bug the live run found:** `httpx` (python-telegram-bot's HTTP
client) logs the full request URL at `INFO`, and every Bot API URL embeds
the token (`api.telegram.org/bot<TOKEN>/getMe`) — so `logging.basicConfig
(level=logging.INFO)` alone leaks the token into any log output. Found the
hard way on the first run. **Fixed** in both `telegram_bot/bot.py` and the
live-test script: `logging.getLogger("httpx").setLevel(logging.WARNING)`
right after configuring root logging, unconditionally.

**`/create` verified live too**, same script, real Bot API: `/create` →
typed a name in reply → a real forum topic appeared in the group, the
project's `state.yaml` showed `workflow_state: discovery` (the leading
`backlog` gate auto-cleared, `approver: "telegram:<real user id>"`), and a
follow-up plain-text message plus an "Approve" click both went through
inside the newly created topic — the whole loop, starting from a chat
message and ending with a working, linked project, with no CLI step at
all.
