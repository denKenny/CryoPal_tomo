from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryoet_organizer.project import SETTINGS_SUFFIX, ProjectData, ProjectState
from cryoet_organizer.file_resolver import (
    essential_file_roles,
    file_role_config,
    set_file_role_config,
)
from cryoet_organizer.job_catalog import CatalogField, CatalogJob
from cryoet_organizer.mtools_catalog import MToolFlag, m_jobs_by_group
from cryoet_organizer.particles_catalog import PARTICLE_JOBS, export_particles_warp_job
from cryoet_organizer.tomograms_catalog import TOMOGRAM_JOBS
from cryoet_organizer.warptools_catalog import WarpToolFlag, jobs_by_group


DEFAULTS_METADATA_KEY = "job_default_overrides"


@dataclass(frozen=True)
class JobDefaultField:
    key: str
    label: str
    widget: str
    default_value: str
    description: str = ""
    options: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class JobDefaultDefinition:
    namespace: str
    group: str
    job_key: str
    title: str
    fields: tuple[JobDefaultField, ...]


def _warp_flag_to_field(flag: WarpToolFlag) -> JobDefaultField:
    return JobDefaultField(
        key=flag.name,
        label=flag.name,
        widget=flag.widget,
        default_value=flag.default_value,
        description=flag.description,
    )


def _mtool_flag_to_field(flag: MToolFlag) -> JobDefaultField:
    return JobDefaultField(
        key=flag.name,
        label=flag.name,
        widget=flag.widget,
        default_value=flag.default_value,
        description=flag.description,
    )


def _catalog_field_to_default(field: CatalogField) -> JobDefaultField:
    return JobDefaultField(
        key=field.key,
        label=field.label,
        widget=field.widget,
        default_value=field.default_value,
        description=field.description,
        options=field.options,
    )


def _catalog_job_to_default(job: CatalogJob) -> JobDefaultDefinition:
    return JobDefaultDefinition(
        namespace=job.namespace,
        group=job.group,
        job_key=job.job_key,
        title=job.title,
        fields=tuple(_catalog_field_to_default(field) for field in job.fields),
    )


def _execution_environment_field() -> JobDefaultField:
    return JobDefaultField(
        key="execution_environment",
        label="Default local environment",
        widget="environment",
        default_value="None",
        description="Optional environment to activate before running this job locally.",
    )


def build_job_default_registry() -> list[JobDefaultDefinition]:
    registry: list[JobDefaultDefinition] = []

    registry.append(
        JobDefaultDefinition(
            namespace="Project Overview",
            group="Add dataset for processing",
            job_key="add_dataset_for_processing",
            title="Add dataset for processing",
            fields=(
                JobDefaultField("sample", "Sample", "text", ""),
                JobDefaultField("comment", "Comment", "text", ""),
                JobDefaultField("raw_frames_folder", "Raw frames folder", "path", ""),
                JobDefaultField("mdocs_folder", "Mdocs folder", "path", ""),
                JobDefaultField("gain_file", "Gain file (optional)", "path", ""),
                JobDefaultField("processing_folder", "Processing folder", "path", ""),
                JobDefaultField("pixel_size", "Pixelsize", "text", ""),
                JobDefaultField("exposure", "Exposure", "text", ""),
                JobDefaultField("tomogram_x", "Tomogram X", "text", ""),
                JobDefaultField("tomogram_y", "Tomogram Y", "text", ""),
                JobDefaultField("tomogram_z", "Tomogram Z", "text", ""),
                JobDefaultField("unify_mdoc_names", "Unify mdoc names", "bool", "true"),
                JobDefaultField("ignore_override_mdocs", "Ignore override.mdoc", "bool", ""),
                JobDefaultField("ignore_custom_mdocs", "Ignore custom.mdoc", "bool", ""),
                JobDefaultField("ignore_custom_mdocs_pattern", "Ignore custom pattern", "text", ""),
            ),
        )
    )
    registry.append(
        JobDefaultDefinition(
            namespace="Project Overview",
            group="Import already processed dataset",
            job_key="import_processed_dataset",
            title="Import already processed dataset",
            fields=(
                JobDefaultField("sample", "Sample", "text", ""),
                JobDefaultField("comment", "Comment", "text", ""),
                JobDefaultField("frameseries_settings_file", "Frameseries.settings file", "path", ""),
                JobDefaultField("tiltseries_settings_file", "Tiltseries.settings file", "path", ""),
                JobDefaultField("raw_frames_folder", "Raw frames folder", "path", ""),
                JobDefaultField("gain_file", "Gain file (optional)", "path", ""),
                JobDefaultField("processing_folder", "Processing folder", "path", ""),
                JobDefaultField("pixel_size", "Pixelsize", "text", ""),
                JobDefaultField("exposure", "Exposure", "text", ""),
                JobDefaultField("tomogram_x", "Tomogram X", "text", ""),
                JobDefaultField("tomogram_y", "Tomogram Y", "text", ""),
                JobDefaultField("tomogram_z", "Tomogram Z", "text", ""),
                JobDefaultField("tomostar_folder", "Tomostar folder", "path", ""),
                JobDefaultField("frameseries_processing_folder", "Frameseries processing folder", "path", ""),
                JobDefaultField("tiltseries_processing_folder", "Tiltseries processing folder", "path", ""),
            ),
        )
    )

    for group, jobs in jobs_by_group().items():
        for job in jobs:
            fields = []
            for flag in job.flags:
                if job.command == "create_settings" and group == "Frame series" and flag.name == "--tomo_dimensions":
                    continue
                fields.append(_warp_flag_to_field(flag))
            registry.append(
                JobDefaultDefinition(
                    namespace="Processing",
                    group=group,
                    job_key=job.command,
                    title=job.command,
                    fields=(_execution_environment_field(), *tuple(fields)),
                )
            )

    for group, jobs in m_jobs_by_group().items():
        for job in jobs:
            registry.append(
                JobDefaultDefinition(
                    namespace="Processing: M",
                    group=group,
                    job_key=job.command,
                    title=job.command,
                    fields=(
                        _execution_environment_field(),
                        *tuple(_mtool_flag_to_field(flag) for flag in job.flags),
                    ),
                )
            )

    export_job = export_particles_warp_job()
    registry.append(
        JobDefaultDefinition(
            namespace="Particles",
            group="Export particles",
            job_key="ts_export_particles",
            title="Export particles",
            fields=(
                _execution_environment_field(),
                *tuple(
                    _warp_flag_to_field(flag)
                    for flag in export_job.flags
                    if flag.name != "--settings"
                ),
            ),
        )
    )
    for job in PARTICLE_JOBS[1:]:
        registry.append(_catalog_job_to_default(job))
    for job in TOMOGRAM_JOBS:
        registry.append(
            JobDefaultDefinition(
                namespace=job.namespace,
                group=job.group,
                job_key=job.job_key,
                title=job.title,
                fields=(
                    _execution_environment_field(),
                    *tuple(_catalog_field_to_default(field) for field in job.fields),
                ),
            )
        )
    return registry


def registry_lookup() -> dict[tuple[str, str, str], JobDefaultDefinition]:
    return {
        (item.namespace, item.group, item.job_key): item
        for item in build_job_default_registry()
    }


def get_project_job_default_overrides(project: ProjectData) -> dict[str, dict[str, dict[str, str]]]:
    payload = project.state.job_default_overrides
    cleaned: dict[str, dict[str, dict[str, str]]] = {}
    for job_path, fields in payload.items():
        if not isinstance(job_path, str) or not isinstance(fields, dict):
            continue
        cleaned_fields: dict[str, dict[str, str]] = {}
        for field_key, settings in fields.items():
            if not isinstance(field_key, str) or not isinstance(settings, dict):
                continue
            enabled = settings.get("enabled", False)
            value = settings.get("value", "")
            if enabled:
                cleaned_fields[field_key] = {
                    "enabled": "true",
                    "value": "" if value is None else str(value),
                }
        if cleaned_fields:
            cleaned[job_path] = cleaned_fields
    return cleaned


def set_project_job_default_overrides(
    project: ProjectData,
    overrides: dict[str, dict[str, dict[str, str]]],
) -> None:
    project.state.job_default_overrides = deepcopy(overrides)


def job_override_key(namespace: str, group: str, job_key: str) -> str:
    return f"{namespace}/{group}/{job_key}"


def resolve_job_default(
    project: ProjectData,
    namespace: str,
    group: str,
    job_key: str,
    field_key: str,
    base_value: str,
) -> str:
    overrides = get_project_job_default_overrides(project)
    job_settings = overrides.get(job_override_key(namespace, group, job_key), {})
    field_settings = job_settings.get(field_key)
    if not field_settings:
        return base_value
    return field_settings.get("value", "")


def export_job_defaults(path: str | Path, overrides: dict[str, dict[str, dict[str, str]]]) -> Path:
    export_path = Path(path)
    if not str(export_path).endswith(SETTINGS_SUFFIX):
        export_path = export_path.with_name(f"{export_path.name}{SETTINGS_SUFFIX}")
    project = ProjectData()
    project.state.job_default_overrides = deepcopy(overrides)
    payload = {
        "version": 1,
        "job_default_overrides": overrides,
        "file_registry_patterns": {
            role: file_role_config(project, role).to_dict()
            for role in essential_file_roles()
        },
    }
    export_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return export_path


def import_job_defaults(path: str | Path) -> dict[str, dict[str, dict[str, str]]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    overrides = payload.get("job_default_overrides", {})
    if not isinstance(overrides, dict):
        return {}
    project = ProjectData()
    project.state.job_default_overrides = deepcopy(overrides)
    return get_project_job_default_overrides(project)


def export_settings_payload(
    project: ProjectData,
    overrides: dict[str, dict[str, dict[str, str]]],
) -> dict:
    return {
        "version": 1,
        "job_default_overrides": overrides,
        "file_registry_patterns": {
            role: file_role_config(project, role).to_dict()
            for role in essential_file_roles()
        },
    }


def import_settings_payload(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def imported_file_registry_patterns(payload: dict) -> dict[str, JobDefaultField | object]:
    patterns = payload.get("file_registry_patterns", {})
    if not isinstance(patterns, dict):
        return {}
    imported: dict[str, object] = {}
    for role in essential_file_roles():
        role_payload = patterns.get(role)
        if isinstance(role_payload, dict):
            imported[role] = file_role_config(
                ProjectData(
                    state=ProjectState(
                        file_registry_patterns={role: role_payload},
                    )
                ),
                role,
            )
    return imported


def apply_imported_file_registry_patterns(project: ProjectData, payload: dict) -> None:
    for role, config in imported_file_registry_patterns(payload).items():
        set_file_role_config(project, role, config)
