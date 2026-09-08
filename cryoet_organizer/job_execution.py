from __future__ import annotations

import hashlib
import socket
import threading
from datetime import datetime, timezone
from typing import Callable

from cryoet_organizer import __version__
from cryoet_organizer.log_window import BatchCommandOutputWindow
from cryoet_organizer.project import JobHistoryEntry
from cryoet_organizer.slurm import SlurmSubmissionResult, wait_for_slurm_job


def build_slurm_override_metadata(
    partition: str = "",
    time_limit: str = "",
    gpus: str = "",
    cpus_per_task: str = "",
    mem: str = "",
    mem_per_cpu: str = "",
    mem_mode: str = "mem",
) -> dict[str, str]:
    return {
        "slurm_partition": partition.strip(),
        "slurm_time": time_limit.strip(),
        "slurm_gpus": gpus.strip(),
        "slurm_cpus_per_task": cpus_per_task.strip(),
        "slurm_mem": mem.strip(),
        "slurm_mem_per_cpu": mem_per_cpu.strip(),
        "slurm_mem_mode": mem_mode.strip() or "mem",
    }


def slurm_override_payload(parameters: dict[str, str]) -> dict[str, str]:
    overrides = {
        key.removeprefix("slurm_header__"): value
        for key, value in parameters.items()
        if key.startswith("slurm_header__")
    }
    if "slurm_memory_choice" in parameters:
        overrides["slurm_memory_choice"] = parameters.get("slurm_memory_choice", "")
    if overrides:
        return overrides
    return {
        "slurm_partition": parameters.get("slurm_partition", ""),
        "slurm_time": parameters.get("slurm_time", ""),
        "slurm_gpus": parameters.get("slurm_gpus", ""),
        "slurm_cpus_per_task": parameters.get("slurm_cpus_per_task", ""),
        "slurm_mem": parameters.get("slurm_mem", ""),
        "slurm_mem_per_cpu": parameters.get("slurm_mem_per_cpu", ""),
        "slurm_mem_mode": parameters.get("slurm_mem_mode", "mem"),
    }


def history_timestamp_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def execution_environment_fingerprint(title: str, activation_command: str) -> str:
    normalized = f"{title.strip()}\0{activation_command.strip()}"
    if not normalized.strip("\0"):
        return ""
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_scheduled_history_entry(entry: JobHistoryEntry) -> bool:
    return entry.action == "scheduled" or entry.timestamp == "Scheduled"


def display_history_timestamp(entry: JobHistoryEntry) -> str:
    timestamp = entry.timestamp.replace("T", " ") if "T" in entry.timestamp else entry.timestamp
    if is_scheduled_history_entry(entry):
        if entry.timestamp == "Scheduled":
            return "scheduled"
        return f"scheduled: {timestamp}"
    return timestamp


def create_history_entry(
    *,
    action: str,
    group: str,
    job_name: str,
    command: str,
    processing_tab: str = "",
    dataset_name: str = "",
    parameters: dict[str, str] | None = None,
    scheduled: bool = False,
    execution_mode: str = "local",
    slurm_profile: str = "",
    environment_title: str = "",
    working_directory: str = "",
) -> JobHistoryEntry:
    timestamp = history_timestamp_now()
    status = "scheduled" if scheduled else "created"
    submitted_at = ""
    started_at = ""
    if action == "submitted":
        status = "submitted"
        submitted_at = timestamp
    elif action == "ran":
        status = "running"
        started_at = timestamp
    elif action == "copied":
        status = "copied"
    return JobHistoryEntry(
        timestamp=timestamp,
        action=action,
        group=group,
        job_name=job_name,
        command=command,
        processing_tab=processing_tab,
        dataset_name=dataset_name,
        execution_mode=execution_mode,
        slurm_profile=slurm_profile,
        environment_title=environment_title,
        parameters=dict(parameters or {}),
        status=status,
        submitted_at=submitted_at,
        started_at=started_at,
        working_directory=working_directory,
        app_version=__version__,
        host=socket.gethostname(),
    )


def complete_history_entry(
    entry: JobHistoryEntry,
    return_code: int | None,
    *,
    aborted: bool = False,
    failure_reason: str = "",
) -> None:
    entry.exit_code = return_code
    entry.finished_at = history_timestamp_now()
    if aborted:
        entry.status = "cancelled"
        entry.failure_reason = failure_reason or "Aborted by user"
    elif return_code == 0:
        entry.status = "succeeded"
        entry.failure_reason = ""
    else:
        entry.status = "failed"
        entry.failure_reason = failure_reason or (
            f"Process exited with code {return_code}" if return_code is not None else "Process failed before exit"
        )


def execute_scheduled_history_entries(
    app,
    entries: list[JobHistoryEntry],
    *,
    cwd: str | None,
    dataset_name: str,
    force_slurm: bool,
    forced_profile: str,
    wait_for_slurm_completion: bool,
    on_entry_started: Callable[[JobHistoryEntry, str], None],
    on_entry_submitted: Callable[[JobHistoryEntry, str, SlurmSubmissionResult, str], None],
    on_entry_completed: Callable[[JobHistoryEntry], None] | None = None,
    on_finished: Callable[[int, list[str]], None],
) -> None:
    local_entries = [
        entry
        for entry in entries
        if not (force_slurm or entry.execution_mode == "slurm")
    ]
    output_window = None
    output_job_ids: dict[str, str] = {}
    if local_entries:
        output_window = BatchCommandOutputWindow(app.root, title="Scheduled job output")
        for index, entry in enumerate(local_entries, start=1):
            job_id = f"scheduled-{index}"
            output_job_ids[entry.entry_id] = job_id
            label_bits = [
                bit
                for bit in (entry.dataset_name, entry.parameters.get("ts_name", ""), entry.job_name)
                if bit
            ]
            label = " / ".join(label_bits) if label_bits else entry.job_name
            output_window.add_job(job_id, label, entry.command)

    def worker() -> None:
        failures: list[str] = []
        for entry in entries:
            entry.working_directory = cwd or ""
            entry.app_version = entry.app_version or __version__
            entry.host = entry.host or socket.gethostname()
            try:
                run_as_slurm = force_slurm or entry.execution_mode == "slurm"
                if run_as_slurm:
                    profile_name = forced_profile or entry.slurm_profile
                    result = app.submit_slurm_command(
                        entry.command,
                        profile_name=profile_name,
                        cwd=cwd,
                        dataset_name=dataset_name,
                        job_name=entry.job_name,
                        overrides=slurm_override_payload(entry.parameters),
                    )
                    submitted_at = history_timestamp_now()
                    entry.submitted_at = submitted_at
                    entry.status = "submitted"
                    entry.slurm_job_id = result.job_id
                    entry.slurm_profile = profile_name
                    app.root.after(
                        0,
                        lambda current_entry=entry, current_time=submitted_at, current_result=result, current_profile=profile_name: on_entry_submitted(
                            current_entry,
                            current_time,
                            current_result,
                            current_profile,
                        ),
                    )
                    if wait_for_slurm_completion and not app.is_debug_mode_enabled():
                        succeeded, state = wait_for_slurm_job(result.job_id)
                        if not succeeded:
                            entry.status = "failed"
                            entry.finished_at = history_timestamp_now()
                            entry.failure_reason = f"Slurm job ended with state {state}"
                            failures.append(f"{entry.job_name}: Slurm job ended with state {state}")
                            break
                        entry.status = "succeeded"
                        entry.exit_code = 0
                        entry.finished_at = history_timestamp_now()
                        if on_entry_completed is not None:
                            app.root.after(0, lambda current_entry=entry: on_entry_completed(current_entry))
                else:
                    started_at = history_timestamp_now()
                    entry.started_at = started_at
                    entry.status = "running"
                    activation_command = app.resolve_environment_activation(entry.environment_title)
                    entry.environment_fingerprint = execution_environment_fingerprint(
                        entry.environment_title,
                        activation_command,
                    )
                    batch_job_id = output_job_ids.get(entry.entry_id, "")
                    app.root.after(
                        0,
                        lambda current_entry=entry, current_time=started_at: on_entry_started(
                            current_entry,
                            current_time,
                        ),
                    )
                    if output_window is not None and batch_job_id:
                        output_window.set_job_running(batch_job_id)
                    process = app.start_managed_process_for_output(
                        entry.command,
                        cwd=cwd,
                        activation_command=activation_command,
                    )
                    if output_window is not None and batch_job_id:
                        output_window.attach_process(batch_job_id, process)
                    return_code = app.wait_managed_process(process)
                    entry.exit_code = return_code
                    entry.finished_at = history_timestamp_now()
                    if output_window is not None and batch_job_id:
                        output_window.set_job_finished(batch_job_id, return_code)
                    if app.abort_requested():
                        entry.status = "cancelled"
                        entry.failure_reason = "Aborted by user"
                        failures.append(f"{entry.job_name}: aborted")
                        break
                    if return_code != 0:
                        entry.status = "failed"
                        entry.failure_reason = f"Process exited with code {return_code}"
                        failures.append(f"{entry.job_name}: exit code {return_code}")
                        break
                    entry.status = "succeeded"
                    if on_entry_completed is not None:
                        app.root.after(0, lambda current_entry=entry: on_entry_completed(current_entry))
            except Exception as exc:
                entry.status = "failed"
                entry.finished_at = history_timestamp_now()
                entry.failure_reason = str(exc)
                failures.append(f"{entry.job_name}: {exc}")
                break
            if app.abort_requested():
                failures.append(f"{entry.job_name}: aborted")
                break

        if output_window is not None:
            output_window.finish_batch(failures)
        app.root.after(0, lambda: on_finished(len(entries), failures))

    threading.Thread(target=worker, daemon=True).start()


def execute_command_sequence(
    app,
    items: list[dict[str, object]],
    *,
    use_slurm: bool,
    profile_name: str,
    overrides: dict[str, str] | None,
    on_submitted: Callable[[dict[str, object], SlurmSubmissionResult], None] | None,
    on_completed: Callable[[dict[str, object]], None] | None,
    on_finished: Callable[[int, list[str]], None],
) -> None:
    output_window = None
    if not use_slurm and items:
        output_window = BatchCommandOutputWindow(app.root, title="Command output")
        for index, item in enumerate(items, start=1):
            job_id = f"job-{index}"
            item["_batch_output_id"] = job_id
            dataset_name = str(item.get("dataset_name", "")).strip()
            ts_name = str(item.get("ts_name", "")).strip()
            job_name = str(item.get("job_name", "")).strip() or "job"
            label_bits = [bit for bit in (dataset_name, ts_name, job_name) if bit]
            label = " / ".join(label_bits) if label_bits else job_name
            output_window.add_job(job_id, label, str(item.get("command", "")))

    def worker() -> None:
        failures: list[str] = []
        for item in items:
            command = str(item.get("command", "")).strip()
            dataset_name = str(item.get("dataset_name", "")).strip()
            job_name = str(item.get("job_name", "")).strip() or "job"
            cwd = str(item.get("cwd", "")).strip() or None
            error_label = str(item.get("error_label", "")).strip() or job_name
            batch_job_id = str(item.get("_batch_output_id", "")).strip()
            history_entry = item.get("history_entry")
            if isinstance(history_entry, JobHistoryEntry):
                history_entry.working_directory = cwd or ""
                history_entry.host = history_entry.host or socket.gethostname()
                history_entry.app_version = history_entry.app_version or __version__
            try:
                if use_slurm:
                    result = app.submit_slurm_command(
                        command,
                        profile_name=profile_name,
                        cwd=cwd,
                        dataset_name=dataset_name,
                        job_name=job_name,
                        overrides=overrides or {},
                    )
                    if isinstance(history_entry, JobHistoryEntry):
                        submitted_at = history_timestamp_now()
                        history_entry.action = "submitted"
                        history_entry.timestamp = submitted_at
                        history_entry.status = "submitted"
                        history_entry.submitted_at = submitted_at
                        history_entry.execution_mode = "slurm"
                        history_entry.slurm_profile = profile_name
                        history_entry.slurm_job_id = result.job_id
                        history_entry.slurm_script_path = result.script_path
                    if on_submitted is not None:
                        app.root.after(
                            0,
                            lambda current_item=item, current_result=result: on_submitted(current_item, current_result),
                        )
                else:
                    if isinstance(history_entry, JobHistoryEntry):
                        started_at = history_timestamp_now()
                        history_entry.action = "ran"
                        history_entry.timestamp = started_at
                        history_entry.status = "running"
                        history_entry.started_at = history_entry.started_at or started_at
                    activation_command = str(item.get("activation_command", "")).strip()
                    if isinstance(history_entry, JobHistoryEntry):
                        history_entry.environment_fingerprint = execution_environment_fingerprint(
                            history_entry.environment_title,
                            activation_command,
                        )
                    if output_window is not None and batch_job_id:
                        output_window.set_job_running(batch_job_id)
                    process = app.start_managed_process_for_output(
                        command,
                        cwd=cwd,
                        activation_command=activation_command,
                    )
                    if output_window is not None and batch_job_id:
                        output_window.attach_process(batch_job_id, process)
                    return_code = app.wait_managed_process(process)
                    if output_window is not None and batch_job_id:
                        output_window.set_job_finished(batch_job_id, return_code)
                    if app.abort_requested():
                        if isinstance(history_entry, JobHistoryEntry):
                            complete_history_entry(history_entry, return_code, aborted=True)
                        failures.append(f"{error_label}: aborted")
                        break
                    if return_code != 0:
                        if isinstance(history_entry, JobHistoryEntry):
                            complete_history_entry(history_entry, return_code)
                        failures.append(f"{error_label}: exit code {return_code}")
                        break
                    if isinstance(history_entry, JobHistoryEntry):
                        complete_history_entry(history_entry, return_code)
                    if on_completed is not None:
                        app.root.after(0, lambda current_item=item: on_completed(current_item))
            except Exception as exc:
                if isinstance(history_entry, JobHistoryEntry):
                    complete_history_entry(history_entry, None, failure_reason=str(exc))
                failures.append(f"{error_label}: {exc}")
                break
            if app.abort_requested():
                failures.append(f"{error_label}: aborted")
                break

        if output_window is not None:
            output_window.finish_batch(failures)
        app.root.after(0, lambda: on_finished(len(items), failures))

    threading.Thread(target=worker, daemon=True).start()
