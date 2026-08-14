from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryoet_organizer.job_execution import display_history_timestamp, is_scheduled_history_entry
from cryoet_organizer.project import DatasetRecord, JobHistoryEntry, MPopulationRecord, ProjectData


@dataclass
class ScheduledJobRef:
    entry: JobHistoryEntry
    owner: DatasetRecord | MPopulationRecord
    owner_kind: str
    owner_name: str
    cwd: str

    @property
    def entry_id(self) -> str:
        return self.entry.entry_id


def iter_job_history_refs(project: ProjectData, *, scheduled_only: bool = False) -> list[ScheduledJobRef]:
    refs: list[ScheduledJobRef] = []
    seen_entry_ids: set[str] = set()
    for dataset in project.datasets:
        cwd = dataset.processing_folder or dataset.tilt_series_processing_folder or dataset.frame_series_processing_folder
        for entry in dataset.job_history:
            if scheduled_only and not is_scheduled_history_entry(entry):
                continue
            if entry.entry_id in seen_entry_ids:
                continue
            seen_entry_ids.add(entry.entry_id)
            refs.append(
                ScheduledJobRef(
                    entry=entry,
                    owner=dataset,
                    owner_kind="dataset",
                    owner_name=dataset.dataset_name,
                    cwd=cwd,
                )
            )
    for population in project.m_populations:
        for entry in population.job_history:
            if scheduled_only and not is_scheduled_history_entry(entry):
                continue
            if entry.entry_id in seen_entry_ids:
                continue
            seen_entry_ids.add(entry.entry_id)
            refs.append(
                ScheduledJobRef(
                    entry=entry,
                    owner=population,
                    owner_kind="m_population",
                    owner_name=population.name,
                    cwd=population.directory,
                )
            )
    return sorted(refs, key=_job_list_sort_key)


def iter_scheduled_job_refs(project: ProjectData) -> list[ScheduledJobRef]:
    return sorted(iter_job_history_refs(project, scheduled_only=True), key=_queue_sort_key)


def _queue_sort_key(ref: ScheduledJobRef) -> tuple[int, str, str]:
    order = ref.entry.artifacts.get("queue_order") if isinstance(ref.entry.artifacts, dict) else None
    try:
        order_value = int(order)
    except Exception:
        order_value = 10_000_000
    return (order_value, ref.entry.timestamp, ref.entry.entry_id)


def _job_list_sort_key(ref: ScheduledJobRef) -> tuple[int, int, str, str]:
    if is_scheduled_history_entry(ref.entry):
        order = ref.entry.artifacts.get("queue_order") if isinstance(ref.entry.artifacts, dict) else None
        try:
            order_value = int(order)
        except Exception:
            order_value = 10_000_000
        return (0, order_value, ref.entry.timestamp, ref.entry.entry_id)
    return (1, 0, reverse_timestamp_sort_key(ref.entry.timestamp), ref.entry.entry_id)


def reverse_timestamp_sort_key(timestamp: str) -> str:
    try:
        cleaned = str(timestamp).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        seconds = int(parsed.timestamp())
    except Exception:
        seconds = 0
    return f"{9999999999 - seconds:010d}"


def assign_queue_order(refs: list[ScheduledJobRef]) -> None:
    for index, ref in enumerate(refs, start=1):
        ref.entry.artifacts["queue_order"] = index


def remove_job_ref(ref: ScheduledJobRef) -> None:
    history = getattr(ref.owner, "job_history", None)
    if isinstance(history, list):
        ref.owner.job_history = [entry for entry in history if entry.entry_id != ref.entry.entry_id]


def remove_job_ref_from_project(project: ProjectData, ref: ScheduledJobRef) -> None:
    for owner in [*project.datasets, *project.m_populations]:
        history = getattr(owner, "job_history", None)
        if isinstance(history, list):
            owner.job_history = [entry for entry in history if entry.entry_id != ref.entry.entry_id]


def remove_scheduled_ref(ref: ScheduledJobRef) -> None:
    remove_job_ref(ref)


def display_ref_timestamp(ref: ScheduledJobRef) -> str:
    return display_history_timestamp(ref.entry)


def ref_dataset_name(ref: ScheduledJobRef) -> str:
    return ref.entry.dataset_name or ref.owner_name


def ref_cwd(ref: ScheduledJobRef) -> str | None:
    cleaned = (ref.cwd or "").strip()
    if not cleaned:
        return None
    return str(Path(cleaned).expanduser())
