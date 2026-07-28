from __future__ import annotations

import json
from pathlib import Path

from cryoet_organizer.project import ProjectData


DEFAULT_PREFERENCES: dict[str, str] = {
    "save_particle_plots": "false",
    "use_downscaled_thumbnails": "true",
    "thumbnail_cache_location": "dataset/thumbnail-cache",
    "thumbnail_cache_size": "256",
    "gallery_page_size": "50",
}

DEFAULT_GLOBAL_PREFERENCES: dict[str, str] = {
    "display_profile": "auto",
}

DISPLAY_PROFILE_OPTIONS: tuple[str, ...] = (
    "auto",
    "normal",
    "large",
    "extra_large",
)

DISPLAY_PROFILE_LABELS: dict[str, str] = {
    "auto": "Auto",
    "normal": "Normal",
    "large": "Large",
    "extra_large": "Extra large",
}

DISPLAY_PROFILE_METRICS: dict[str, dict[str, float | int]] = {
    "normal": {
        "layout_scale": 1.0,
        "body_size": 10,
        "small_size": 9,
        "heading_size": 14,
        "dialog_heading_size": 12,
        "status_icon_size": 26,
        "debug_banner_size": 10,
        "shortcut_tile_size": 11,
        "shortcut_plus_size": 36,
        "splash_title_size": 14,
        "splash_subtitle_size": 10,
    },
    "large": {
        "layout_scale": 1.3,
        "body_size": 12,
        "small_size": 11,
        "heading_size": 17,
        "dialog_heading_size": 14,
        "status_icon_size": 32,
        "debug_banner_size": 12,
        "shortcut_tile_size": 13,
        "shortcut_plus_size": 44,
        "splash_title_size": 17,
        "splash_subtitle_size": 12,
    },
    "extra_large": {
        "layout_scale": 1.5,
        "body_size": 14,
        "small_size": 12,
        "heading_size": 19,
        "dialog_heading_size": 16,
        "status_icon_size": 36,
        "debug_banner_size": 13,
        "shortcut_tile_size": 14,
        "shortcut_plus_size": 50,
        "splash_title_size": 19,
        "splash_subtitle_size": 13,
    },
}

_GLOBAL_PREFERENCES_PATH = Path.home() / ".cryopal_tomo_preferences.json"


def normalize_display_profile(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if normalized in DISPLAY_PROFILE_OPTIONS:
        return normalized
    return DEFAULT_GLOBAL_PREFERENCES["display_profile"]


def load_global_preferences() -> dict[str, str]:
    payload = dict(DEFAULT_GLOBAL_PREFERENCES)
    try:
        raw = json.loads(_GLOBAL_PREFERENCES_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for key, default in DEFAULT_GLOBAL_PREFERENCES.items():
                value = raw.get(key, default)
                payload[key] = "" if value is None else str(value)
    except Exception:
        pass
    payload["display_profile"] = normalize_display_profile(payload.get("display_profile"))
    return payload


def save_global_preferences(preferences: dict[str, str]) -> dict[str, str]:
    payload = dict(DEFAULT_GLOBAL_PREFERENCES)
    payload.update({key: "" if value is None else str(value) for key, value in preferences.items()})
    payload["display_profile"] = normalize_display_profile(payload.get("display_profile"))
    try:
        _GLOBAL_PREFERENCES_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    return payload


def global_preference(preferences: dict[str, str], key: str, default: str = "") -> str:
    value = preferences.get(key)
    if value is None or str(value).strip() == "":
        if key in DEFAULT_GLOBAL_PREFERENCES:
            return DEFAULT_GLOBAL_PREFERENCES[key]
        return default
    return str(value)


def resolve_display_profile(
    value: str | None,
    *,
    screen_dpi: float | None = None,
    screen_width: int | None = None,
    screen_height: int | None = None,
) -> str:
    profile = normalize_display_profile(value)
    if profile != "auto":
        return profile
    width = int(screen_width or 0)
    height = int(screen_height or 0)
    dpi = float(screen_dpi or 0.0)
    if dpi >= 200 or max(width, height) >= 5000:
        return "extra_large"
    if dpi >= 125 or max(width, height) >= 2500:
        return "large"
    return "normal"


def display_profile_metrics(profile: str) -> dict[str, float | int]:
    resolved = resolve_display_profile(profile)
    return dict(DISPLAY_PROFILE_METRICS.get(resolved, DISPLAY_PROFILE_METRICS["normal"]))


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
