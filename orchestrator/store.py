"""Durable, human-inspectable project state on the filesystem.

Everything the system knows lives as Markdown/YAML/JSONL under a project's
agent-state directory. No database is authoritative. If a container dies
mid-turn, the next process reads the same tree and continues.

Writes are atomic (tempfile in the same directory + os.replace) because state
files may be watched, synced, or read concurrently by the dashboard. Pang
learned this the hard way against Obsidian LiveSync; the pattern is cheap
insurance regardless of what is watching.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import yaml


def utcnow() -> str:
    """ISO-8601 UTC timestamp. Single source so every record sorts lexically."""
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, data: str) -> None:
    """Replace `path` with `data` atomically.

    The temp file is created in the *same* directory so os.replace stays on one
    filesystem; across devices it would degrade to a copy and lose atomicity.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Never leave a partial temp file behind on failure.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class ProjectStore:
    """Filesystem layout for one project's agent state.

    Only this class knows where things live. Everything else asks for a path,
    so relocating the layout is a change in one file.
    """

    def __init__(self, state_root: Path | str, source_root: Path | str | None = None):
        self.root = Path(state_root)
        self.source_root = Path(source_root) if source_root else None

    # ---- paths -----------------------------------------------------------

    @property
    def state_file(self) -> Path:
        return self.root / "state.yaml"

    @property
    def events_file(self) -> Path:
        return self.root / "events.jsonl"

    @property
    def task_log(self) -> Path:
        return self.root / "task-log.md"

    def artifact(self, name: str) -> Path:
        return self.root / "artifacts" / name

    def decisions_dir(self) -> Path:
        return self.root / "decisions"

    def conversation_dir(self, workflow_state: str) -> Path:
        return self.root / "conversations" / workflow_state

    def summary_file(self, name: str) -> Path:
        return self.root / "summaries" / f"{name}.md"

    def turn_dir(self, turn_id: str) -> Path:
        return self.root / "turns" / turn_id

    def index_dir(self) -> Path:
        return self.root / "indexes"

    def hermes_home(self) -> Path:
        """The HERMES_HOME every runtime call for this project must use.

        Verified isolation boundary (Phase 1 spike, not assumed): Hermes's
        background memory writer persists to <HERMES_HOME>/memories/MEMORY.md
        and two profiles sharing one HERMES_HOME both recalled the same
        planted secret in live testing. Living inside this project's own
        agent-state tree means it is backed up and recoverable exactly like
        the rest of the project's state, and structurally cannot be shared
        with another project's tree.
        """
        return self.root / ".hermes-home"

    # ---- state -----------------------------------------------------------

    def read_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        loaded = yaml.safe_load(self.state_file.read_text(encoding="utf-8"))
        return loaded or {}

    def write_state(self, state: dict[str, Any]) -> None:
        _atomic_write(
            self.state_file,
            yaml.safe_dump(state, sort_keys=False, allow_unicode=True),
        )

    def update_state(self, **fields: Any) -> dict[str, Any]:
        """Shallow-merge `fields` into state.yaml and persist.

        Read-modify-write is not locked. Concurrency is handled a level up by
        serialising all mutations for a given project through its job queue --
        two writers to one project is a bug, not a case to merge.
        """
        state = self.read_state()
        state.update(fields)
        self.write_state(state)
        return state

    # ---- events ----------------------------------------------------------

    def append_event(self, event: dict[str, Any]) -> None:
        """Append one event to the JSONL log.

        Append-only and never rewritten, so a plain open('a') is safe and
        atomic enough for single-line writes on both POSIX and Windows.
        """
        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self.events_file.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")

    def read_events(self) -> Iterator[dict[str, Any]]:
        if not self.events_file.exists():
            return
        with self.events_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    # ---- turns -----------------------------------------------------------

    def write_turn_artifact(self, turn_id: str, filename: str, content: str) -> Path:
        path = self.turn_dir(turn_id) / filename
        _atomic_write(path, content)
        return path

    def read_turn_artifact(self, turn_id: str, filename: str) -> str | None:
        path = self.turn_dir(turn_id) / filename
        return path.read_text(encoding="utf-8") if path.exists() else None

    # ---- conversations ---------------------------------------------------

    def next_turn_sequence(self, workflow_state: str) -> int:
        """Next 1-based turn number within a workflow state.

        Derived from what is on disk rather than a counter in state.yaml, so a
        crash between writing a turn and updating state cannot desynchronise
        the two.
        """
        d = self.conversation_dir(workflow_state)
        if not d.exists():
            return 1
        highest = 0
        for entry in d.iterdir():
            head = entry.name.split("-", 1)[0]
            if head.isdigit():
                highest = max(highest, int(head))
        return highest + 1

    def append_conversation_turn(
        self, workflow_state: str, actor: str, content: str
    ) -> Path:
        """Persist one raw turn. Retained for auditability, not for context."""
        seq = self.next_turn_sequence(workflow_state)
        path = self.conversation_dir(workflow_state) / f"{seq:03d}-{actor}.md"
        _atomic_write(path, content)
        return path

    # ---- bootstrap -------------------------------------------------------

    def ensure_layout(self) -> None:
        for sub in ("artifacts", "decisions", "conversations", "summaries", "turns", "indexes"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
