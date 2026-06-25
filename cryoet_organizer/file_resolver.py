from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from fnmatch import fnmatch
from pathlib import Path

from cryoet_organizer.project import (
    DatasetRecord,
    ProjectData,
    best_matching_paths_for_ts,
    dataset_ts_names,
    prepared_mdoc_path_for_ts,
)


FILE_REGISTRY_PATTERNS_METADATA_KEY = "file_registry_patterns"

_DIR_LISTING_CACHE: dict[tuple[str, bool], tuple[float, list[Path]]] = {}
_CACHE_TTL = 5.0


def _list_dir_cached(base_dir: Path, recursive: bool) -> list[Path]:
    """Return all files under *base_dir*, cached for up to _CACHE_TTL seconds."""
    key = (str(base_dir), recursive)
    entry = _DIR_LISTING_CACHE.get(key)
    if entry is not None:
        ts, items = entry
        if time.monotonic() - ts < _CACHE_TTL:
            return items
    iterator = base_dir.rglob("*") if recursive else base_dir.iterdir()
    items = [p for p in iterator if p.is_file()]
    _DIR_LISTING_CACHE[key] = (time.monotonic(), items)
    return items
FILE_REGISTRY_OVERRIDES_METADATA_KEY = "file_registry_overrides"
FILE_REGISTRY_ROLE_ORDER_METADATA_KEY = "file_registry_role_order"


@dataclass
class FileRoleConfig:
    role: str
    title: str
    description: str
    base_dir_template: str
    filename_pattern: str
    exclude_patterns: str = ""
    recursive: bool = False
    selection_mode: str = "unique"
    apply_ts_matching: bool = True

    @classmethod
    def from_dict(cls, role: str, payload: dict, fallback: "FileRoleConfig") -> "FileRoleConfig":
        return cls(
            role=role,
            title=str(payload.get("title", fallback.title)),
            description=str(payload.get("description", fallback.description)),
            base_dir_template=str(payload.get("base_dir_template", fallback.base_dir_template)),
            filename_pattern=str(payload.get("filename_pattern", fallback.filename_pattern)),
            exclude_patterns=str(payload.get("exclude_patterns", fallback.exclude_patterns)),
            recursive=bool(payload.get("recursive", fallback.recursive)),
            selection_mode=str(payload.get("selection_mode", fallback.selection_mode)) or fallback.selection_mode,
            apply_ts_matching=bool(payload.get("apply_ts_matching", fallback.apply_ts_matching)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResolvedFileRecord:
    role: str
    dataset_name: str
    ts_name: str
    path: str = ""
    source: str = "missing"
    note: str = ""


DEFAULT_FILE_ROLE_CONFIGS: dict[str, FileRoleConfig] = {
    "tomogram": FileRoleConfig(
        role="tomogram",
        title="Tomogram",
        description="Tomogram reconstruction volume used by Gallery and Tomogram jobs.",
        base_dir_template="{tilt_series_processing_folder}/reconstruction",
        filename_pattern="*.mrc",
        recursive=False,
        selection_mode="newest",
        apply_ts_matching=True,
    ),
    "aligned_stack": FileRoleConfig(
        role="aligned_stack",
        title="Aligned stack",
        description="Aligned tilt stack, typically *_ali.mrc inside the tiltstack directory.",
        base_dir_template="{tilt_series_processing_folder}/tiltstack/{ts_name}",
        filename_pattern="*_ali.mrc",
        recursive=False,
        selection_mode="unique",
        apply_ts_matching=True,
    ),
    "angle_file": FileRoleConfig(
        role="angle_file",
        title="Angle file",
        description="Tilt-angle file, usually *.tlt while excluding *_fid.tlt.",
        base_dir_template="{tilt_series_processing_folder}/tiltstack/{ts_name}",
        filename_pattern="*.tlt",
        exclude_patterns="*_fid.tlt",
        recursive=False,
        selection_mode="unique",
        apply_ts_matching=True,
    ),
    "mdoc": FileRoleConfig(
        role="mdoc",
        title="MDOC file",
        description="MDOC metadata source associated with a TS.",
        base_dir_template="{mdocs_source_folder}",
        filename_pattern="*.mdoc",
        recursive=False,
        selection_mode="unique",
        apply_ts_matching=True,
    ),
    "tomostar": FileRoleConfig(
        role="tomostar",
        title="Tomostar file",
        description="Tomostar metadata file associated with a TS.",
        base_dir_template="{tilt_series_data_folder}",
        filename_pattern="*.tomostar",
        recursive=False,
        selection_mode="unique",
        apply_ts_matching=True,
    ),
    "ts_xml": FileRoleConfig(
        role="ts_xml",
        title="TS XML file",
        description="Per-TS Warp XML metadata file associated with a TS.",
        base_dir_template="{tilt_series_processing_folder}",
        filename_pattern="*.xml",
        recursive=False,
        selection_mode="unique",
        apply_ts_matching=True,
    ),
}

LEGACY_TS_ROLE_MAP = {
    "ts_aligned_stack": "aligned_stack",
    "ts_angle_file": "angle_file",
    "ts_tomogram": "tomogram",
}


def essential_file_roles() -> set[str]:
    return set(DEFAULT_FILE_ROLE_CONFIGS.keys())


def normalize_file_role(role: str) -> str:
    return LEGACY_TS_ROLE_MAP.get(role, role)


def file_role_order(project: ProjectData | None = None) -> list[str]:
    base_roles = list(DEFAULT_FILE_ROLE_CONFIGS.keys())
    if project is None:
        return base_roles
    order_payload = project.state.file_registry_role_order
    custom_roles: list[str] = []
    patterns = project.state.file_registry_patterns
    if isinstance(patterns, dict):
        for role in patterns.keys():
            role_key = normalize_file_role(str(role))
            if role_key not in base_roles and role_key not in custom_roles:
                custom_roles.append(role_key)
    ordered_custom: list[str] = []
    if isinstance(order_payload, list):
        for item in order_payload:
            role_key = normalize_file_role(str(item))
            if role_key not in base_roles and role_key in custom_roles and role_key not in ordered_custom:
                ordered_custom.append(role_key)
    for role in custom_roles:
        if role not in ordered_custom:
            ordered_custom.append(role)
    return base_roles + ordered_custom


def file_role_config(project: ProjectData, role: str) -> FileRoleConfig:
    role = normalize_file_role(role)
    fallback = DEFAULT_FILE_ROLE_CONFIGS.get(
        role,
        FileRoleConfig(
            role=role,
            title=role.replace("_", " ").title(),
            description="Custom file role.",
            base_dir_template="",
            filename_pattern="*",
        ),
    )
    payload = project.state.file_registry_patterns
    role_payload = payload.get(role, {})
    if not isinstance(role_payload, dict):
        return fallback
    return FileRoleConfig.from_dict(role, role_payload, fallback)


def all_file_role_configs(project: ProjectData) -> dict[str, FileRoleConfig]:
    configs: dict[str, FileRoleConfig] = {}
    payload = project.state.file_registry_patterns
    for role in file_role_order(project):
        if role in DEFAULT_FILE_ROLE_CONFIGS:
            configs[role] = file_role_config(project, role)
        else:
            role_payload = payload.get(role, {}) if isinstance(payload, dict) else {}
            if isinstance(role_payload, dict):
                configs[role] = FileRoleConfig.from_dict(
                    role,
                    role_payload,
                    FileRoleConfig(
                        role=role,
                        title=role.replace("_", " ").title(),
                        description="Custom file role.",
                        base_dir_template="",
                        filename_pattern="*",
                    ),
                )
    return configs


def set_file_role_config(project: ProjectData, role: str, config: FileRoleConfig) -> None:
    role = normalize_file_role(role)
    payload = project.state.file_registry_patterns
    payload[role] = config.to_dict()
    if role not in essential_file_roles():
        order = file_role_order(project)
        if role not in order:
            project.state.file_registry_role_order = order + [role]


def add_custom_file_role(project: ProjectData, config: FileRoleConfig) -> None:
    role = normalize_file_role(config.role)
    set_file_role_config(project, role, FileRoleConfig(
        role=role,
        title=config.title,
        description=config.description,
        base_dir_template=config.base_dir_template,
        filename_pattern=config.filename_pattern,
        exclude_patterns=config.exclude_patterns,
        recursive=config.recursive,
        selection_mode=config.selection_mode,
        apply_ts_matching=config.apply_ts_matching,
    ))
    order = file_role_order(project)
    if role not in order:
        project.state.file_registry_role_order = order + [role]


def remove_custom_file_role(project: ProjectData, role: str) -> None:
    role = normalize_file_role(role)
    if role in essential_file_roles():
        return
    payload = project.state.file_registry_patterns
    if isinstance(payload, dict):
        payload.pop(role, None)
    overrides = _all_overrides(project)
    for dataset_payload in list(overrides.values()):
        if isinstance(dataset_payload, dict):
            dataset_payload.pop(role, None)
    order = [item for item in file_role_order(project) if item != role and item not in essential_file_roles()]
    project.state.file_registry_role_order = order


def _all_overrides(project: ProjectData) -> dict:
    return project.state.file_registry_overrides


def file_override(project: ProjectData, dataset_name: str, role: str, ts_name: str) -> str:
    payload = _all_overrides(project)
    dataset_payload = payload.get(dataset_name, {})
    if not isinstance(dataset_payload, dict):
        return ""
    role_payload = dataset_payload.get(role, {})
    if not isinstance(role_payload, dict):
        return ""
    value = role_payload.get(ts_name, "")
    return str(value).strip()


def set_file_override(project: ProjectData, dataset_name: str, role: str, ts_name: str, path: str) -> None:
    payload = _all_overrides(project)
    dataset_payload = payload.setdefault(dataset_name, {})
    if not isinstance(dataset_payload, dict):
        dataset_payload = {}
        payload[dataset_name] = dataset_payload
    role_payload = dataset_payload.setdefault(role, {})
    if not isinstance(role_payload, dict):
        role_payload = {}
        dataset_payload[role] = role_payload
    cleaned = path.strip()
    if cleaned:
        role_payload[ts_name] = cleaned
    else:
        role_payload.pop(ts_name, None)


def clear_dataset_role_overrides(project: ProjectData, dataset_name: str, role: str) -> None:
    payload = _all_overrides(project)
    dataset_payload = payload.get(dataset_name, {})
    if not isinstance(dataset_payload, dict):
        return
    dataset_payload.pop(role, None)
    if not dataset_payload:
        payload.pop(dataset_name, None)


def clear_dataset_overrides(project: ProjectData, dataset_name: str) -> None:
    payload = _all_overrides(project)
    payload.pop(dataset_name, None)


def role_titles() -> dict[str, str]:
    return {role: config.title for role, config in DEFAULT_FILE_ROLE_CONFIGS.items()}


def role_title(project: ProjectData, role: str) -> str:
    return file_role_config(project, role).title


def _template_context(dataset: DatasetRecord, ts_name: str) -> dict[str, str]:
    return {
        "dataset_name": dataset.dataset_name,
        "sample": dataset.sample,
        "processing_folder": dataset.processing_folder,
        "processing_root_folder": dataset.processing_root_folder,
        "raw_frames_folder": dataset.raw_frames_folder,
        "mdocs_folder": dataset.mdocs_folder,
        "mdocs_source_folder": dataset.mdocs_source_folder or dataset.mdocs_folder,
        "tilt_series_processing_folder": dataset.tilt_series_processing_folder,
        "frame_series_processing_folder": dataset.frame_series_processing_folder,
        "tilt_series_data_folder": dataset.tilt_series_data_folder,
        "ts_name": ts_name,
    }


def _render_template(template: str, dataset: DatasetRecord, ts_name: str) -> str:
    try:
        return template.format(**_template_context(dataset, ts_name)).strip()
    except Exception:
        return template.strip()


def _exclude_patterns(config: FileRoleConfig) -> list[str]:
    return [item.strip() for item in config.exclude_patterns.split(";") if item.strip()]


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _candidate_base_dirs(dataset: DatasetRecord, ts_name: str, config: FileRoleConfig) -> list[Path]:
    if config.role == "tomogram" and dataset.tomogram_folder.strip():
        return [Path(dataset.tomogram_folder.strip())]
    base_dir_text = _render_template(config.base_dir_template, dataset, ts_name)
    if not base_dir_text:
        return []
    base_dir = Path(base_dir_text)
    candidates = [base_dir]
    if config.role == "tomogram":
        if base_dir.name == "reconstruction":
            candidates.append(base_dir.with_name("reconstructions"))
        elif base_dir.name == "reconstructions":
            candidates.append(base_dir.with_name("reconstruction"))
    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(item)
    return unique_candidates


def _matching_candidates(dataset: DatasetRecord, ts_name: str, config: FileRoleConfig) -> list[Path]:
    base_dirs = _candidate_base_dirs(dataset, ts_name, config)
    if not base_dirs:
        return []
    exclude_patterns = _exclude_patterns(config)
    pattern = _render_template(config.filename_pattern, dataset, ts_name) or "*"
    candidates: list[Path] = []
    seen_paths: set[str] = set()
    for base_dir in base_dirs:
        if not base_dir.exists():
            continue
        all_files = _list_dir_cached(base_dir, config.recursive)
        for item in all_files:
            if not fnmatch(item.name, pattern):
                continue
            if exclude_patterns and any(fnmatch(item.name, exclude) for exclude in exclude_patterns):
                continue
            path_key = str(item)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)
            candidates.append(item)
    candidates.sort(key=lambda item: item.name.casefold())
    if config.apply_ts_matching:
        candidates = best_matching_paths_for_ts(candidates, ts_name)
    return candidates


def resolve_dataset_file(
    project: ProjectData,
    dataset: DatasetRecord,
    ts_name: str,
    role: str,
) -> ResolvedFileRecord:
    role = normalize_file_role(role)
    manual = file_override(project, dataset.dataset_name, role, ts_name)
    if manual:
        manual_path = Path(manual)
        if manual_path.exists():
            return ResolvedFileRecord(
                role=role,
                dataset_name=dataset.dataset_name,
                ts_name=ts_name,
                path=str(manual_path),
                source="manual override",
            )
        return ResolvedFileRecord(
            role=role,
            dataset_name=dataset.dataset_name,
            ts_name=ts_name,
            path=manual,
            source="manual override",
            note="Manual override path does not currently exist.",
        )

    if role == "mdoc" and dataset.unified_mdoc_names:
        mapped = prepared_mdoc_path_for_ts(dataset, ts_name).strip()
        if not mapped:
            return ResolvedFileRecord(
                role=role,
                dataset_name=dataset.dataset_name,
                ts_name=ts_name,
                source="missing",
                note="No prepared MDOC file mapped for this TS.",
            )
        prepared_path = Path(mapped)
        if prepared_path.exists():
            return ResolvedFileRecord(
                role=role,
                dataset_name=dataset.dataset_name,
                ts_name=ts_name,
                path=str(prepared_path),
                source="automatic",
                note="Prepared unified MDOC file selected automatically.",
            )
        return ResolvedFileRecord(
            role=role,
            dataset_name=dataset.dataset_name,
            ts_name=ts_name,
            path=str(prepared_path),
            source="missing",
            note="Prepared unified MDOC file is missing.",
        )

    config = file_role_config(project, role)
    candidates = _matching_candidates(dataset, ts_name, config)
    if not candidates:
        return ResolvedFileRecord(
            role=role,
            dataset_name=dataset.dataset_name,
            ts_name=ts_name,
            source="missing",
            note="No matching files found.",
        )
    if config.selection_mode == "newest":
        selected = max(candidates, key=_path_mtime)
        return ResolvedFileRecord(
            role=role,
            dataset_name=dataset.dataset_name,
            ts_name=ts_name,
            path=str(selected),
            source="automatic",
            note="Newest matching file selected automatically." if len(candidates) > 1 else "",
        )
    if len(candidates) > 1:
        return ResolvedFileRecord(
            role=role,
            dataset_name=dataset.dataset_name,
            ts_name=ts_name,
            source="ambiguous",
            note=f"{len(candidates)} matching files found.",
        )
    return ResolvedFileRecord(
        role=role,
        dataset_name=dataset.dataset_name,
        ts_name=ts_name,
        path=str(candidates[0]),
        source="automatic",
    )


def resolve_dataset_role_map(
    project: ProjectData,
    dataset: DatasetRecord,
    role: str,
) -> list[ResolvedFileRecord]:
    return [resolve_dataset_file(project, dataset, ts_name, role) for ts_name in dataset_ts_names(dataset)]
