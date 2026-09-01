# ADR-0001: Layered `src/agentic_dev/` package, workspace as a separate repo

**Status:** Accepted · **Date:** 2026-08-25 · **Deciders:** project maintainer

## Context

The codebase worked and was verified live end to end (Docker, real Hermes
CLI, real Telegram bot), but it grew by accretion: eight flat top-level
directories (`orchestrator/`, `runtime/`, `indexing/`, `telegram_bot/`,
`config/`, `docker/`, `secrets/`, `docker/spike/`), no enforced layering,
and several structural problems that were verified during analysis rather
than assumed:

- A real import cycle: `orchestrator/cli.py` imported `telegram_bot.topics`.
- Five modules independently computed their own location via
  `Path(__file__).resolve().parents[N]`, each with a different `N`. Any
  file move broke one of them **silently** — a missing soul file, a
  missing index, or a missing project source all degraded to doing
  nothing rather than raising.
- A CLI test wrote souls to one path while `cli.py` read from another,
  and every test still passed, because the consumer (`RoleSoulProvider`)
  silently returned `[]` on a missing file instead of erroring.
- Dockerfiles hand-duplicated the dependency list and used selective
  `COPY`, which had already shipped one production bug (a module omitted
  from the image, failing only when its route was hit).
- `config/projects.yaml` was both git-tracked *and* runtime-mutated by the
  registry — every project registration was an uncommitted local diff.
- Secrets existed in three places with every value duplicated twice
  (`.env`, `docker/spike/secrets/.env.spike`, `secrets/.env.telegram`),
  the sharp edge being that rotating a credential in `.env` alone left a
  revoked value live on disk indefinitely in the other two.

The goal was clear layering instead of a flat namespace, a hard separation
between dockerized code, user-runnable workspace, and development docs,
and closing the silent-failure classes above structurally, not by
discipline.

## Decision

1. **Single `src/agentic_dev/` package, five ranked layers.**
   `domain(0) → ports(1) → adapters(2) → app(3) → entrypoints(4)`. Imports
   point downward or stay same-rank; `entrypoints` may reach `adapters`
   directly as the composition root. Enforced by an AST-walking test
   (`tests/test_architecture.py`) with no third-party dependency, run as
   part of the normal suite. See `docs/architecture.md` for the full
   contract.
2. **The workspace (`config/`, `agent-state/`, `projects/`) moved to a
   separate sibling git repo, `../agentic-workspace`**, rather than
   staying inside this repo. `config/` is tracked there; `agent-state/`
   and `projects/` are gitignored (each project under `projects/` keeps
   its own `.git`, so nothing nests). Bootstrapped from a template shipped
   inside the package (`src/agentic_dev/templates/workspace/config`) via
   `agentic init-workspace <path>`.
3. **Secrets consolidated to one `.env`, in the code repo — not the
   workspace repo.** `secrets/` and `docker/spike/secrets/` (and
   `docker/spike/` itself, whose four scripts were the only readers of
   the duplicate secret files) are deleted outright.
4. **`settings.py` is the single place allowed to reason about
   `Path(__file__)` or locate the workspace.** Every other module is
   handed a path rather than computing one. Resolution order: explicit
   `$AGENTIC_WORKSPACE`, then a sibling `../agentic-workspace` found by
   marker search upward from CWD, then a loud `WorkspaceNotFound` naming
   the exact `agentic init-workspace` command to run.

## Options Considered

**A. Keep the flat layout, fix the cycle and the path-climbing ad hoc.**
Rejected — it would have fixed the one cycle that was found, but nothing
stops the next one, and nothing stops a sixth module from adding its own
`Path(__file__).resolve().parents[N]`. No structural guard, only more
discipline asked of the same kind of mistake that already happened five
times.

**B. Layered package, but keep `config/`/`agent-state/`/`projects/` inside
this repo.** Rejected — `config/projects.yaml` would stay both tracked and
runtime-mutated (every project registration a dirty diff against the code
repo's own history), and a user's actual project source would end up
nested inside this repo's working tree, one `git add -A` away from being
swept into a commit it has no business being in.

**C. Layered package, workspace as a separate repo, `.env` also moved into
the workspace repo** (since the workspace is "the user's setup"). Rejected
— the workspace repo is designed to be committed and pushed (it tracks
`config/`); a secrets file sitting in a pushable repo is one `git add -A`
away from leaking, whereas the code repo's `.env` has been reliably
gitignored since day one. Compose also reads `.env` from the compose
project directory (the code repo root) with zero flags; moving it would
mean `--env-file ../agentic-workspace/.env` on every invocation, or an
environment variable export that silently stops applying in a fresh shell.

**D. A ports-and-adapters split with more than two ports.** Considered and
rejected as premature — only `indexer` and `agent_runtime` have two live
implementations each, selected at runtime (`DartAnalyzerIndexer` /
`RemoteIndexer`; in-process Hermes / `HttpAgentRuntime`). No other seam in
the codebase has a second real implementation today, so no other port was
speculatively introduced.

## Trade-off Analysis

| | Chosen (layered package + separate workspace repo) | Rejected: flat + ad hoc fixes | Rejected: workspace stays in-repo |
|---|---|---|---|
| Import cycles | Structurally prevented (rank order + AST test) | Possible again with the next new module | Same protection as chosen, doesn't address this axis |
| Path-climbing fragility | One module (`settings.py`) can break; everything else is handed a path | Five-plus call sites, each a silent-failure risk | Same protection as chosen, doesn't address this axis |
| Project-registry drift | `config/projects.yaml` lives in a repo whose whole purpose is to be mutated locally | Unchanged — still a tracked-and-mutated file | Not solved — still tracked and mutated inside the code repo |
| Secrets exposure | One gitignored `.env`, never in a pushable repo | Unchanged — three files, two duplicated | Not addressed by this axis alone |
| Upgrade story for shipped defaults | Diffing template vs. workspace copy — a new, honest cost | N/A | N/A |
| Fresh-machine setup | One explicit `agentic init-workspace` step | Implicit, nothing to run, but implicit "just clone and it works" is what let projects.yaml drift go unnoticed | Implicit, same drift risk |

## Consequences

**Easier:** adding a new adapter without touching unrelated layers,
testing `domain` with fakes (nothing in it can reach the filesystem),
reasoning about exactly what ships to which Docker image (`orchestrator`
extras vs. `runtime` extras in `pyproject.toml`), running the layering
guard as a normal 100ms test instead of a manual review checklist.

**Harder:** upgrading a shipped default (a soul file, a budget) no longer
reaches an existing workspace automatically — it now means diffing the
package's `templates/workspace/config` against the user's own
`../agentic-workspace/config`. This is called out in `README.md` rather
than solved here; a possible `agentic init-workspace --diff` affordance is
noted as future work, not built.

**To revisit:** whether `agent-state/` should eventually be tracked (it
isn't today — regenerated indexes and turn history, deliberately
disposable).
