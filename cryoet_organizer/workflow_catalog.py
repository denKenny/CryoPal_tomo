from __future__ import annotations

import uuid
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any

from cryoet_organizer.custom_jobs import CustomJobDefinition, get_project_custom_jobs
from cryoet_organizer.executables import (
    CRYOLITHE_EXECUTABLE,
    MEMBRAIN_EXECUTABLE,
    MTOOLS_EXECUTABLE,
    PYTOM_EXTRACT_EXECUTABLE,
    PYTOM_MATCH_EXECUTABLE,
    SLABIFY_EXECUTABLE,
    WARPTOOLS_EXECUTABLE,
    resolve_executable_command,
)
from cryoet_organizer.job_defaults import (
    JobDefaultDefinition,
    JobDefaultField,
    build_job_default_registry,
    effective_job_default_definition,
)
from cryoet_organizer.mtools_catalog import m_jobs_by_group
from cryoet_organizer.project import ProjectData


@dataclass(frozen=True)
class WorkflowCatalogJob:
    catalog_id: str
    namespace: str
    group: str
    job_key: str
    title: str
    processing_tab: str
    owner_kind: str
    owner_name: str
    fields: tuple[JobDefaultField, ...]
    command_template: str


def build_workflow_job_catalog(project: ProjectData) -> list[WorkflowCatalogJob]:
    jobs: list[WorkflowCatalogJob] = []
    for definition in build_job_default_registry():
        if definition.namespace == "Project Overview":
            continue
        effective = effective_job_default_definition(project, definition)
        jobs.append(_catalog_job_from_default(project, effective))
    for custom_job in get_project_custom_jobs(project):
        jobs.append(_catalog_job_from_custom(project, custom_job))
    return sorted(jobs, key=lambda job: (job.processing_tab.casefold(), job.group.casefold(), job.title.casefold()))


def workflow_job_from_catalog(project: ProjectData, catalog_job: WorkflowCatalogJob) -> dict[str, Any]:
    parameters = {field.key: field.default_value for field in catalog_job.fields if field.default_value}
    environment = parameters.get("execution_environment", "")
    entry = {
        "timestamp": "",
        "action": "scheduled",
        "group": catalog_job.group,
        "job_name": catalog_job.job_key,
        "command": command_from_catalog_job(catalog_job, parameters),
        "processing_tab": catalog_job.processing_tab,
        "dataset_name": catalog_job.owner_name,
        "execution_mode": "local",
        "slurm_profile": "",
        "environment_title": environment if environment and environment != "None" else "",
        "slurm_job_id": "",
        "slurm_script_path": "",
        "parameters": parameters,
        "artifacts": {
            "workflow_catalog_id": catalog_job.catalog_id,
            "workflow_catalog_namespace": catalog_job.namespace,
            "workflow_catalog_group": catalog_job.group,
            "workflow_catalog_job_key": catalog_job.job_key,
        },
    }
    return {
        "workflow_job_id": uuid.uuid4().hex,
        "owner_kind": catalog_job.owner_kind,
        "owner_name": catalog_job.owner_name,
        "entry": entry,
    }


def fields_for_workflow_job(
    project: ProjectData,
    job: dict[str, Any],
    catalog_jobs: Sequence[WorkflowCatalogJob] | None = None,
) -> tuple[JobDefaultField, ...]:
    catalog_job = _catalog_job_for_workflow_job(project, job, catalog_jobs)
    if catalog_job is not None:
        return catalog_job.fields
    entry = job.get("entry", {}) if isinstance(job.get("entry"), dict) else {}
    parameters = entry.get("parameters", {}) if isinstance(entry.get("parameters"), dict) else {}
    return tuple(
        JobDefaultField(
            key=str(key),
            label=str(key),
            widget=_infer_widget_from_key(str(key)),
            default_value=str(value),
            parameter_name=str(key) if str(key).startswith("-") else "",
        )
        for key, value in parameters.items()
    )


def rebuild_workflow_job_command(
    project: ProjectData,
    job: dict[str, Any],
    parameters: dict[str, str],
    fallback_command: str,
    catalog_jobs: Sequence[WorkflowCatalogJob] | None = None,
) -> str:
    catalog_job = _catalog_job_for_workflow_job(project, job, catalog_jobs)
    if catalog_job is not None:
        return command_from_catalog_job(catalog_job, parameters)
    return fallback_command


def _catalog_job_for_workflow_job(
    project: ProjectData,
    job: dict[str, Any],
    catalog_jobs: Sequence[WorkflowCatalogJob] | None = None,
) -> WorkflowCatalogJob | None:
    entry = job.get("entry", {}) if isinstance(job.get("entry"), dict) else {}
    artifacts = entry.get("artifacts", {}) if isinstance(entry.get("artifacts"), dict) else {}
    namespace = artifacts.get("workflow_catalog_namespace") or _namespace_from_processing_tab(str(entry.get("processing_tab", "")))
    group = artifacts.get("workflow_catalog_group") or str(entry.get("group", ""))
    job_key = artifacts.get("workflow_catalog_job_key") or str(entry.get("job_name", ""))
    for catalog_job in catalog_jobs if catalog_jobs is not None else build_workflow_job_catalog(project):
        if catalog_job.namespace == namespace and catalog_job.group == group and catalog_job.job_key == job_key:
            return catalog_job
    return None


def command_from_catalog_job(catalog_job: WorkflowCatalogJob, parameters: dict[str, str]) -> str:
    parts = [catalog_job.command_template.strip()]
    for field in catalog_job.fields:
        key = field.key
        if key == "execution_environment":
            continue
        value = str(parameters.get(key, "")).strip()
        parameter = field.parameter_name.strip()
        if field.widget == "bool":
            if value.casefold() in {"1", "true", "yes", "on"} and parameter:
                parts.append(parameter)
            continue
        if value and parameter:
            parts.extend([parameter, value])
        elif value and key.startswith("-"):
            parts.extend([key, value])
    return " ".join(part for part in parts if part)


def owner_options_for_kind(project: ProjectData, owner_kind: str) -> list[str]:
    if owner_kind == "m_population":
        return [population.name for population in project.m_populations if population.name]
    return [dataset.dataset_name for dataset in project.datasets if dataset.dataset_name]


def default_owner_for_processing_tab(project: ProjectData, processing_tab: str) -> tuple[str, str]:
    if processing_tab == "Processing: M":
        names = owner_options_for_kind(project, "m_population")
        return "m_population", names[0] if names else ""
    names = owner_options_for_kind(project, "dataset")
    return "dataset", names[0] if names else ""


def _catalog_job_from_default(project: ProjectData, definition: JobDefaultDefinition) -> WorkflowCatalogJob:
    processing_tab = _processing_tab_from_namespace(definition.namespace)
    owner_kind, owner_name = default_owner_for_processing_tab(project, processing_tab)
    return WorkflowCatalogJob(
        catalog_id=f"default::{definition.namespace}/{definition.group}/{definition.job_key}",
        namespace=definition.namespace,
        group=definition.group,
        job_key=definition.job_key,
        title=definition.title,
        processing_tab=processing_tab,
        owner_kind=owner_kind,
        owner_name=owner_name,
        fields=definition.fields,
        command_template=_command_template_for_default(project, definition),
    )


def _catalog_job_from_custom(project: ProjectData, job: CustomJobDefinition) -> WorkflowCatalogJob:
    owner_kind, owner_name = default_owner_for_processing_tab(project, "Processing: Custom jobs")
    fields = tuple(
        [JobDefaultField("execution_environment", "Default local environment", "environment", job.environment_title or "None")]
        + [
            JobDefaultField(
                key=parameter.key or parameter.flag or parameter.label,
                label=parameter.label or parameter.key or parameter.flag,
                widget=parameter.widget,
                default_value=parameter.default,
                parameter_name=parameter.flag,
            )
            for parameter in job.parameters
        ]
    )
    return WorkflowCatalogJob(
        catalog_id=f"custom::{job.name}",
        namespace="Custom",
        group="Custom jobs",
        job_key=job.name,
        title=job.name,
        processing_tab="Processing: Custom jobs",
        owner_kind=owner_kind,
        owner_name=owner_name,
        fields=fields,
        command_template=job.command_template.strip() or job.name,
    )


def _command_template_for_default(project: ProjectData, definition: JobDefaultDefinition) -> str:
    if definition.namespace == "Processing":
        return f"{resolve_executable_command(project, WARPTOOLS_EXECUTABLE)} {definition.job_key}"
    if definition.namespace == "Processing: M":
        executable = _mtools_executable_for_job(definition.group, definition.job_key)
        parts = [resolve_executable_command(project, executable)]
        if definition.job_key != executable:
            parts.append(definition.job_key)
        return " ".join(parts)
    if definition.namespace == "Tomograms":
        executable = {
            "cryolithe_denoising": CRYOLITHE_EXECUTABLE,
            "pytom_template_matching": PYTOM_MATCH_EXECUTABLE,
            "pytom_extract_coordinates": PYTOM_EXTRACT_EXECUTABLE,
            "slabify_mask_creation": SLABIFY_EXECUTABLE,
            "membrain_segmentation": MEMBRAIN_EXECUTABLE,
        }.get(definition.job_key, definition.job_key)
        command = resolve_executable_command(project, executable)
        if definition.job_key == "membrain_segmentation":
            return f"{command} segment"
        return command
    if definition.namespace == "Particles" and definition.job_key == "ts_export_particles":
        return f"{resolve_executable_command(project, WARPTOOLS_EXECUTABLE)} ts_export_particles"
    return f"cryopal_internal {definition.job_key}"


def _mtools_executable_for_job(group: str, job_key: str) -> str:
    for command in m_jobs_by_group().get(group, ()):
        if command.command == job_key:
            return command.executable
    return MTOOLS_EXECUTABLE


def _processing_tab_from_namespace(namespace: str) -> str:
    if namespace == "Processing":
        return "Processing: WARP"
    if namespace == "Processing: M":
        return "Processing: M"
    if namespace == "Tomograms":
        return "Processing: TS jobs"
    if namespace == "Particles":
        return "Processing: Particle jobs"
    return namespace


def _namespace_from_processing_tab(processing_tab: str) -> str:
    if processing_tab == "Processing: WARP":
        return "Processing"
    if processing_tab == "Processing: M":
        return "Processing: M"
    if processing_tab == "Processing: TS jobs":
        return "Tomograms"
    if processing_tab == "Processing: Particle jobs":
        return "Particles"
    if processing_tab == "Processing: Custom jobs":
        return "Custom"
    return processing_tab


def _infer_widget_from_key(key: str) -> str:
    lower = key.casefold()
    if "path" in lower or "folder" in lower or "directory" in lower or lower.endswith("_file"):
        return "path"
    if lower.startswith("do_") or lower.startswith("use_") or lower.startswith("write_"):
        return "bool"
    return "text"
