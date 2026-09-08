from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import uuid
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_SUFFIX = ".cryopal.json"
SETTINGS_SUFFIX = ".cryopal.settings"
TS_NAME_DELIMITERS = "_-. "
PROJECT_SCHEMA_VERSION = 8
PROJECT_BACKUP_COUNT = 3


class ProjectFormatError(ValueError):
    """Raised when a project cannot be interpreted without risking data loss."""


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ProjectFormatError(f"Project JSON contains non-standard numeric value {value!r}.")


def _strict_bool(value: Any, *, field_name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ProjectFormatError(f"Project field '{field_name}' must be a boolean value.")


def _finite_float(value: Any, *, field_name: str, minimum: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ProjectFormatError(f"Project field '{field_name}' must be a number.") from exc
    if not math.isfinite(parsed) or parsed < minimum:
        raise ProjectFormatError(f"Project field '{field_name}' must be finite and at least {minimum}.")
    return parsed


def _nonnegative_integer(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ProjectFormatError(f"Project field '{field_name}' must be a non-negative integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectFormatError(f"Project field '{field_name}' must be a non-negative integer.") from exc
    if parsed < 0 or (isinstance(value, float) and not value.is_integer()):
        raise ProjectFormatError(f"Project field '{field_name}' must be a non-negative integer.")
    return parsed


def _string_keyed_dict(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items()}


def _string_keyed_list(payload: Any) -> list[str]:
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload]


def _string_string_dict(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    return {str(key): "" if value is None else str(value) for key, value in payload.items()}


def _string_string_dict_list(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, list):
        return []
    return [_string_string_dict(item) for item in payload if isinstance(item, dict)]


def _triple_string_dict(payload: Any) -> dict[str, dict[str, dict[str, str]]]:
    if not isinstance(payload, dict):
        return {}
    cleaned: dict[str, dict[str, dict[str, str]]] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            continue
        inner: dict[str, dict[str, str]] = {}
        for inner_key, inner_value in value.items():
            if not isinstance(inner_value, dict):
                continue
            inner[str(inner_key)] = _string_string_dict(inner_value)
        cleaned[str(key)] = inner
    return cleaned


def _double_any_dict(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}
    cleaned: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if isinstance(value, dict):
            cleaned[str(key)] = deepcopy(dict(value))
    return cleaned


@dataclass
class ProjectState:
    preferences: dict[str, str] = field(default_factory=dict)
    appearance: dict[str, str] = field(default_factory=dict)
    viewer_defaults: dict[str, Any] | None = None
    slurm_profiles: list[dict[str, str]] = field(default_factory=list)
    environments: list[dict[str, str]] = field(default_factory=list)
    executable_overrides: dict[str, str] = field(default_factory=dict)
    job_default_overrides: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    file_registry_patterns: dict[str, dict[str, Any]] = field(default_factory=dict)
    file_registry_role_order: list[str] = field(default_factory=list)
    file_registry_overrides: dict[str, dict[str, dict[str, str]]] = field(default_factory=dict)
    custom_job_types: list[dict[str, Any]] = field(default_factory=list)
    shortcuts: list[dict[str, str]] = field(default_factory=list)
    relion_project_default: dict[str, str] = field(default_factory=dict)
    relion_projects: list[dict[str, str]] = field(default_factory=list)
    tomograms_selection: list[dict[str, str]] = field(default_factory=list)
    workflows: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict | None) -> "ProjectState":
        payload = _string_keyed_dict(payload)
        custom_jobs_payload = payload.get("custom_job_types", [])
        custom_jobs = [
            deepcopy(dict(item))
            for item in custom_jobs_payload
            if isinstance(item, dict)
        ]
        return cls(
            preferences=_string_string_dict(payload.get("preferences", {})),
            appearance=_string_string_dict(payload.get("appearance", {})),
            viewer_defaults=deepcopy(dict(payload.get("viewer_defaults", {}))) if isinstance(payload.get("viewer_defaults"), dict) else None,
            slurm_profiles=_string_string_dict_list(payload.get("slurm_profiles", [])),
            environments=_string_string_dict_list(payload.get("environments", [])),
            executable_overrides=_string_string_dict(payload.get("executable_overrides", {})),
            job_default_overrides=_triple_string_dict(payload.get("job_default_overrides", {})),
            file_registry_patterns=_double_any_dict(payload.get("file_registry_patterns", {})),
            file_registry_role_order=_string_keyed_list(payload.get("file_registry_role_order", [])),
            file_registry_overrides=_triple_string_dict(payload.get("file_registry_overrides", {})),
            custom_job_types=custom_jobs,
            shortcuts=_string_string_dict_list(payload.get("shortcuts", [])),
            relion_project_default=_string_string_dict(payload.get("relion_project_default", {})),
            relion_projects=_string_string_dict_list(payload.get("relion_projects", [])),
            tomograms_selection=_string_string_dict_list(payload.get("tomograms_selection", [])),
            workflows=[
                deepcopy(dict(item))
                for item in payload.get("workflows", [])
                if isinstance(item, dict)
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class JobHistoryEntry:
    timestamp: str
    action: str
    group: str
    job_name: str
    command: str
    processing_tab: str = ""
    dataset_name: str = ""
    execution_mode: str = "local"
    slurm_profile: str = ""
    environment_title: str = ""
    slurm_job_id: str = ""
    slurm_script_path: str = ""
    parameters: dict[str, str] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "unknown"
    submitted_at: str = ""
    started_at: str = ""
    finished_at: str = ""
    exit_code: int | None = None
    failure_reason: str = ""
    working_directory: str = ""
    host: str = ""
    app_version: str = ""
    tool_version: str = ""
    environment_fingerprint: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> "JobHistoryEntry":
        return cls(
            entry_id=str(payload.get("entry_id") or uuid.uuid4().hex),
            timestamp=payload.get("timestamp")
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
            action=payload.get("action", ""),
            group=payload.get("group", ""),
            job_name=payload.get("job_name", ""),
            command=payload.get("command", ""),
            processing_tab=payload.get("processing_tab", ""),
            dataset_name=payload.get("dataset_name", ""),
            execution_mode=payload.get("execution_mode", "local"),
            slurm_profile=payload.get("slurm_profile", ""),
            environment_title=payload.get("environment_title", ""),
            slurm_job_id=payload.get("slurm_job_id", ""),
            slurm_script_path=payload.get("slurm_script_path", ""),
            parameters={str(key): str(value) for key, value in payload.get("parameters", {}).items()},
            artifacts=deepcopy(dict(payload.get("artifacts", {}))) if isinstance(payload.get("artifacts"), dict) else {},
            status=str(payload.get("status", "unknown")),
            submitted_at=str(payload.get("submitted_at", "")),
            started_at=str(payload.get("started_at", "")),
            finished_at=str(payload.get("finished_at", "")),
            exit_code=(int(payload["exit_code"]) if payload.get("exit_code") is not None else None),
            failure_reason=str(payload.get("failure_reason", "")),
            working_directory=str(payload.get("working_directory", "")),
            host=str(payload.get("host", "")),
            app_version=str(payload.get("app_version", "")),
            tool_version=str(payload.get("tool_version", "")),
            environment_fingerprint=str(payload.get("environment_fingerprint", "")),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ThumbnailRecord:
    image_path: str
    ts_name: str
    mrc_path: str = ""
    rating: int = 0
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict) -> "ThumbnailRecord":
        return cls(
            image_path=payload.get("image_path", ""),
            ts_name=payload.get("ts_name", ""),
            mrc_path=payload.get("mrc_path", ""),
            rating=int(payload.get("rating", 0)),
            tags=[str(tag) for tag in payload.get("tags", [])],
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MPopulationRecord:
    name: str
    directory: str = ""
    population_file: str = ""
    comment: str = ""
    species: list[dict[str, str]] = field(default_factory=list)
    sources: list[dict[str, str]] = field(default_factory=list)
    job_history: list[JobHistoryEntry] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @classmethod
    def from_dict(cls, payload: dict) -> "MPopulationRecord":
        return cls(
            name=payload.get("name", ""),
            directory=payload.get("directory", ""),
            population_file=payload.get("population_file", ""),
            comment=payload.get("comment", ""),
            species=_string_string_dict_list(payload.get("species", [])),
            sources=_string_string_dict_list(payload.get("sources", [])),
            job_history=[
                JobHistoryEntry.from_dict(item) for item in payload.get("job_history", [])
            ],
            created_at=payload.get("created_at")
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DatasetRecord:
    dataset_name: str
    sample: str
    pixel_size: float
    exposure: float
    tomogram_x: int
    tomogram_y: int
    tomogram_z: int
    raw_frames_folder: str
    mdocs_folder: str
    mdocs_source_folder: str = ""
    unified_mdoc_names: bool = True
    unified_mdocs_folder: str = ""
    prepared_mdoc_map: dict[str, str] = field(default_factory=dict)
    ignore_override_mdocs: bool = False
    ignore_custom_mdocs: bool = False
    ignore_custom_mdocs_pattern: str = ""
    gain_file: str = ""
    frame_series_settings_file: str = ""
    tilt_series_settings_file: str = ""
    frame_series_processing_folder: str = ""
    tilt_series_processing_folder: str = ""
    tilt_series_data_folder: str = ""
    thumbnail_folder: str = ""
    tomogram_folder: str = ""
    thumbnails: list[ThumbnailRecord] = field(default_factory=list)
    processing_root_folder: str = ""
    processing_folder: str = ""
    comment: str = ""
    job_history: list[JobHistoryEntry] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @classmethod
    def from_dict(cls, payload: dict) -> "DatasetRecord":
        return cls(
            dataset_name=payload.get("dataset_name", ""),
            sample=payload.get("sample", ""),
            comment=payload.get("comment", ""),
            pixel_size=_finite_float(payload.get("pixel_size", 0.0), field_name="datasets[].pixel_size"),
            exposure=_finite_float(payload.get("exposure", 0.0), field_name="datasets[].exposure"),
            tomogram_x=_nonnegative_integer(payload.get("tomogram_x", 0), field_name="datasets[].tomogram_x"),
            tomogram_y=_nonnegative_integer(payload.get("tomogram_y", 0), field_name="datasets[].tomogram_y"),
            tomogram_z=_nonnegative_integer(payload.get("tomogram_z", 0), field_name="datasets[].tomogram_z"),
            raw_frames_folder=payload.get("raw_frames_folder", ""),
            mdocs_folder=payload.get("mdocs_folder", ""),
            mdocs_source_folder=payload.get("mdocs_source_folder", payload.get("mdocs_folder", "")),
            unified_mdoc_names=_strict_bool(
                payload.get("unified_mdoc_names", True),
                field_name="datasets[].unified_mdoc_names",
                default=True,
            ),
            unified_mdocs_folder=payload.get("unified_mdocs_folder", ""),
            prepared_mdoc_map=_string_string_dict(payload.get("prepared_mdoc_map", {})),
            ignore_override_mdocs=_strict_bool(
                payload.get("ignore_override_mdocs", False),
                field_name="datasets[].ignore_override_mdocs",
            ),
            ignore_custom_mdocs=_strict_bool(
                payload.get("ignore_custom_mdocs", False),
                field_name="datasets[].ignore_custom_mdocs",
            ),
            ignore_custom_mdocs_pattern=payload.get("ignore_custom_mdocs_pattern", ""),
            gain_file=payload.get("gain_file", ""),
            frame_series_settings_file=payload.get("frame_series_settings_file", ""),
            tilt_series_settings_file=payload.get("tilt_series_settings_file", ""),
            frame_series_processing_folder=payload.get("frame_series_processing_folder", ""),
            tilt_series_processing_folder=payload.get("tilt_series_processing_folder", ""),
            tilt_series_data_folder=payload.get("tilt_series_data_folder", ""),
            thumbnail_folder=payload.get("thumbnail_folder", ""),
            tomogram_folder=payload.get("tomogram_folder", ""),
            thumbnails=[
                ThumbnailRecord.from_dict(item) for item in payload.get("thumbnails", [])
            ],
            processing_root_folder=payload.get("processing_root_folder", ""),
            processing_folder=payload.get("processing_folder", ""),
            job_history=[
                JobHistoryEntry.from_dict(item) for item in payload.get("job_history", [])
            ],
            created_at=payload.get("created_at")
            or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProjectData:
    name: str = "Untitled Project"
    schema_version: int = PROJECT_SCHEMA_VERSION
    dataset_sort_column: str = "created_at"
    dataset_sort_descending: bool = False
    datasets: list[DatasetRecord] = field(default_factory=list)
    m_populations: list[MPopulationRecord] = field(default_factory=list)
    state: ProjectState = field(default_factory=ProjectState)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict) -> "ProjectData":
        if not isinstance(payload, dict):
            raise ProjectFormatError("CryoPal_tomo project root must be a JSON object.")
        migrated = migrate_project_payload(payload)
        dataset_payload = migrated.get("datasets", [])
        if not isinstance(dataset_payload, list) or not all(isinstance(item, dict) for item in dataset_payload):
            raise ProjectFormatError("Project field 'datasets' must be a list of objects.")
        population_payload = migrated.get("m_populations", [])
        if not isinstance(population_payload, list) or not all(isinstance(item, dict) for item in population_payload):
            raise ProjectFormatError("Project field 'm_populations' must be a list of objects.")
        datasets = [
            DatasetRecord.from_dict(item) for item in dataset_payload
        ]
        assert_unique_dataset_names(datasets)
        return cls(
            name=migrated.get("name", "Untitled Project"),
            schema_version=int(migrated.get("schema_version", PROJECT_SCHEMA_VERSION) or PROJECT_SCHEMA_VERSION),
            dataset_sort_column=migrated.get(
                "dataset_sort_column",
                "dataset_name"
                if migrated.get("dataset_sort_mode") == "alphabetical"
                else "created_at",
            ),
            dataset_sort_descending=_strict_bool(
                migrated.get("dataset_sort_descending", False),
                field_name="dataset_sort_descending",
            ),
            datasets=datasets,
            m_populations=[
                MPopulationRecord.from_dict(item) for item in population_payload
            ],
            state=ProjectState.from_dict(migrated.get("state")),
            metadata=deepcopy(dict(migrated.get("metadata", {}))),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def ensure_project_suffix(path: Path) -> Path:
    if str(path).endswith(PROJECT_SUFFIX):
        return path
    return path.with_name(f"{path.name}{PROJECT_SUFFIX}")


def filtered_mdoc_paths(dataset: DatasetRecord) -> list[Path]:
    source_value = dataset.mdocs_source_folder.strip() or dataset.mdocs_folder.strip()
    if not source_value:
        return []
    source_dir = Path(source_value)
    if not source_dir.exists():
        return []

    custom_pattern = dataset.ignore_custom_mdocs_pattern.strip().casefold()
    files = sorted(
        [path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() == ".mdoc"],
        key=lambda path: path.name.casefold(),
    )
    filtered: list[Path] = []
    for path in files:
        lower_name = path.name.casefold()
        if dataset.ignore_override_mdocs and lower_name.endswith("_override.mdoc"):
            continue
        if dataset.ignore_custom_mdocs and custom_pattern and custom_pattern in lower_name:
            continue
        filtered.append(path)
    return filtered


def _sanitize_dataset_folder_name(dataset_name: str) -> str:
    cleaned = dataset_name.strip().replace("/", "_").replace("\\", "_")
    return cleaned or "dataset"


def normalize_dataset_name(dataset_name: str) -> str:
    return str(dataset_name).strip().casefold()


def duplicate_dataset_names(datasets: list[DatasetRecord]) -> list[str]:
    counts: dict[str, int] = {}
    display_names: dict[str, str] = {}
    duplicates: list[str] = []
    for dataset in datasets:
        key = normalize_dataset_name(dataset.dataset_name)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
        display_names.setdefault(key, dataset.dataset_name.strip() or dataset.dataset_name)
        if counts[key] == 2:
            duplicates.append(display_names[key])
    return sorted(duplicates, key=str.casefold)


def assert_unique_dataset_names(datasets: list[DatasetRecord]) -> None:
    duplicates = duplicate_dataset_names(datasets)
    if duplicates:
        quoted = ", ".join(duplicates)
        raise ValueError(
            "Duplicate dataset names are not supported because CryoPal_tomo uses dataset names "
            f"as stable identifiers across the project. Please rename these datasets: {quoted}"
        )


def prepared_mdoc_map(dataset: DatasetRecord) -> dict[str, str]:
    if dataset.prepared_mdoc_map:
        return {
            str(key): str(value)
            for key, value in dataset.prepared_mdoc_map.items()
            if str(key).strip() and str(value).strip()
        }
    if not dataset.unified_mdoc_names or not dataset.unified_mdocs_folder.strip():
        return {}
    filtered = filtered_mdoc_paths(dataset)
    if not filtered:
        return {}
    width = max(3, len(str(len(filtered))))
    base_name = _sanitize_dataset_folder_name(dataset.dataset_name)
    target_dir = Path(dataset.unified_mdocs_folder)
    mapping: dict[str, str] = {}
    for index, source_path in enumerate(filtered, start=1):
        target_name = f"{base_name}_TS_{index:0{width}d}.mdoc"
        mapping[source_path.stem] = str(target_dir / target_name)
    return mapping


def prepared_mdoc_path_for_ts(dataset: DatasetRecord, ts_name: str) -> str:
    mapping = prepared_mdoc_map(dataset)
    if not mapping:
        return ""
    for key, value in mapping.items():
        if key.casefold() == ts_name.casefold():
            return value
    matched_key = best_matching_ts_name(ts_name, list(mapping.keys()))
    if matched_key:
        return mapping.get(matched_key, "")
    return ""


def prepare_unified_mdocs_directory(dataset: DatasetRecord) -> tuple[str, int, dict[str, str]]:
    processing_dir = Path(dataset.processing_folder)
    processing_dir.mkdir(parents=True, exist_ok=True)
    target_dir = processing_dir / "new_mdoc"
    mdoc_files = filtered_mdoc_paths(dataset)
    if not mdoc_files:
        raise ValueError("No .mdoc files remained after applying the selected ignore filters.")

    stage_dir = Path(tempfile.mkdtemp(prefix=".new_mdoc_stage_", dir=processing_dir))
    backup_dir: Path | None = None
    try:
        width = max(3, len(str(len(mdoc_files))))
        base_name = _sanitize_dataset_folder_name(dataset.dataset_name)
        prepared_map: dict[str, str] = {}
        for index, source_path in enumerate(mdoc_files, start=1):
            target_name = f"{base_name}_TS_{index:0{width}d}.mdoc"
            staged_target = stage_dir / target_name
            shutil.copy2(source_path, staged_target)
            prepared_map[source_path.stem] = str(target_dir / target_name)

        if target_dir.exists():
            backup_dir = processing_dir / f".new_mdoc_backup_{uuid.uuid4().hex}"
            if backup_dir.exists():
                shutil.rmtree(backup_dir, ignore_errors=True)
            target_dir.rename(backup_dir)

        try:
            stage_dir.rename(target_dir)
        except Exception:
            if backup_dir is not None and backup_dir.exists() and not target_dir.exists():
                backup_dir.rename(target_dir)
            raise

        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

        return str(target_dir), len(mdoc_files), prepared_map
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise


def dataset_ts_names(dataset: DatasetRecord) -> list[str]:
    tomostar_folder_value = dataset.tilt_series_data_folder.strip()
    if tomostar_folder_value:
        tomostar_folder = Path(tomostar_folder_value)
        if tomostar_folder.exists():
            tomostar_stems = sorted(
                [item.stem for item in tomostar_folder.glob("*.tomostar") if item.is_file()],
                key=str.casefold,
            )
            if tomostar_stems:
                return tomostar_stems

    filtered_mdocs = filtered_mdoc_paths(dataset)
    if filtered_mdocs:
        return [item.stem for item in filtered_mdocs]

    mdocs_folder_value = dataset.mdocs_folder.strip()
    if not mdocs_folder_value:
        return []
    mdocs_folder = Path(mdocs_folder_value)
    if not mdocs_folder.exists():
        return []
    return sorted(
        [item.stem for item in mdocs_folder.iterdir() if item.is_file() and item.suffix.lower() == ".mdoc"],
        key=str.casefold,
    )


def find_dataset_for_ts_name(project: ProjectData, ts_name: str) -> DatasetRecord | None:
    ts_key = ts_name.casefold()
    for dataset in project.datasets:
        if any(name.casefold() == ts_key for name in dataset_ts_names(dataset)):
            return dataset
    return None


def _boundary_ok(value: str, start: int, end: int) -> bool:
    before_ok = start == 0 or value[start - 1] in TS_NAME_DELIMITERS
    after_ok = end == len(value) or value[end] in TS_NAME_DELIMITERS
    return before_ok and after_ok


def ts_name_match_score(target_stem: str, ts_name: str) -> tuple[int, int] | None:
    target = target_stem.casefold()
    candidate = ts_name.casefold()
    if not target or not candidate:
        return None
    if target == candidate:
        return (4, len(candidate))
    if target.startswith(candidate):
        end = len(candidate)
        if end == len(target) or target[end] in TS_NAME_DELIMITERS:
            return (3, len(candidate))
    start = target.find(candidate)
    while start != -1:
        end = start + len(candidate)
        if _boundary_ok(target, start, end):
            return (2, len(candidate))
        start = target.find(candidate, start + 1)
    return None


def best_matching_ts_name(target_stem: str, ts_names: list[str]) -> str:
    best_name = ""
    best_score: tuple[int, int] | None = None
    for ts_name in ts_names:
        score = ts_name_match_score(target_stem, ts_name)
        if score is None:
            continue
        if best_score is None or score > best_score:
            best_name = ts_name
            best_score = score
    return best_name


def best_matching_path_for_ts(paths: list[Path], ts_name: str, ts_names: list[str] | None = None) -> Path | None:
    top_paths = best_matching_paths_for_ts(paths, ts_name, ts_names)
    if len(top_paths) == 1:
        return top_paths[0]
    return None


def best_matching_paths_for_ts(paths: list[Path], ts_name: str, ts_names: list[str] | None = None) -> list[Path]:
    ts_key = ts_name.casefold()
    exact_stem_matches = [path for path in paths if path.stem.casefold() == ts_key]
    if exact_stem_matches:
        return exact_stem_matches
    known_ts_names = [name for name in (ts_names or []) if str(name).strip()]
    ranked: list[tuple[tuple[int, int], Path]] = []
    for path in paths:
        if known_ts_names:
            best_owner = best_matching_ts_name(path.stem, known_ts_names)
            if best_owner and best_owner.casefold() != ts_key:
                continue
        score = ts_name_match_score(path.stem, ts_name)
        if score is None:
            continue
        ranked.append((score, path))
    if not ranked:
        return []
    ranked.sort(key=lambda item: (item[0][0], item[0][1], -len(item[1].name)), reverse=True)
    top_score = ranked[0][0]
    top_paths = [path for score, path in ranked if score == top_score]
    return top_paths


def load_project(path: str | Path) -> ProjectData:
    project_path = Path(path)
    payload = json.loads(
        project_path.read_text(encoding="utf-8"),
        parse_constant=_reject_nonstandard_json_constant,
    )
    return ProjectData.from_dict(payload)


def save_project(path: str | Path, project: ProjectData) -> Path:
    project_path = ensure_project_suffix(Path(path))
    project.schema_version = PROJECT_SCHEMA_VERSION
    data = json.dumps(project.to_dict(), indent=2, ensure_ascii=False, allow_nan=False)
    project_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=project_path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        _rotate_project_backups(project_path)
        os.replace(tmp_name, project_path)
        try:
            directory_fd = os.open(project_path.parent, os.O_RDONLY)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return project_path


def _backup_path(project_path: Path, generation: int) -> Path:
    return project_path.with_name(f"{project_path.name}.bak{generation}")


def _rotate_project_backups(project_path: Path) -> None:
    if not project_path.exists():
        return
    _backup_path(project_path, PROJECT_BACKUP_COUNT).unlink(missing_ok=True)
    for generation in range(PROJECT_BACKUP_COUNT - 1, 0, -1):
        source = _backup_path(project_path, generation)
        if source.exists():
            os.replace(source, _backup_path(project_path, generation + 1))
    backup_fd, backup_tmp_name = tempfile.mkstemp(dir=project_path.parent, suffix=".backup.tmp")
    os.close(backup_fd)
    backup_tmp = Path(backup_tmp_name)
    try:
        shutil.copy2(project_path, backup_tmp)
        os.replace(backup_tmp, _backup_path(project_path, 1))
    finally:
        backup_tmp.unlink(missing_ok=True)


def migrate_project_payload(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(dict(payload))
    try:
        schema_version = int(migrated.get("schema_version", 1) or 1)
    except (TypeError, ValueError) as exc:
        raise ProjectFormatError("Project schema_version must be an integer.") from exc
    if schema_version > PROJECT_SCHEMA_VERSION:
        raise ProjectFormatError(
            f"This project uses schema version {schema_version}, but this CryoPal_tomo release "
            f"supports up to version {PROJECT_SCHEMA_VERSION}. Open it with a newer release; "
            "the project was not modified."
        )
    if schema_version < 1:
        raise ProjectFormatError("Project schema_version must be at least 1.")
    metadata = migrated.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    migrated["metadata"] = metadata
    state = migrated.get("state")
    if not isinstance(state, dict):
        state = {}
    migrated["state"] = state
    migrated.setdefault("datasets", [])
    migrated.setdefault("m_populations", [])
    migrated.setdefault("dataset_sort_column", "created_at")
    migrated.setdefault("dataset_sort_descending", False)
    if schema_version < 2:
        migrated = _migrate_v1_to_v2(migrated)
        schema_version = 2
    legacy_state_keys = {
        "appearance",
        "slurm_profiles",
        "executable_overrides",
        "job_default_overrides",
        "file_registry_patterns",
        "file_registry_role_order",
        "file_registry_overrides",
        "custom_job_types",
        "shortcuts",
        "relion_project_default",
        "relion_projects",
        "tomograms_selection",
        "preferences",
    }
    if schema_version < 3 or (
        not migrated.get("state")
        and any(key in metadata for key in legacy_state_keys)
    ):
        migrated = _migrate_v2_to_v3(migrated)
        schema_version = 3
    if schema_version < 4:
        migrated = _migrate_v3_to_v4(migrated)
        schema_version = 4
    if schema_version < 5:
        migrated = _migrate_v4_to_v5(migrated)
        schema_version = 5
    if schema_version < 6:
        migrated = _migrate_v5_to_v6(migrated)
        schema_version = 6
    if schema_version < 7:
        migrated = _migrate_v6_to_v7(migrated)
        schema_version = 7
    if schema_version < 8:
        migrated = _migrate_v7_to_v8(migrated)
    migrated["schema_version"] = PROJECT_SCHEMA_VERSION
    return migrated


def _migrate_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(payload)
    metadata = migrated.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        migrated["metadata"] = metadata

    legacy_role_map = {
        "ts_aligned_stack": "aligned_stack",
        "ts_angle_file": "angle_file",
        "ts_tomogram": "tomogram",
    }
    patterns = metadata.get("file_registry_patterns", {})
    if isinstance(patterns, dict):
        normalized_patterns: dict[str, Any] = {}
        for role, config in patterns.items():
            normalized_patterns[legacy_role_map.get(str(role), str(role))] = config
        metadata["file_registry_patterns"] = normalized_patterns

    overrides = metadata.get("file_registry_overrides", {})
    if isinstance(overrides, dict):
        normalized_overrides: dict[str, Any] = {}
        for dataset_name, dataset_payload in overrides.items():
            if not isinstance(dataset_payload, dict):
                continue
            normalized_dataset_payload: dict[str, Any] = {}
            for role, ts_payload in dataset_payload.items():
                normalized_dataset_payload[legacy_role_map.get(str(role), str(role))] = ts_payload
            normalized_overrides[str(dataset_name)] = normalized_dataset_payload
        metadata["file_registry_overrides"] = normalized_overrides

    order = metadata.get("file_registry_role_order", [])
    if isinstance(order, list):
        metadata["file_registry_role_order"] = [
            legacy_role_map.get(str(item), str(item)) for item in order
        ]
    return migrated


def _migrate_v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(payload)
    metadata = migrated.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}
        migrated["metadata"] = metadata
    state = migrated.setdefault("state", {})
    if not isinstance(state, dict):
        state = {}
        migrated["state"] = state

    def move(key: str, default: Any) -> None:
        if key in state:
            return
        value = metadata.pop(key, default)
        if value != default:
            state[key] = value

    move("appearance", {})
    move("slurm_profiles", [])
    move("executable_overrides", {})
    move("job_default_overrides", {})
    move("file_registry_patterns", {})
    move("file_registry_role_order", [])
    move("file_registry_overrides", {})
    move("custom_job_types", [])
    move("shortcuts", [])
    move("relion_project_default", {})
    move("relion_projects", [])
    move("tomograms_selection", [])
    move("preferences", {})
    return migrated


def _migrate_v3_to_v4(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(payload)
    state = migrated.setdefault("state", {})
    if not isinstance(state, dict):
        state = {}
        migrated["state"] = state
    state.setdefault("preferences", {})
    return migrated


def _migrate_v4_to_v5(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(payload)
    state = migrated.setdefault("state", {})
    if not isinstance(state, dict):
        state = {}
        migrated["state"] = state
    state.setdefault("shortcuts", [])
    return migrated


def _migrate_v5_to_v6(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(payload)
    state = migrated.setdefault("state", {})
    if not isinstance(state, dict):
        state = {}
        migrated["state"] = state
    state.setdefault("executable_overrides", {})
    return migrated


def _migrate_v6_to_v7(payload: dict[str, Any]) -> dict[str, Any]:
    migrated = deepcopy(payload)
    state = migrated.setdefault("state", {})
    if not isinstance(state, dict):
        state = {}
        migrated["state"] = state
    state.setdefault("relion_projects", [])
    state.setdefault("relion_project_default", {})
    return migrated


def _migrate_v7_to_v8(payload: dict[str, Any]) -> dict[str, Any]:
    """Schema 8 adds optional execution provenance fields to history entries."""
    return deepcopy(payload)
