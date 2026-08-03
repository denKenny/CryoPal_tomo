from __future__ import annotations

import shlex
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from cryoet_organizer.job_execution import history_timestamp_now
from cryoet_organizer.job_queue import ScheduledJobRef, iter_scheduled_job_refs
from cryoet_organizer.project import JobHistoryEntry, ProjectData


def workflow_timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def project_workflows(project: ProjectData) -> list[dict[str, Any]]:
    workflows = project.state.workflows
    if not isinstance(workflows, list):
        project.state.workflows = []
    return project.state.workflows


def workflow_label(workflow: dict[str, Any]) -> str:
    name = str(workflow.get("name", "")).strip()
    jobs = workflow.get("jobs", [])
    count = len(jobs) if isinstance(jobs, list) else 0
    return name or f"Workflow ({count} job{'s' if count != 1 else ''})"


def create_workflow_from_refs(refs: list[ScheduledJobRef], name: str = "") -> dict[str, Any]:
    now = workflow_timestamp_now()
    return {
        "workflow_id": uuid.uuid4().hex,
        "name": name.strip() or "New workflow",
        "created_at": now,
        "updated_at": now,
        "jobs": [workflow_job_from_ref(ref) for ref in refs],
    }


def workflow_job_from_ref(ref: ScheduledJobRef) -> dict[str, Any]:
    return {
        "workflow_job_id": uuid.uuid4().hex,
        "owner_kind": ref.owner_kind,
        "owner_name": ref.owner_name,
        "entry": sanitized_history_entry_payload(ref.entry),
    }


def sanitized_history_entry_payload(entry: JobHistoryEntry) -> dict[str, Any]:
    payload = entry.to_dict()
    payload.pop("entry_id", None)
    payload["timestamp"] = ""
    payload["action"] = "scheduled"
    payload["slurm_job_id"] = ""
    payload["slurm_script_path"] = ""
    artifacts = deepcopy(payload.get("artifacts", {})) if isinstance(payload.get("artifacts"), dict) else {}
    artifacts.pop("queue_order", None)
    payload["artifacts"] = artifacts
    return payload


def clone_workflow_job(job: dict[str, Any]) -> dict[str, Any]:
    cloned = deepcopy(job)
    cloned["workflow_job_id"] = uuid.uuid4().hex
    return cloned


def save_workflow(project: ProjectData, workflow: dict[str, Any]) -> dict[str, Any]:
    workflow = normalize_workflow(workflow)
    workflow["updated_at"] = workflow_timestamp_now()
    workflows = project_workflows(project)
    workflow_id = workflow.get("workflow_id")
    for index, existing in enumerate(workflows):
        if existing.get("workflow_id") == workflow_id:
            if not workflow.get("created_at"):
                workflow["created_at"] = existing.get("created_at") or workflow_timestamp_now()
            workflows[index] = workflow
            return workflow
    workflow["created_at"] = workflow.get("created_at") or workflow_timestamp_now()
    workflows.append(workflow)
    return workflow


def delete_workflow(project: ProjectData, workflow_id: str) -> None:
    project.state.workflows = [
        workflow
        for workflow in project_workflows(project)
        if str(workflow.get("workflow_id", "")) != workflow_id
    ]


def normalize_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    cleaned = deepcopy(workflow)
    cleaned["workflow_id"] = str(cleaned.get("workflow_id") or uuid.uuid4().hex)
    cleaned["name"] = str(cleaned.get("name", "")).strip() or "Untitled workflow"
    cleaned["created_at"] = str(cleaned.get("created_at", ""))
    cleaned["updated_at"] = str(cleaned.get("updated_at", ""))
    jobs = cleaned.get("jobs", [])
    cleaned["jobs"] = [normalize_workflow_job(job) for job in jobs if isinstance(job, dict)]
    return cleaned


def normalize_workflow_job(job: dict[str, Any]) -> dict[str, Any]:
    entry_payload = deepcopy(job.get("entry", {})) if isinstance(job.get("entry"), dict) else {}
    entry_payload = sanitized_history_entry_payload(JobHistoryEntry.from_dict(entry_payload))
    return {
        "workflow_job_id": str(job.get("workflow_job_id") or uuid.uuid4().hex),
        "owner_kind": str(job.get("owner_kind", "")),
        "owner_name": str(job.get("owner_name", "")),
        "entry": entry_payload,
    }


def schedule_workflow(project: ProjectData, workflow: dict[str, Any]) -> list[JobHistoryEntry]:
    workflow = normalize_workflow(workflow)
    created: list[JobHistoryEntry] = []
    missing: list[str] = []
    planned: list[tuple[Any, JobHistoryEntry]] = []
    existing_orders: list[int] = []
    for ref in iter_scheduled_job_refs(project):
        try:
            existing_orders.append(int(ref.entry.artifacts.get("queue_order", 0)))
        except Exception:
            continue
    next_order = (max(existing_orders) if existing_orders else 0) + 1
    for job in workflow.get("jobs", []):
        owner = _workflow_job_owner(project, job)
        if owner is None:
            missing.append(f"{job.get('owner_kind')}: {job.get('owner_name')}")
            continue
        entry_payload = deepcopy(job.get("entry", {}))
        entry = JobHistoryEntry.from_dict(entry_payload)
        entry.entry_id = uuid.uuid4().hex
        entry.action = "scheduled"
        entry.timestamp = history_timestamp_now()
        entry.slurm_job_id = ""
        entry.slurm_script_path = ""
        if isinstance(entry.artifacts, dict):
            entry.artifacts.pop("queue_order", None)
        else:
            entry.artifacts = {}
        entry.artifacts["queue_order"] = next_order
        next_order += 1
        planned.append((owner, entry))
    if missing:
        raise ValueError("Could not find workflow job owner(s): " + "; ".join(missing))
    for owner, entry in planned:
        owner.job_history.append(entry)
        created.append(entry)
    return created


def _workflow_job_owner(project: ProjectData, job: dict[str, Any]):
    owner_kind = str(job.get("owner_kind", ""))
    owner_name = str(job.get("owner_name", ""))
    if owner_kind == "dataset":
        return next((dataset for dataset in project.datasets if dataset.dataset_name == owner_name), None)
    if owner_kind == "m_population":
        return next((population for population in project.m_populations if population.name == owner_name), None)
    return None


def update_command_from_parameter_changes(command: str, old_parameters: dict[str, str], new_parameters: dict[str, str]) -> str:
    updated = command
    for key, new_value in new_parameters.items():
        old_value = old_parameters.get(key, "")
        if old_value == new_value:
            continue
        updated = _update_command_for_parameter(updated, key, old_value, new_value)
    return updated


def _update_command_for_parameter(command: str, key: str, old_value: str, new_value: str) -> str:
    if not command.strip():
        return command
    try:
        tokens = shlex.split(command)
    except ValueError:
        if old_value:
            return command.replace(old_value, new_value)
        return command
    if not tokens:
        return command
    changed = False
    if key.startswith("-"):
        flag_index = next((index for index, token in enumerate(tokens) if token == key), None)
        truthy = new_value.strip().casefold() in {"1", "true", "yes", "on"}
        falsey = new_value.strip().casefold() in {"0", "false", "no", "off", ""}
        if flag_index is not None:
            has_value = flag_index + 1 < len(tokens) and not tokens[flag_index + 1].startswith("-")
            if old_value.strip().casefold() in {"true", "false"} and falsey:
                del tokens[flag_index : flag_index + 1 + int(has_value)]
                changed = True
            elif has_value:
                if new_value:
                    tokens[flag_index + 1] = new_value
                else:
                    del tokens[flag_index : flag_index + 2]
                changed = True
            elif new_value and not truthy:
                tokens.insert(flag_index + 1, new_value)
                changed = True
        elif new_value and not falsey:
            tokens.extend([key, new_value] if not truthy else [key])
            changed = True
    if not changed and old_value:
        for index, token in enumerate(tokens):
            if token == old_value:
                tokens[index] = new_value
                changed = True
    return shlex.join(tokens) if changed else command
