"""The project registry -- config/projects.yaml.

Deliberately tiny: a project entry is just enough to find its two
directories (host source tree, agent state) and know its platform. Anything
richer (Telegram routing, per-project overrides) is added to an entry as
Phase 2/3 need it, not speculatively here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ProjectNotFound(Exception):
    pass


class ProjectAlreadyExists(Exception):
    pass


@dataclass(frozen=True)
class ProjectEntry:
    name: str
    host_path: str
    state_path: str
    platform: str = "flutter"
    telegram_chat_id: int | None = None
    telegram_thread_id: int | None = None


class ProjectRegistry:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"projects": {}}
        doc = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        doc.setdefault("projects", {})
        return doc

    def _write(self, doc: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

    def list(self) -> list[str]:
        return sorted(self._read()["projects"].keys())

    def _entry_from_spec(self, spec: dict[str, Any]) -> ProjectEntry:
        return ProjectEntry(
            name=spec["name"], host_path=spec["host_path"],
            state_path=spec["state_path"], platform=spec.get("platform", "flutter"),
            telegram_chat_id=spec.get("telegram_chat_id"),
            telegram_thread_id=spec.get("telegram_thread_id"),
        )

    def get(self, project_id: str) -> ProjectEntry:
        doc = self._read()
        spec = doc["projects"].get(project_id)
        if spec is None:
            raise ProjectNotFound(project_id)
        return self._entry_from_spec(spec)

    def register(
        self, project_id: str, *, host_path: str, state_path: str, platform: str = "flutter",
    ) -> ProjectEntry:
        doc = self._read()
        if project_id in doc["projects"]:
            raise ProjectAlreadyExists(project_id)
        entry = {
            "name": project_id, "host_path": host_path,
            "state_path": state_path, "platform": platform,
        }
        doc["projects"][project_id] = entry
        self._write(doc)
        return ProjectEntry(**entry)

    # ---- Telegram routing (Phase 2) ---------------------------------------
    #
    # A project already has exactly one canonical identity in this registry,
    # so routing lives on its own entry rather than in a separate mapping
    # file -- there is nothing a second file could say that isn't just
    # "this project" restated.

    def link_telegram(
        self, project_id: str, *, chat_id: int, thread_id: int | None,
    ) -> ProjectEntry:
        doc = self._read()
        spec = doc["projects"].get(project_id)
        if spec is None:
            raise ProjectNotFound(project_id)
        spec["telegram_chat_id"] = chat_id
        spec["telegram_thread_id"] = thread_id
        doc["projects"][project_id] = spec
        self._write(doc)
        return self._entry_from_spec(spec)

    def find_by_telegram(
        self, *, chat_id: int, thread_id: int | None,
    ) -> ProjectEntry | None:
        for spec in self._read()["projects"].values():
            if (
                spec.get("telegram_chat_id") == chat_id
                and spec.get("telegram_thread_id") == thread_id
            ):
                return self._entry_from_spec(spec)
        return None
