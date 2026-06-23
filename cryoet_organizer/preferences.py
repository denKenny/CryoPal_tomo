from __future__ import annotations

from cryoet_organizer.project import ProjectData


DEFAULT_PREFERENCES: dict[str, str] = {
    "save_particle_plots": "false",
}


def project_preference(project: ProjectData, key: str, default: str = "") -> str:
    if key in project.state.preferences:
        value = project.state.preferences.get(key, default)
        return "" if value is None else str(value)
    if key in DEFAULT_PREFERENCES:
        return DEFAULT_PREFERENCES[key]
    return default


def project_preference_enabled(project: ProjectData, key: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return project_preference(project, key, fallback).strip().lower() in {"1", "true", "yes", "on"}
