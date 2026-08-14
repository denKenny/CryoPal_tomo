from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


@dataclass(frozen=True)
class RelionPipelineJob:
    process_name: str
    job_type: str
    job_id: str
    relative_path: str
    alias: str = ""
    status: str = ""


def _natural_key(value: str) -> tuple:
    parts = re.split(r"(\d+)", value.casefold())
    return tuple(int(part) if part.isdigit() else part for part in parts)


def parse_relion_pipeline_processes(path: str | Path) -> list[RelionPipelineJob]:
    star_path = Path(path)
    lines = star_path.read_text(encoding="utf-8", errors="replace").splitlines()
    jobs: list[RelionPipelineJob] = []
    in_processes = False
    in_loop = False
    labels: list[str] = []
    process_column = 0
    alias_column = -1
    status_column = -1

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if in_processes and in_loop and labels:
                break
            continue
        if line.startswith("#"):
            continue
        if line.startswith("data_"):
            if in_processes and line != "data_pipeline_processes":
                break
            in_processes = line == "data_pipeline_processes"
            in_loop = False
            labels = []
            process_column = 0
            alias_column = -1
            status_column = -1
            continue
        if not in_processes:
            continue
        if line == "loop_":
            in_loop = True
            labels = []
            process_column = 0
            alias_column = -1
            status_column = -1
            continue
        if not in_loop:
            continue
        if line.startswith("_") or re.match(r"^[A-Za-z]\s+#\d+$", line):
            label_name = line.split()[0]
            labels.append(label_name)
            if label_name == "_rlnPipeLineProcessName":
                process_column = len(labels) - 1
            elif label_name == "_rlnPipeLineProcessAlias":
                alias_column = len(labels) - 1
            elif label_name == "_rlnPipeLineProcessStatusLabel":
                status_column = len(labels) - 1
            continue
        tokens = line.split()
        if not tokens:
            continue
        if process_column >= len(tokens):
            process_column = 0
        process_name = tokens[process_column].strip()
        alias = _clean_optional_token(tokens[alias_column]) if 0 <= alias_column < len(tokens) else ""
        status = _clean_optional_token(tokens[status_column]) if 0 <= status_column < len(tokens) else ""
        parsed = _parse_process_name(process_name, alias=alias, status=status)
        if parsed is not None:
            jobs.append(parsed)

    unique: dict[str, RelionPipelineJob] = {}
    for job in jobs:
        unique.setdefault(job.relative_path.casefold(), job)
    return sorted(unique.values(), key=lambda job: (_natural_key(job.job_type), _natural_key(job.job_id)))


def _parse_process_name(process_name: str, *, alias: str = "", status: str = "") -> RelionPipelineJob | None:
    cleaned = process_name.strip().strip("'\"").strip("/")
    if not cleaned:
        return None
    parts = [part for part in cleaned.split("/") if part]
    if len(parts) < 2:
        return None
    job_type = parts[0].strip()
    job_id = parts[1].strip()
    if not job_type or not job_id:
        return None
    return RelionPipelineJob(
        process_name=process_name,
        job_type=job_type,
        job_id=job_id,
        relative_path=f"{job_type}/{job_id}",
        alias=alias,
        status=status,
    )


def _clean_optional_token(value: str) -> str:
    cleaned = str(value).strip().strip("'\"")
    return "" if cleaned.casefold() == "none" else cleaned


def group_relion_jobs_by_type(jobs: list[RelionPipelineJob]) -> dict[str, list[RelionPipelineJob]]:
    grouped: dict[str, list[RelionPipelineJob]] = {}
    for job in jobs:
        grouped.setdefault(job.job_type, []).append(job)
    return {
        job_type: sorted(items, key=lambda job: _natural_key(job.job_id))
        for job_type, items in sorted(grouped.items(), key=lambda item: _natural_key(item[0]))
    }


def relion_job_files(root_directory: str | Path, job: RelionPipelineJob) -> list[Path]:
    folder = Path(root_directory) / job.relative_path
    if not folder.exists() or not folder.is_dir():
        return []
    files = [item for item in folder.iterdir() if item.is_file()]
    return sorted(files, key=lambda item: (_safe_mtime(item), item.name.casefold()), reverse=True)


def relion_job_number(job: RelionPipelineJob) -> int:
    match = re.search(r"(\d+)", job.job_id)
    return int(match.group(1)) if match else -1


def relion_jobs_descending(jobs: list[RelionPipelineJob]) -> list[RelionPipelineJob]:
    return sorted(jobs, key=lambda job: (relion_job_number(job), job.job_type.casefold(), job.job_id.casefold()), reverse=True)


def class3d_preview_files(root_directory: str | Path, job: RelionPipelineJob) -> tuple[int | None, list[Path]]:
    files = relion_job_files(root_directory, job)
    return class3d_preview_files_from_list(files)


def class3d_preview_files_from_list(files: list[Path]) -> tuple[int | None, list[Path]]:
    by_iteration: dict[int, list[Path]] = {}
    for path in files:
        if path.suffix.casefold() not in {".mrc", ".mrcs"}:
            continue
        iteration = _run_iteration(path.name)
        if iteration is None:
            continue
        if _class_number(path.name) is None:
            continue
        by_iteration.setdefault(iteration, []).append(path)
    if not by_iteration:
        return None, []
    iteration = max(by_iteration)
    return iteration, sorted(by_iteration[iteration], key=lambda path: (_class_number(path.name) or 0, path.name.casefold()))


def refine3d_preview_file(root_directory: str | Path, job: RelionPipelineJob) -> tuple[int | None, Path | None]:
    files = relion_job_files(root_directory, job)
    return refine3d_preview_file_from_list(files)


def refine3d_preview_file_from_list(files: list[Path]) -> tuple[int | None, Path | None]:
    candidates: list[tuple[int, int, str, Path]] = []
    for path in files:
        if path.suffix.casefold() not in {".mrc", ".mrcs"}:
            continue
        iteration = _run_iteration(path.name)
        if iteration is None:
            continue
        lower_name = path.name.casefold()
        priority = 0
        if "class" in lower_name and "half" not in lower_name:
            priority = 3
        elif "half" not in lower_name:
            priority = 2
        elif "unfil" not in lower_name:
            priority = 1
        candidates.append((iteration, priority, path.name.casefold(), path))
    if not candidates:
        return None, None
    iteration, _priority, _name, path = max(candidates, key=lambda item: (item[0], item[1], item[2]))
    return iteration, path


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _run_iteration(name: str) -> int | None:
    match = re.search(r"run_it(\d+)", name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _class_number(name: str) -> int | None:
    match = re.search(r"class(\d+)", name, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None
