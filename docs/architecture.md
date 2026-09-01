# Architecture

This is a reference document: what the layering contract is today, and how
it's enforced. For the history of how the codebase got here, see
`docs/adr/0001-layered-package.md` and `docs/progress.md`.

## The package

All application code lives under `src/agentic_dev/`, one installable
package (`pip install -e .`, console scripts `agentic` / `agentic-bot`).
It's organized into five ranked layers:

| Rank | Layer | Contains | Example |
|---|---|---|---|
| 0 | `domain/` | Pure logic. No `os`, no `Path(__file__)`, no default paths, no I/O. | `domain/workflow.py`, `domain/context/builder.py` |
| 1 | `ports/` | `Protocol`s with more than one real implementation. | `ports/indexer.py`, `ports/agent_runtime.py` |
| 2 | `adapters/` | Concrete I/O — filesystem, subprocess, HTTP, git. | `adapters/hermes/invocation.py`, `adapters/telegram/topics.py` |
| 3 | `app/` | Use cases — orchestrate domain + ports/adapters into one operation. | `app/turn_runner.py`, `app/approval_flow.py` |
| 4 | `entrypoints/` | Inbound boundaries — CLI, Telegram bot, HTTP server. | `entrypoints/cli.py`, `entrypoints/telegram/bot.py` |

`settings.py` sits outside the layer tree, at the package root. It is the
single module allowed to reason about where the workspace lives — see
"Where does what live" in the main `README.md` and its own module
docstring for the resolution order. Nothing else in the codebase may
evaluate `Path(__file__)` or a bare `__file__`.

## The rule

**Imports only ever point downward, or stay within the same rank.** A
module in layer *N* may import its own layer or any layer with a strictly
lower rank; it may never import a layer with a higher rank. Concretely:

- `domain` imports nothing above it — not even `ports`.
- `ports` may import `domain`.
- `adapters` may import `ports` and `domain`.
- `app` may import `adapters`, `ports`, and `domain`.
- `entrypoints` may import `app`, `adapters`, `ports`, and `domain`.

**Entrypoints may reach into `adapters` directly**, skipping `app`, because
`entrypoints` is the composition root — it's where concrete adapters get
selected and wired together (for example, choosing `HttpAgentRuntime` vs.
an in-process runtime based on `AGENTIC_RUNTIME_URL`) before being handed
to an `app`-layer use case. This is still a downward edge (rank 4 → rank
2), not an exception to the rule above — it's called out here only because
skipping a layer can look surprising at first glance.

### The one documented same-rank exception

`adapters/` is a single rank, and one adapter is allowed to import a
sibling adapter: `adapters/context/providers.py` composes
`adapters/storage`, `adapters/indexing`, and `adapters/git` to assemble a
context package from several on-disk sources. This is same-rank (2 → 2),
so it's already permitted by the downward-or-same-rank rule as written —
it's named explicitly (in both this document and the guard test's own
docstring) so nobody mistakes it for an oversight when looking for a
stricter "adapters may never import each other" rule that the shape of the
rule doesn't actually claim.

No other cross-adapter import is expected. If one shows up, treat it as a
sign that the shared logic belongs in `domain` or `ports` instead of in
either adapter.

## Enforcement

`tests/test_architecture.py` is the mechanism, not the description — this
document should never drift from what that test actually checks. It's a
plain AST walk over every `.py` file under `src/agentic_dev/` (no
third-party dependency, runs in well under 100ms as part of the normal
`pytest` run) asserting three things:

1. **No upward imports.** For every module, every `agentic_dev.<layer>...`
   import it makes is resolved to a target layer, and the target's rank
   must be ≤ the importer's rank.
2. **`domain/` never imports `os`.** Anything that needs "the environment"
   takes it as a parameter instead (see
   `domain/roles.py::resolve_env_placeholders`, which is handed a mapping
   rather than calling `os.environ.get` itself).
3. **Only `settings.py` uses `__file__`.** Every other module that needs a
   path is handed one, rather than computing it by climbing from its own
   file location.

Run it on its own with:

```bash
pytest tests/test_architecture.py -q
```

It runs as part of the full suite (`pytest -q`) like every other test —
there's no separate CI step to remember.

## Why this shape

The layering isn't decorative: before this refactor, five different
modules independently computed their own path by climbing
`Path(__file__).resolve().parents[N]`, each with a different `N`. Any file
move broke one of them silently — a missing soul file, a missing index, or
a missing project source all degraded to "quietly do nothing" rather than
raising. Concentrating that concern in one module (`settings.py`), and
having a cheap automated test that would fail loudly the moment a new
`Path(__file__)` callsite appeared anywhere else, closes that failure class
structurally rather than by code-review discipline.

The rank ordering also killed a real import cycle that existed in the flat
layout (an entrypoint-ish module importing what was really an adapter, and
vice versa) — see `docs/adr/0001-layered-package.md` for the specific
before/after.
