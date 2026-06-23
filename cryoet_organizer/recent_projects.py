from __future__ import annotations

import json
from pathlib import Path


_RECENT_PATH = Path.home() / ".cryopal_tomo_recent.json"
_MAX_RECENT = 10


def load_recent_projects() -> list[str]:
    """Return a list of recently opened project paths (existing files only)."""
    try:
        payload = json.loads(_RECENT_PATH.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [str(p) for p in payload if p and Path(str(p)).exists()]
    except Exception:
        pass
    return []


def add_recent_project(path: str | Path) -> None:
    """Prepend *path* to the recent-projects list and persist it."""
    path_str = str(Path(path).resolve())
    entries = load_recent_projects()
    if path_str in entries:
        entries.remove(path_str)
    entries.insert(0, path_str)
    _save_recent(entries[:_MAX_RECENT])


def _save_recent(entries: list[str]) -> None:
    try:
        _RECENT_PATH.write_text(
            json.dumps(entries, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
