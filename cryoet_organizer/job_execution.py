from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Callable

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
    overrides = {key: value for key, value in parameters.items() if key.startswith("slurm_header__")}
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
) -> JobHistoryEntry:
    return JobHistoryEntry(
        timestamp=history_timestamp_now(),
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
    def worker() -> None:
        failures: list[str] = []
        log_counter = 1
        for entry in entries:
            started_at = history_timestamp_now()
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
                    app.root.after(
                        0,
                        lambda current_entry=entry, current_time=started_at, current_result=result, current_profile=profile_name: on_entry_submitted(
                            current_entry,
                            current_time,
                            current_result,
                            current_profile,
                        ),
                    )
                    if wait_for_slurm_completion and not app.is_debug_mode_enabled():
                        succeeded, state = wait_for_slurm_job(result.job_id)
                        if not succeeded:
                            failures.append(f"{entry.job_name}: Slurm job ended with state {state}")
                            break
                        if on_entry_completed is not None:
                            app.root.after(0, lambda current_entry=entry: on_entry_completed(current_entry))
                else:
                    activation_command = app.resolve_environment_activation(entry.environment_title)
                    app.root.after(
                        0,
                        lambda current_entry=entry, current_time=started_at: on_entry_started(
                            current_entry,
                            current_time,
                        ),
                    )
                    process = app.start_managed_process_with_log(
                        entry.command,
                        cwd=cwd,
                        title=f"Scheduled job output ({log_counter}/{len(entries)}): {entry.job_name}",
                        activation_command=activation_command,
                    )
                    log_counter += 1
                    return_code = app.wait_managed_process(process)
                    if app.abort_requested():
                        failures.append(f"{entry.job_name}: aborted")
                        break
                    if return_code != 0:
                        failures.append(f"{entry.job_name}: exit code {return_code}")
                        break
                    if on_entry_completed is not None:
                        app.root.after(0, lambda current_entry=entry: on_entry_completed(current_entry))
            except Exception as exc:
                failures.append(f"{entry.job_name}: {exc}")
                break
            if app.abort_requested():
                failures.append(f"{entry.job_name}: aborted")
                break

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
    def worker() -> None:
        failures: list[str] = []
        log_counter = 1
        for item in items:
            command = str(item.get("command", "")).strip()
            dataset_name = str(item.get("dataset_name", "")).strip()
            job_name = str(item.get("job_name", "")).strip() or "job"
            cwd = str(item.get("cwd", "")).strip() or None
            error_label = str(item.get("error_label", "")).strip() or job_name
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
                    if on_submitted is not None:
                        app.root.after(
                            0,
                            lambda current_item=item, current_result=result: on_submitted(current_item, current_result),
                        )
                else:
                    activation_command = str(item.get("activation_command", "")).strip()
                    process = app.start_managed_process_with_log(
                        command,
                        cwd=cwd,
                        title=f"Command output ({log_counter}/{len(items)}): {job_name}",
                        activation_command=activation_command,
                    )
                    log_counter += 1
                    return_code = app.wait_managed_process(process)
                    if app.abort_requested():
                        failures.append(f"{error_label}: aborted")
                        break
                    if return_code != 0:
                        failures.append(f"{error_label}: exit code {return_code}")
                        break
                    if on_completed is not None:
                        app.root.after(0, lambda current_item=item: on_completed(current_item))
            except Exception as exc:
                failures.append(f"{error_label}: {exc}")
                break
            if app.abort_requested():
                failures.append(f"{error_label}: aborted")
                break

        app.root.after(0, lambda: on_finished(len(items), failures))

    threading.Thread(target=worker, daemon=True).start()
