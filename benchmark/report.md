# Context/token benchmark

Same 9-turn scenario across 4 workflow states (discovery -> planning -> product-design -> architecture, each gated by an approval), replayed against our hybrid context/session design and against a naive Hermes-default with no orchestration layer. `hermes_run` is mocked on both sides -- what's measured is what each design actually assembles and sends, not model behavior.

| # | State | Ours: turn kind | Ours tokens | Naive tokens | Naive / Ours |
|---|---|---|---|---|---|
| 1 | discovery | full-rebuild | 329 | 16 | 0.05x |
| 2 | discovery | continuation | 586 | 213 | 0.36x |
| 3 | planning | full-rebuild | 420 | 461 | 1.10x |
| 4 | planning | continuation | 740 | 762 | 1.03x |
| 5 | planning | continuation | 733 | 999 | 1.36x |
| 6 | product-design | full-rebuild | 426 | 1190 | 2.79x |
| 7 | product-design | continuation | 734 | 1481 | 2.02x |
| 8 | architecture | full-rebuild | 437 | 1615 | 3.70x |
| 9 | architecture | continuation | 702 | 1862 | 2.65x |

**Total tokens -- ours: 5107, naive: 8599 (40.6% reduction).**

Naive's total grows monotonically because nothing is ever removed from its history and there is no budget to enforce it against; ours resets to a small, budgeted rebuild at each approval boundary (turns 1, 3, 6, 8) and only ever resends the delta on continuation turns. The gap widens with every state the project passes through -- by the last state the naive prompt is carrying the full discovery-through-product-design transcript into a turn that only needs the architecture role's own context.

## Denylist correctness (not a token metric)

- **Ours:** refused to build: role 'qa' must not receive 'artifacts/tech-plan.md' (matched 'artifacts/tech-plan.md'): QA must verify against acceptance criteria, not be anchored by builder reasoning. Taken directly from Pang's handle_qa prompt.
- **Naive:** included verbatim -- a naive prompt has no policy object to consult, so QA would read the builder's own reasoning about *how* it was built instead of verifying *what* it built against the acceptance criteria
