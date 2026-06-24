from __future__ import annotations

from cryoet_organizer.project import ProjectData


DEFAULT_PREFERENCES: dict[str, str] = {
    "save_particle_plots": "false",
    "use_downscaled_thumbnails": "true",
    "thumbnail_cache_location": "dataset/thumbnail-cache",
    "thumbnail_cache_size": "256",
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


def project_preference_int(
    project: ProjectData,
    key: str,
    default: int = 0,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        value = int(project_preference(project, key, str(default)).strip())
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value
