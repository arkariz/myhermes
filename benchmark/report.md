# Context/token benchmark

Same 9-turn scenario across 4 workflow states (discovery -> planning -> product-design -> architecture, each gated by an approval), replayed against our hybrid context/session design and against a naive Hermes-default with no orchestration layer. `hermes_run` is mocked on both sides -- what's measured is what each design actually assembles and sends, not model behavior.

| # | State | Ours: turn kind | Ours tokens | Naive tokens | Naive / Ours |
|---|---|---|---|---|---|
| 1 | discovery | full-rebuild | 400 | 16 | 0.04x |
| 2 | discovery | continuation | 668 | 213 | 0.32x |
| 3 | planning | full-rebuild | 491 | 461 | 0.94x |
| 4 | planning | continuation | 822 | 762 | 0.93x |
| 5 | planning | continuation | 815 | 999 | 1.23x |
| 6 | product-design | full-rebuild | 489 | 1190 | 2.43x |
| 7 | product-design | continuation | 808 | 1481 | 1.83x |
| 8 | architecture | full-rebuild | 501 | 1615 | 3.22x |
| 9 | architecture | continuation | 777 | 1862 | 2.40x |

**Total tokens -- ours: 5771, naive: 8599 (32.9% reduction).**

Naive's total grows monotonically because nothing is ever removed from its history and there is no budget to enforce it against; ours resets to a small, budgeted rebuild at each approval boundary (turns 1, 3, 6, 8) and only ever resends the delta on continuation turns. The gap widens with every state the project passes through -- by the last state the naive prompt is carrying the full discovery-through-product-design transcript into a turn that only needs the architecture role's own context.

## Denylist correctness (not a token metric)

- **Ours:** refused to build: role 'qa' must not receive 'artifacts/tech-plan.md' (matched 'artifacts/tech-plan.md'): QA must verify against acceptance criteria, not be anchored by builder reasoning. Taken directly from Pang's handle_qa prompt.
- **Naive:** included verbatim -- a naive prompt has no policy object to consult, so QA would read the builder's own reasoning about *how* it was built instead of verifying *what* it built against the acceptance criteria
