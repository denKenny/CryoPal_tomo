from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypeVar

from cryoet_organizer.appearance import get_project_appearance
from cryoet_organizer.custom_jobs import CustomJobDefinition, get_project_custom_jobs, set_project_custom_jobs
from cryoet_organizer.environments import EnvironmentDefinition, get_project_environments, set_project_environments
from cryoet_organizer.file_resolver import essential_file_roles, file_role_config, set_file_role_config
from cryoet_organizer.job_defaults import build_job_default_registry, get_project_job_default_overrides
from cryoet_organizer.preferences import DEFAULT_PREFERENCES
from cryoet_organizer.project import ProjectData, ProjectState, SETTINGS_SUFFIX
from cryoet_organizer.shortcuts import ShortcutDefinition, get_project_shortcuts, set_project_shortcuts
from cryoet_organizer.slurm import SlurmProfile, get_project_slurm_profiles, set_project_slurm_profiles
from cryoet_organizer.viewer_defaults import get_effective_viewer_defaults, set_project_viewer_defaults


SETTINGS_CATEGORY_LABELS: dict[str, str] = {
    "preferences": "Set preferences",
    "viewer_defaults": "Configure viewer defaults",
    "default_parameters": "Set default parameters",
    "slurm_profiles": "Slurm submission",
    "environments": "Manage environments",
    "custom_job_types": "Manage custom job types",
    "shortcuts": "Manage shortcuts",
    "appearance": "Appearance",
}

SETTINGS_CATEGORY_ORDER: tuple[str, ...] = (
    "preferences",
    "viewer_defaults",
    "default_parameters",
    "slurm_profiles",
    "environments",
    "custom_job_types",
    "shortcuts",
    "appearance",
)


@dataclass(frozen=True)
class SettingsSelectionItem:
    key: str
    label: str


@dataclass(frozen=True)
class SettingsSelectionGroup:
    key: str
    label: str
    items: tuple[SettingsSelectionItem, ...]


T = TypeVar("T")


def settings_selection_label_map(groups: list[SettingsSelectionGroup]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for group in groups:
        for item in group.items:
            if "__empty__" in item.key:
                continue
            labels[item.key] = f"{group.label} > {item.label}"
    return labels


def _ensure_settings_suffix(path: str | Path) -> Path:
    path_obj = Path(path)
    if str(path_obj).endswith(SETTINGS_SUFFIX):
        return path_obj
    return path_obj.with_name(f"{path_obj.name}{SETTINGS_SUFFIX}")


def _singular_item(category: str, label: str) -> SettingsSelectionGroup:
    return SettingsSelectionGroup(
        key=category,
        label=SETTINGS_CATEGORY_LABELS[category],
        items=(SettingsSelectionItem(key=f"{category}::{category}", label=label),),
    )


def _job_default_groups(project: ProjectData) -> SettingsSelectionGroup:
    definitions = build_job_default_registry()
    items = [
        SettingsSelectionItem(
            key=f"default_parameters::job::{definition.namespace}/{definition.group}/{definition.job_key}",
            label=f"{definition.namespace} > {definition.group} > {definition.title}",
        )
        for definition in definitions
    ]
    items.extend(
        SettingsSelectionItem(
            key=f"default_parameters::file_registry::{role}",
            label=f"File registry > {file_role_config(project, role).title}",
        )
        for role in essential_file_roles()
    )
    return SettingsSelectionGroup(
        key="default_parameters",
        label=SETTINGS_CATEGORY_LABELS["default_parameters"],
        items=tuple(items),
    )


def exportable_settings_groups(project: ProjectData) -> list[SettingsSelectionGroup]:
    groups: list[SettingsSelectionGroup] = [
        _singular_item("preferences", "Save particle plots"),
        _singular_item("viewer_defaults", "Viewer defaults"),
        _job_default_groups(project),
        SettingsSelectionGroup(
            key="slurm_profiles",
            label=SETTINGS_CATEGORY_LABELS["slurm_profiles"],
            items=tuple(
                SettingsSelectionItem(key=f"slurm_profiles::{profile.name}", label=profile.name)
                for profile in get_project_slurm_profiles(project)
            )
            or (SettingsSelectionItem(key="slurm_profiles::__empty__", label="(no profiles yet)"),),
        ),
        SettingsSelectionGroup(
            key="environments",
            label=SETTINGS_CATEGORY_LABELS["environments"],
            items=tuple(
                SettingsSelectionItem(key=f"environments::{item.title}", label=item.title)
                for item in get_project_environments(project)
            )
            or (SettingsSelectionItem(key="environments::__empty__", label="(no environments yet)"),),
        ),
        SettingsSelectionGroup(
            key="custom_job_types",
            label=SETTINGS_CATEGORY_LABELS["custom_job_types"],
            items=tuple(
                SettingsSelectionItem(key=f"custom_job_types::{job.name}", label=job.name)
                for job in get_project_custom_jobs(project)
            )
            or (SettingsSelectionItem(key="custom_job_types::__empty__", label="(no custom jobs yet)"),),
        ),
        SettingsSelectionGroup(
            key="shortcuts",
            label=SETTINGS_CATEGORY_LABELS["shortcuts"],
            items=tuple(
                SettingsSelectionItem(key=f"shortcuts::{item.title}", label=item.title)
                for item in get_project_shortcuts(project)
            )
            or (SettingsSelectionItem(key="shortcuts::__empty__", label="(no shortcuts yet)"),),
        ),
        _singular_item("appearance", "Project appearance"),
    ]
    return groups


def _item_name(item_key: str) -> str:
    return item_key.split("::")[-1]


def build_settings_export_payload(project: ProjectData, selected_item_keys: list[str]) -> dict[str, Any]:
    selected = {key for key in selected_item_keys if "__empty__" not in key}
    categories: dict[str, Any] = {}

    if "preferences::preferences" in selected:
        categories["preferences"] = deepcopy(project.state.preferences)
    if "viewer_defaults::viewer_defaults" in selected:
        categories["viewer_defaults"] = get_effective_viewer_defaults(project).to_dict()

    default_job_overrides = deepcopy(get_project_job_default_overrides(project))
    default_patterns: dict[str, Any] = {}
    selected_job_keys = {
        key.removeprefix("default_parameters::job::")
        for key in selected
        if key.startswith("default_parameters::job::")
    }
    selected_roles = {
        key.removeprefix("default_parameters::file_registry::")
        for key in selected
        if key.startswith("default_parameters::file_registry::")
    }
    if selected_job_keys or selected_roles:
        filtered_overrides = {
            job_key: deepcopy(values)
            for job_key, values in default_job_overrides.items()
            if job_key in selected_job_keys
        }
        for role in selected_roles:
            default_patterns[role] = file_role_config(project, role).to_dict()
        categories["default_parameters"] = {
            "job_default_overrides": filtered_overrides,
            "file_registry_patterns": default_patterns,
        }

    selected_slurm_names = {_item_name(key) for key in selected if key.startswith("slurm_profiles::")}
    if selected_slurm_names:
        categories["slurm_profiles"] = [
            profile.to_dict()
            for profile in get_project_slurm_profiles(project)
            if profile.name in selected_slurm_names
        ]

    selected_environment_names = {_item_name(key) for key in selected if key.startswith("environments::")}
    if selected_environment_names:
        categories["environments"] = [
            item.to_dict()
            for item in get_project_environments(project)
            if item.title in selected_environment_names
        ]

    selected_custom_names = {_item_name(key) for key in selected if key.startswith("custom_job_types::")}
    if selected_custom_names:
        categories["custom_job_types"] = [
            job.to_dict()
            for job in get_project_custom_jobs(project)
            if job.name in selected_custom_names
        ]

    selected_shortcut_names = {_item_name(key) for key in selected if key.startswith("shortcuts::")}
    if selected_shortcut_names:
        categories["shortcuts"] = [
            item.to_dict()
            for item in get_project_shortcuts(project)
            if item.title in selected_shortcut_names
        ]

    if "appearance::appearance" in selected:
        categories["appearance"] = get_project_appearance(project).to_dict()

    return {
        "version": 3,
        "categories": categories,
    }


def export_settings_bundle(path: str | Path, project: ProjectData, selected_item_keys: list[str]) -> Path:
    target = _ensure_settings_suffix(path)
    payload = build_settings_export_payload(project, selected_item_keys)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return target


def load_settings_bundle(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("categories"), dict):
        return payload
    if isinstance(payload, dict):
        legacy_categories: dict[str, Any] = {}
        if "job_default_overrides" in payload or "file_registry_patterns" in payload:
            legacy_categories["default_parameters"] = {
                "job_default_overrides": deepcopy(payload.get("job_default_overrides", {})),
                "file_registry_patterns": deepcopy(payload.get("file_registry_patterns", {})),
            }
        return {"version": 1, "categories": legacy_categories}
    return {"version": 1, "categories": {}}


def importable_settings_groups(payload: dict[str, Any]) -> list[SettingsSelectionGroup]:
    categories = payload.get("categories", {})
    if not isinstance(categories, dict):
        return []
    groups_by_key: dict[str, SettingsSelectionGroup] = {}
    if isinstance(categories.get("preferences"), dict):
        groups_by_key["preferences"] = _singular_item("preferences", "Save particle plots")
    if isinstance(categories.get("viewer_defaults"), dict):
        groups_by_key["viewer_defaults"] = _singular_item("viewer_defaults", "Viewer defaults")
    if isinstance(categories.get("default_parameters"), dict):
        default_payload = categories["default_parameters"]
        items: list[SettingsSelectionItem] = []
        overrides = default_payload.get("job_default_overrides", {})
        if isinstance(overrides, dict):
            for job_key in overrides.keys():
                items.append(SettingsSelectionItem(key=f"default_parameters::job::{job_key}", label=job_key))
        patterns = default_payload.get("file_registry_patterns", {})
        if isinstance(patterns, dict):
            for role, config_payload in patterns.items():
                title = role
                if isinstance(config_payload, dict):
                    title = str(config_payload.get("title") or role)
                items.append(
                    SettingsSelectionItem(
                        key=f"default_parameters::file_registry::{role}",
                        label=f"File registry > {title}",
                    )
                )
        groups_by_key["default_parameters"] = SettingsSelectionGroup(
            key="default_parameters",
            label=SETTINGS_CATEGORY_LABELS["default_parameters"],
            items=tuple(items),
        )
    for category, cls_key, label_key in (
        ("slurm_profiles", "name", "slurm_profiles"),
        ("environments", "title", "environments"),
        ("custom_job_types", "name", "custom_job_types"),
        ("shortcuts", "title", "shortcuts"),
    ):
        payload_items = categories.get(category)
        if isinstance(payload_items, list):
            items = tuple(
                SettingsSelectionItem(
                    key=f"{category}::{str(item.get(cls_key, '')).strip()}",
                    label=str(item.get(cls_key, "")).strip(),
                )
                for item in payload_items
                if isinstance(item, dict) and str(item.get(cls_key, "")).strip()
            )
            groups_by_key[category] = SettingsSelectionGroup(
                key=category,
                label=SETTINGS_CATEGORY_LABELS[label_key],
                items=items,
            )
    if isinstance(categories.get("appearance"), dict):
        groups_by_key["appearance"] = _singular_item("appearance", "Project appearance")
    return [groups_by_key[key] for key in SETTINGS_CATEGORY_ORDER if key in groups_by_key]


def conflicting_import_items(project: ProjectData, selected_item_keys: list[str]) -> list[str]:
    existing_default_overrides = set(get_project_job_default_overrides(project).keys())
    conflicts: list[str] = []
    for key in selected_item_keys:
        if key.startswith("preferences::") and project.state.preferences:
            conflicts.append(key)
        elif key.startswith("viewer_defaults::") and project.state.viewer_defaults is not None:
            conflicts.append(key)
        elif key.startswith("default_parameters::job::") and key.removeprefix("default_parameters::job::") in existing_default_overrides:
            conflicts.append(key)
        elif key.startswith("default_parameters::file_registry::") and key.removeprefix("default_parameters::file_registry::") in project.state.file_registry_patterns:
            conflicts.append(key)
        elif key.startswith("slurm_profiles::"):
            if any(profile.name == _item_name(key) for profile in get_project_slurm_profiles(project)):
                conflicts.append(key)
        elif key.startswith("environments::"):
            if any(item.title == _item_name(key) for item in get_project_environments(project)):
                conflicts.append(key)
        elif key.startswith("custom_job_types::"):
            if any(job.name == _item_name(key) for job in get_project_custom_jobs(project)):
                conflicts.append(key)
        elif key.startswith("shortcuts::"):
            if any(item.title == _item_name(key) for item in get_project_shortcuts(project)):
                conflicts.append(key)
        elif key.startswith("appearance::") and project.state.appearance:
            conflicts.append(key)
    return conflicts


def _merge_named_items(
    existing: list[T],
    imported: list[T],
    name_getter: Callable[[T], str],
    overwrite: bool,
) -> list[T]:
    result = list(existing)
    index_by_name = {name_getter(item): index for index, item in enumerate(result)}
    for item in imported:
        name = name_getter(item)
        if name in index_by_name:
            if overwrite:
                result[index_by_name[name]] = item
        else:
            result.append(item)
            index_by_name[name] = len(result) - 1
    return result


def apply_settings_import(
    project: ProjectData,
    payload: dict[str, Any],
    selected_item_keys: list[str],
    *,
    overwrite_existing: bool,
) -> tuple[list[str], list[str]]:
    categories = payload.get("categories", {})
    if not isinstance(categories, dict):
        return [], selected_item_keys

    applied: list[str] = []
    skipped: list[str] = []
    selected = {key for key in selected_item_keys if "__empty__" not in key}

    if "preferences::preferences" in selected and isinstance(categories.get("preferences"), dict):
        if project.state.preferences and not overwrite_existing:
            skipped.append("preferences::preferences")
        else:
            project.state.preferences = deepcopy(categories["preferences"])
            applied.append("preferences::preferences")

    if "viewer_defaults::viewer_defaults" in selected and isinstance(categories.get("viewer_defaults"), dict):
        if project.state.viewer_defaults is not None and not overwrite_existing:
            skipped.append("viewer_defaults::viewer_defaults")
        else:
            project.state.viewer_defaults = deepcopy(categories["viewer_defaults"])
            applied.append("viewer_defaults::viewer_defaults")

    default_payload = categories.get("default_parameters")
    if isinstance(default_payload, dict):
        imported_overrides = default_payload.get("job_default_overrides", {})
        if isinstance(imported_overrides, dict):
            for job_key, values in imported_overrides.items():
                item_key = f"default_parameters::job::{job_key}"
                if item_key not in selected:
                    continue
                if job_key in project.state.job_default_overrides and not overwrite_existing:
                    skipped.append(item_key)
                    continue
                project.state.job_default_overrides[job_key] = deepcopy(values)
                applied.append(item_key)
        imported_patterns = default_payload.get("file_registry_patterns", {})
        if isinstance(imported_patterns, dict):
            for role, config_payload in imported_patterns.items():
                item_key = f"default_parameters::file_registry::{role}"
                if item_key not in selected:
                    continue
                if role in project.state.file_registry_patterns and not overwrite_existing:
                    skipped.append(item_key)
                    continue
                temp_project = ProjectData(state=ProjectState(file_registry_patterns={role: config_payload}))
                set_file_role_config(project, role, file_role_config(temp_project, role))
                applied.append(item_key)

    if isinstance(categories.get("slurm_profiles"), list):
        selected_names = {_item_name(key) for key in selected if key.startswith("slurm_profiles::")}
        imported_profiles = [
            SlurmProfile.from_dict(item)
            for item in categories["slurm_profiles"]
            if isinstance(item, dict) and str(item.get("name", "")).strip() in selected_names
        ]
        existing = get_project_slurm_profiles(project)
        merged = _merge_named_items(existing, imported_profiles, lambda item: item.name, overwrite_existing)
        set_project_slurm_profiles(project, merged)
        for profile in imported_profiles:
            key = f"slurm_profiles::{profile.name}"
            if any(existing_profile.name == profile.name for existing_profile in existing) and not overwrite_existing:
                skipped.append(key)
            else:
                applied.append(key)

    if isinstance(categories.get("environments"), list):
        selected_names = {_item_name(key) for key in selected if key.startswith("environments::")}
        imported_items = [
            EnvironmentDefinition.from_dict(item)
            for item in categories["environments"]
            if isinstance(item, dict) and str(item.get("title", "")).strip() in selected_names
        ]
        existing = get_project_environments(project)
        merged = _merge_named_items(existing, imported_items, lambda item: item.title, overwrite_existing)
        set_project_environments(project, merged)
        for item in imported_items:
            key = f"environments::{item.title}"
            if any(existing_item.title == item.title for existing_item in existing) and not overwrite_existing:
                skipped.append(key)
            else:
                applied.append(key)

    if isinstance(categories.get("custom_job_types"), list):
        selected_names = {_item_name(key) for key in selected if key.startswith("custom_job_types::")}
        imported_items = [
            CustomJobDefinition.from_dict(item)
            for item in categories["custom_job_types"]
            if isinstance(item, dict) and str(item.get("name", "")).strip() in selected_names
        ]
        existing = get_project_custom_jobs(project)
        merged = _merge_named_items(existing, imported_items, lambda item: item.name, overwrite_existing)
        set_project_custom_jobs(project, merged)
        for item in imported_items:
            key = f"custom_job_types::{item.name}"
            if any(existing_item.name == item.name for existing_item in existing) and not overwrite_existing:
                skipped.append(key)
            else:
                applied.append(key)

    if isinstance(categories.get("shortcuts"), list):
        selected_names = {_item_name(key) for key in selected if key.startswith("shortcuts::")}
        imported_items = [
            ShortcutDefinition.from_dict(item)
            for item in categories["shortcuts"]
            if isinstance(item, dict) and str(item.get("title", "")).strip() in selected_names
        ]
        existing = get_project_shortcuts(project)
        merged = _merge_named_items(existing, imported_items, lambda item: item.title, overwrite_existing)
        set_project_shortcuts(project, merged)
        for item in imported_items:
            key = f"shortcuts::{item.title}"
            if any(existing_item.title == item.title for existing_item in existing) and not overwrite_existing:
                skipped.append(key)
            else:
                applied.append(key)

    if "appearance::appearance" in selected and isinstance(categories.get("appearance"), dict):
        if project.state.appearance and not overwrite_existing:
            skipped.append("appearance::appearance")
        else:
            project.state.appearance = deepcopy(categories["appearance"])
            applied.append("appearance::appearance")

    return applied, skipped
