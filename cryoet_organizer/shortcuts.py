from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from cryoet_organizer.project import ProjectData


SHORTCUTS_SUFFIX = ".cryopal.shortcuts.json"


@dataclass
class ShortcutDefinition:
    title: str
    script: str = ""
    color: str = "#d9e7ff"

    @classmethod
    def from_dict(cls, payload: dict) -> "ShortcutDefinition":
        return cls(
            title=str(payload.get("title", "")).strip(),
            script=str(payload.get("script", "")),
            color=str(payload.get("color", "#d9e7ff")).strip() or "#d9e7ff",
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def get_project_shortcuts(project: ProjectData) -> list[ShortcutDefinition]:
    payload = project.state.shortcuts
    return [
        ShortcutDefinition.from_dict(item)
        for item in payload
        if isinstance(item, dict) and str(item.get("title", "")).strip()
    ]


def set_project_shortcuts(project: ProjectData, shortcuts: list[ShortcutDefinition]) -> None:
    project.state.shortcuts = [item.to_dict() for item in shortcuts if item.title.strip()]


def _ensure_suffix(path: str | Path) -> Path:
    path_obj = Path(path)
    if str(path_obj).endswith(SHORTCUTS_SUFFIX):
        return path_obj
    return path_obj.with_name(f"{path_obj.name}{SHORTCUTS_SUFFIX}")


def export_shortcuts(path: str | Path, shortcuts: list[ShortcutDefinition]) -> Path:
    target = _ensure_suffix(path)
    target.write_text(
        json.dumps([item.to_dict() for item in shortcuts], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def import_shortcuts(path: str | Path) -> list[ShortcutDefinition]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Shortcut file must contain a list of shortcut definitions.")
    return [
        ShortcutDefinition.from_dict(item)
        for item in payload
        if isinstance(item, dict) and str(item.get("title", "")).strip()
    ]
