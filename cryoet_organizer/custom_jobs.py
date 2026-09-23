from __future__ import annotations

import json
import re
import shlex
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cryoet_organizer.project import ProjectData


CUSTOM_JOBS_METADATA_KEY = "custom_job_types"
CUSTOM_JOBS_SUFFIX = ".cryopal.custom_jobs.json"


def render_custom_context(template: str, context: dict[str, str]) -> str:
    """Insert application-owned context as shell arguments; leave user flags untouched."""
    keys = {"dataset_name", "ts_name", "input_stem", "processing_directory",
            "population_name", "population_file", "population_directory", *context}
    pattern = r"(?<![$\\])\{(" + "|".join(re.escape(key) for key in sorted(keys)) + r")\}"

    def replace(match):
        key = match.group(1)
        value = context.get(key, "")
        if not value:
            raise ValueError(f"Missing execution context for {{{key}}}.")
        return shlex.quote(value)

    return re.sub(pattern, replace, template)


@dataclass
class CustomJobParameter:
    key: str
    label: str
    flag: str = ""
    widget: str = "text"
    default: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: dict) -> "CustomJobParameter":
        return cls(
            key=str(payload.get("key", "")),
            label=str(payload.get("label", payload.get("description", ""))),
            flag=str(payload.get("flag", "")),
            widget=str(payload.get("widget", payload.get("input_type", "text"))),
            default=str(payload.get("default", "")),
            extra={str(key): str(value) for key, value in payload.get("extra", {}).items()},
        )


@dataclass
class CustomJobDefinition:
    name: str
    description: str = ""
    command_template: str = ""
    environment_title: str = "None"
    parameters: list[CustomJobParameter] = field(default_factory=list)
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    target_tab: str = ""
    target_group: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> "CustomJobDefinition":
        return cls(
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            command_template=str(payload.get("command_template", "")),
            environment_title=str(payload.get("environment_title", "None") or "None"),
            parameters=[CustomJobParameter.from_dict(item) for item in payload.get("parameters", [])],
            job_id=str(payload.get("job_id") or uuid.uuid5(uuid.NAMESPACE_URL, "cryopal:custom:" + str(payload.get("name", ""))).hex),
            target_tab=str(payload.get("target_tab", "")),
            target_group=str(payload.get("target_group", "")),
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["parameters"] = [asdict(item) for item in self.parameters]
        return payload


def get_project_custom_jobs(project: ProjectData) -> list[CustomJobDefinition]:
    payload = project.state.custom_job_types
    return [CustomJobDefinition.from_dict(item) for item in payload if isinstance(item, dict)]


def set_project_custom_jobs(project: ProjectData, jobs: list[CustomJobDefinition]) -> None:
    seen = set()
    for job in jobs:
        if job.job_id in seen:
            job.job_id = uuid.uuid4().hex
        seen.add(job.job_id)
    project.state.custom_job_types = [job.to_dict() for job in jobs]


def _ensure_suffix(path: str | Path) -> Path:
    path_obj = Path(path)
    if str(path_obj).endswith(CUSTOM_JOBS_SUFFIX):
        return path_obj
    return path_obj.with_name(f"{path_obj.name}{CUSTOM_JOBS_SUFFIX}")


def export_custom_jobs(path: str | Path, jobs: list[CustomJobDefinition]) -> Path:
    target = _ensure_suffix(path)
    target.write_text(
        json.dumps([job.to_dict() for job in jobs], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def import_custom_jobs(path: str | Path) -> list[CustomJobDefinition]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Custom job file must contain a list of job definitions.")
    return [CustomJobDefinition.from_dict(item) for item in payload if isinstance(item, dict)]


def merge_custom_jobs(
    existing: list[CustomJobDefinition],
    imported: list[CustomJobDefinition],
) -> list[CustomJobDefinition]:
    merged = list(existing)
    existing_names = {job.name.casefold() for job in existing}
    for job in imported:
        candidate = job
        if candidate.name.casefold() in existing_names:
            suffix = 2
            base_name = candidate.name
            while f"{base_name} ({suffix})".casefold() in existing_names:
                suffix += 1
            candidate = CustomJobDefinition(
                name=f"{base_name} ({suffix})",
                description=job.description,
                command_template=job.command_template,
                environment_title=job.environment_title,
                parameters=list(job.parameters),
                target_tab=job.target_tab,
                target_group=job.target_group,
            )
        merged.append(candidate)
        existing_names.add(candidate.name.casefold())
    return merged


CUSTOM_JOB_TARGETS = {
    "": "None", "processing": "Processing: WARP", "processing_m": "Processing: M",
    "tomograms": "Processing: TS jobs", "particles": "Processing: Particle jobs",
}


def custom_job_groups(target_tab: str) -> dict[str, str]:
    from cryoet_organizer.warptools_catalog import GROUPS
    from cryoet_organizer.mtools_catalog import M_GROUPS
    labels = GROUPS if target_tab == "processing" else M_GROUPS if target_tab == "processing_m" else ()
    return {label.lower().replace(" ", "_"): label for label in labels}


def custom_job_assignment_error(target_tab: str, target_group: str) -> str:
    if target_tab not in CUSTOM_JOB_TARGETS:
        return "Unknown Processing tab. Choose a supported tab or None."
    groups = custom_job_groups(target_tab)
    if groups and target_group not in groups:
        return "Please select a valid job group for the chosen Processing tab."
    if not groups and target_group:
        return "This Processing tab does not have job groups."
    return ""


def assigned_custom_jobs(project: ProjectData, target_tab: str, group: str = "") -> list[CustomJobDefinition]:
    return [job for job in get_project_custom_jobs(project)
            if job.target_tab == target_tab and not custom_job_assignment_error(job.target_tab, job.target_group)
            and custom_job_groups(target_tab).get(job.target_group, "") == group]
