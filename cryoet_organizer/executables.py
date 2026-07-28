from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from cryoet_organizer.mtools_catalog import m_jobs_by_group
from cryoet_organizer.project import ProjectData
from cryoet_organizer.tomograms_catalog import TOMOGRAM_JOBS
from cryoet_organizer.warptools_catalog import jobs_by_group


EXECUTABLE_OVERRIDES_STATE_KEY = "executable_overrides"

WARPTOOLS_EXECUTABLE = "WarpTools"
MTOOLS_EXECUTABLE = "MTools"
MCORE_EXECUTABLE = "MCore"
CRYOLITHE_EXECUTABLE = "cryolithe"
PYTOM_MATCH_EXECUTABLE = "pytom_match_template.py"
PYTOM_EXTRACT_EXECUTABLE = "pytom_extract_candidates.py"
SLABIFY_EXECUTABLE = "slabify"
MEMBRAIN_EXECUTABLE = "membrain"


@dataclass(frozen=True)
class ExecutableUsage:
    executable: str
    applications: tuple[str, ...]


def _append_usage(usage: dict[str, list[str]], executable: str, application: str) -> None:
    applications = usage.setdefault(executable, [])
    if application not in applications:
        applications.append(application)


@lru_cache(maxsize=1)
def build_executable_usage_registry() -> list[ExecutableUsage]:
    usage: dict[str, list[str]] = {}

    for group, jobs in jobs_by_group().items():
        for job in jobs:
            _append_usage(usage, WARPTOOLS_EXECUTABLE, f"{group} > {job.command}")

    for group, jobs in m_jobs_by_group().items():
        for job in jobs:
            label = job.command if job.command == job.executable else f"{job.command}"
            _append_usage(usage, job.executable, f"{group} > {label}")
    _append_usage(usage, MTOOLS_EXECUTABLE, "MTools > create_population")

    tomogram_executable_by_key = {
        "cryolithe_denoising": CRYOLITHE_EXECUTABLE,
        "pytom_template_matching": PYTOM_MATCH_EXECUTABLE,
        "pytom_extract_coordinates": PYTOM_EXTRACT_EXECUTABLE,
        "slabify_mask_creation": SLABIFY_EXECUTABLE,
        "membrain_segmentation": MEMBRAIN_EXECUTABLE,
    }
    for job in TOMOGRAM_JOBS:
        executable = tomogram_executable_by_key.get(job.job_key)
        if executable:
            _append_usage(usage, executable, f"{job.group} > {job.title}")

    return [
        ExecutableUsage(executable=executable, applications=tuple(applications))
        for executable, applications in sorted(usage.items(), key=lambda item: item[0].casefold())
    ]


def get_project_executable_overrides(project: ProjectData) -> dict[str, str]:
    payload = getattr(project.state, EXECUTABLE_OVERRIDES_STATE_KEY, {})
    if not isinstance(payload, dict):
        return {}
    known_executables = {item.executable for item in build_executable_usage_registry()}
    cleaned: dict[str, str] = {}
    for executable, command in payload.items():
        executable_text = str(executable).strip()
        command_text = str(command).strip()
        if not executable_text or not command_text:
            continue
        if executable_text not in known_executables:
            continue
        if command_text == executable_text:
            continue
        cleaned[executable_text] = command_text
    return cleaned


def set_project_executable_overrides(project: ProjectData, overrides: dict[str, str]) -> None:
    known_executables = {item.executable for item in build_executable_usage_registry()}
    project.state.executable_overrides = {
        str(executable).strip(): str(command).strip()
        for executable, command in overrides.items()
        if str(executable).strip() in known_executables
        and str(command).strip()
        and str(command).strip() != str(executable).strip()
    }


def resolve_executable_command(project: ProjectData, executable: str) -> str:
    executable = str(executable).strip()
    overrides = get_project_executable_overrides(project)
    return overrides.get(executable, executable)
