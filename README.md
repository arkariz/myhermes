# Agentic Dev

A local-first, Dockerized system that runs a small team of AI agents
(planner, designer, architect, builder, reviewer, QA) through a
deterministic workflow — discovery → planning → design → architecture →
implementation → review → QA — with typed approval gates, real git
history, and a codebase-aware index for Flutter/Dart projects. Drive it
from the CLI or from Telegram.

Want the full design rationale, phase-by-phase build history, or the
layering rules? See [Learn more](#learn-more) at the bottom. This document
is just: get it running, configure it, use it.

## 1. Prerequisites

- **Python 3.11+** (for the CLI/local path) — or **Docker Desktop** (for
  the full container topology + Telegram)
- **git**
- **An LLM API key.** Defaults to [OpenRouter](https://openrouter.ai/) —
  any provider Hermes supports works, just change `models.yaml` (see
  [Configuration](#3-configuration)).
- **The [Hermes CLI](https://hermes-agent.nousresearch.com)**, if running
  outside Docker. The `agent-runtime` Docker image already includes it.

## 2. Quick start

Two setup steps, then two paths depending on whether you want Telegram.

### Step 1 — install and bootstrap a workspace

```bash
pip install -e ".[dev]"
agentic init-workspace ../agentic-workspace
```

This creates a **separate sibling repo**, `../agentic-workspace`, holding
your config, generated state, and project registry — kept out of this code
repo on purpose (see [Where things live](#4-where-things-live)). Run this
once per machine.

### Step 2 — add your API key

```bash
cp .env.example .env
```

Open `.env` and fill in `OPENROUTER_API_KEY`. That's the only required
value — everything else is optional.

### Path A — CLI only, no Docker, no Telegram (fastest)

```bash
export $(grep -v '^#' .env | xargs)   # loads .env into your shell
agentic project new toy --host-path /path/to/a/real/project
agentic turn toy "Build a habit tracker for one user."
agentic status toy
```

`--host-path` points at a real project on your machine — a Flutter/Dart
project gets the most out of this (codebase indexing, diff-aware review),
but any git repo works. From here the workflow drives itself: answer the
planner's questions with `agentic turn toy "..."`, approve gates with
`agentic approve toy <TYPE>`, check progress with `agentic status toy`.

### Path B — Docker Compose, full topology + Telegram

```bash
docker compose build
docker compose up -d
```

Both containers mount `../agentic-workspace` at `/workspace`. Use
`--host-path /workspace/projects/<name>` for a project created via the
CLI, or use `/create` from Telegram (see [Telegram setup](#6-telegram-setup)).

```bash
docker compose logs -f orchestrator   # watch turns, approvals, Telegram activity
docker compose run --rm orchestrator agentic status toy   # one-off CLI command against the running stack
```

## 3. Configuration

Everything behavior-related lives in `../agentic-workspace/config/` —
**edited by hand, no rebuild needed.**

### API key

`hermes/.env` → `OPENROUTER_API_KEY=...`

### Which model each role uses

`../agentic-workspace/config/models.yaml`:

```yaml
routing:
  builder:
    provider: openrouter
    model: ${BUILDER_MODEL:-deepseek/deepseek-v3}
```

Either edit the default directly, or override per-role from `.env` without
touching the file — uncomment the matching line in `.env.example`:

```bash
BUILDER_MODEL=anthropic/claude-sonnet-4.6
```

### Skills, toolsets, and budgets per role

`../agentic-workspace/config/agents.yaml`:

```yaml
builder:
  context_mode: guided
  toolsets: [skills, terminal, file]
  skills: [flutter-widgets, dart-testing]   # named Hermes skill files
  budget:
    max_input_tokens: 20000
    max_output_tokens: 6000
```

- `toolsets` — which Hermes tool categories a role gets (`file`,
  `terminal`, `skills`).
- `skills` — which named skill files Hermes loads for that role (`--skills`).
  Leave empty for Hermes's own default selection.
- `budget` — token ceilings per turn.

### Session and timeout behavior

Same file, top level:

```yaml
sessions:
  max_session_turns: 12
  max_session_age_minutes: 120

timeouts:
  hermes_turn_seconds: 600   # raise this if a slower model needs more time
```

### Personas

`../agentic-workspace/config/souls/<role>.md` — plain markdown, one file
per role.

### Workflow graph

`../agentic-workspace/config/workflow.yaml` — the state machine itself
(states, transitions, which are gated by approval). Only touch this if
you're changing the actual process, not just tuning a role.

## 4. Where things live

| Location | What | Git |
|---|---|---|
| `hermes/.env` | Secrets: API key, Telegram token/chat id, workspace path, optional model overrides | gitignored — never commit |
| `hermes/.env.example` | The same keys, blank/commented | tracked, code repo |
| `../agentic-workspace/config/*.yaml`, `souls/*.md` | Everything above: models, roles, skills, budgets, workflow, personas | tracked, workspace repo — meant to be pushed |
| `../agentic-workspace/config/projects.yaml` | Project registry (name → paths) | tracked, workspace repo |
| `../agentic-workspace/agent-state/` | Generated: turn history, indexes, per-project Hermes memory | gitignored |
| `../agentic-workspace/projects/` | Your actual project source, if placed there | gitignored — each project keeps its own `.git` |

Secrets stay in the **code repo's** `.env` (gitignored since day one)
specifically because the workspace repo is meant to be committed and
pushed. `AGENTIC_WORKSPACE` in `.env` is the one line connecting the two —
leave it unset and it defaults to the sibling `../agentic-workspace`.

`agentic init-workspace --check` confirms a workspace is found (and
where), without creating anything — useful as a sanity check or a
container health-check.

## 5. Everyday commands

```bash
agentic project new <id> --host-path <path>   # register a project
agentic turn <id> "<message>"                 # send a message
agentic approve <id> <APPROVAL_TYPE>           # pass a gate
agentic status <id>                            # workflow state, attempts, session
agentic init-workspace ../agentic-workspace     # (re)bootstrap a workspace
agentic init-workspace --check                  # verify resolution, no side effects
```

Without the installed console script, prefix any of the above with
`python -m agentic_dev.entrypoints.cli`.

Progress is visible in two places at any point: `agentic status <id>`, or
the raw event log at
`../agentic-workspace/agent-state/<id>/events.jsonl`.

## 6. Telegram setup

1. Create a bot via [@BotFather](https://t.me/BotFather); copy the token
   into `.env` as `TELEGRAM_BOT_TOKEN`.
2. Create a Telegram group with **Topics (forum mode) enabled**, add the
   bot as admin with **"Manage Topics"** permission.
3. Get the chat id: `curl https://api.telegram.org/bot<TOKEN>/getUpdates`
   after sending any message in the group — look for `"chat":{"id": ...}`.
   Put it in `.env` as `TELEGRAM_FORUM_CHAT_ID`.
4. Run the bot:

```bash
agentic-bot
# or, via Docker Compose: docker compose up -d
```

In the group: `/create` starts a new project (asks for a name, creates a
dedicated topic automatically). `/link <id>` attaches an existing project
to the current chat/topic instead. `/status` shows workflow state. Plain
messages in a linked topic are turns; approval gates show up as an inline
"Approve" button.

## 7. Project structure

```
hermes/                      code repo — pure code
├── src/agentic_dev/
│   ├── domain/               pure logic — workflow, sessions, context assembly
│   ├── ports/                 interfaces (AgentRuntime, CodebaseIndexer)
│   ├── adapters/              concrete I/O — Hermes, git, storage, Telegram
│   ├── app/                   use cases — TurnRunner, approval flow
│   └── entrypoints/            CLI, Telegram bot, HTTP server
├── deploy/                    Dockerfiles
├── compose.yaml
└── tests/

../agentic-workspace/        separate repo — your setup
├── config/                    models, roles, workflow, souls   (tracked)
├── agent-state/                generated state                (ignored)
└── projects/                    your project source            (ignored)
```

Layering rule: imports only ever point downward (`domain → ports →
adapters → app → entrypoints`), enforced by `tests/test_architecture.py`.
Full contract: `docs/architecture.md`.

## 8. Testing

```bash
pytest
```

344 tests, no network calls, no cost — everything real (Hermes CLI, git,
Docker, HTTP) is either mocked or exercised in an isolated integration
test. This suite is the actual specification of the system's invariants.

## Troubleshooting

- **Git Bash on Windows mangles paths.** `docker compose run ... --host-path
  /workspace/...` gets rewritten into a Windows path by MSYS before Docker
  ever sees it. Fix: prefix with `MSYS_NO_PATHCONV=1`.
- **A model override in `.env` has no effect.** Confirm the line is
  uncommented and matches the exact role name in `models.yaml` (e.g.
  `BUILDER_MODEL`, not `BUILD_MODEL`).
- **"No workspace found."** Run `agentic init-workspace ../agentic-workspace`,
  or set `AGENTIC_WORKSPACE` in `.env` to wherever you put it.
  `agentic init-workspace --check` tells you exactly what was resolved.
- **Telegram `/create` fails to make a topic.** The group needs Topics
  (forum mode) enabled, and the bot needs "Manage Topics" admin rights.
  The project is still created either way — link it manually with `/link`.
- **A turn does nothing / no LLM was called.** Check `OPENROUTER_API_KEY`
  is actually set in the environment the process sees (`export $(grep -v
  '^#' .env | xargs)` for the CLI; `docker compose up` reads `.env`
  automatically for Docker).

## Learn more

- `docs/architecture.md` — the layering contract, in full
- `docs/adr/0001-layered-package.md` — why the code/workspace split exists
- `docs/plan.md` — original design brief and rationale
- `docs/progress.md` — phase-by-phase build checklist, what's done vs. spec
