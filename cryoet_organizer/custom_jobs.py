from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cryoet_organizer.project import ProjectData


CUSTOM_JOBS_METADATA_KEY = "custom_job_types"
CUSTOM_JOBS_SUFFIX = ".cryopal.custom_jobs.json"


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

    @classmethod
    def from_dict(cls, payload: dict) -> "CustomJobDefinition":
        return cls(
            name=str(payload.get("name", "")),
            description=str(payload.get("description", "")),
            command_template=str(payload.get("command_template", "")),
            environment_title=str(payload.get("environment_title", "None") or "None"),
            parameters=[CustomJobParameter.from_dict(item) for item in payload.get("parameters", [])],
        )

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["parameters"] = [asdict(item) for item in self.parameters]
        return payload


def get_project_custom_jobs(project: ProjectData) -> list[CustomJobDefinition]:
    payload = project.state.custom_job_types
    return [CustomJobDefinition.from_dict(item) for item in payload if isinstance(item, dict)]


def set_project_custom_jobs(project: ProjectData, jobs: list[CustomJobDefinition]) -> None:
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
            )
        merged.append(candidate)
        existing_names.add(candidate.name.casefold())
    return merged
