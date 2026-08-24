"""Context denylist enforcement -- a correctness invariant, not a preference.

Inherited from Pang's QA prompt:

    Read ONLY: {note} (concept + acceptance criteria) and the git diff.
    Do NOT read tech-plan.md, task-log.md, or builder reasoning.

The point is not saving tokens. QA that has read the builder's reasoning
verifies "is this consistent with the plan" instead of "does this meet the
acceptance criteria", and the second question is the one that matters. So a
violation fails the turn rather than warning.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass


class DenylistViolation(Exception):
    """A forbidden item reached a context package. Fails the turn."""

    def __init__(self, role: str, key: str, pattern: str, reason: str | None):
        self.role = role
        self.key = key
        self.pattern = pattern
        super().__init__(
            f"role {role!r} must not receive {key!r} (matched {pattern!r})"
            + (f": {reason.strip()}" if reason else "")
        )


@dataclass(frozen=True)
class ContextPolicy:
    role: str
    allowlist: tuple[str, ...] = ()
    denylist: tuple[str, ...] = ()
    reason: str | None = None

    def _matches(self, key: str, pattern: str) -> bool:
        """Match a manifest key against a policy pattern.

        A pattern ending in '/' denotes a directory prefix, so
        'conversations/implementation/' covers every turn inside it. Otherwise
        glob semantics apply, and a bare path also matches its own '#section'
        suffixes so denying 'artifacts/tech-plan.md' covers its sections too.
        """
        if pattern.endswith("/"):
            return key.startswith(pattern)
        if fnmatch.fnmatch(key, pattern):
            return True
        return key.split("#", 1)[0] == pattern.split("#", 1)[0] and "#" not in pattern

    def check(self, key: str) -> None:
        for pattern in self.denylist:
            if self._matches(key, pattern):
                raise DenylistViolation(self.role, key, pattern, self.reason)

    def permitted(self, key: str) -> bool:
        """Whether an item may be included.

        The denylist is absolute. The allowlist, when present, additionally
        narrows inclusion to declared items -- that is how QA is restricted to
        acceptance criteria plus the diff.
        """
        for pattern in self.denylist:
            if self._matches(key, pattern):
                return False
        if not self.allowlist:
            return True
        return any(self._matches(key, p) for p in self.allowlist)

    def filter(self, keys: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
        """Split keys into (kept, [(dropped, reason)])."""
        kept: list[str] = []
        dropped: list[tuple[str, str]] = []
        for key in keys:
            if self.permitted(key):
                kept.append(key)
            else:
                dropped.append((key, f"excluded by {self.role} context policy"))
        return kept, dropped
