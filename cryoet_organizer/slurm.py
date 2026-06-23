from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cryoet_organizer.project import ProjectData


SLURM_PROFILES_METADATA_KEY = "slurm_profiles"
MEMORY_FLAGS = {"--mem", "--mem-per-cpu"}
SLURM_SUCCESS_STATES = {"COMPLETED"}
SLURM_FAILURE_STATES = {
    "BOOT_FAIL",
    "CANCELLED",
    "DEADLINE",
    "FAILED",
    "NODE_FAIL",
    "OUT_OF_MEMORY",
    "PREEMPTED",
    "REVOKED",
    "TIMEOUT",
}


@dataclass
class SlurmHeaderField:
    key: str
    flag: str
    description: str = ""
    value: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> "SlurmHeaderField":
        return cls(
            key=str(payload.get("key", "")).strip(),
            flag=str(payload.get("flag", "")).strip(),
            description=str(payload.get("description", "")).strip(),
            value=str(payload.get("value", "")).strip(),
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SlurmProfile:
    name: str
    header_fields: list[SlurmHeaderField] = field(default_factory=list)
    modules: str = ""
    conda_activate: str = ""
    shell_preamble: str = ""
    partition: str = ""
    time_limit: str = ""
    gpus: str = ""
    cpus_per_task: str = ""
    mem: str = ""
    mem_per_cpu: str = ""
    job_name_template: str = ""
    output_log: str = ""
    error_log: str = ""

    def __post_init__(self) -> None:
        if self.header_fields:
            return
        legacy_payload = {
            "partition": self.partition,
            "time_limit": self.time_limit,
            "gpus": self.gpus,
            "cpus_per_task": self.cpus_per_task,
            "mem": self.mem,
            "mem_per_cpu": self.mem_per_cpu,
            "job_name_template": self.job_name_template,
            "output_log": self.output_log,
            "error_log": self.error_log,
        }
        self.header_fields = legacy_profile_header_fields(legacy_payload)

    @classmethod
    def from_dict(cls, payload: dict) -> "SlurmProfile":
        name = str(payload.get("name", "")).strip()
        header_payload = payload.get("header_fields")
        if isinstance(header_payload, list):
            header_fields = [
                field
                for field in (SlurmHeaderField.from_dict(item) for item in header_payload if isinstance(item, dict))
                if field.key and field.flag
            ]
        else:
            header_fields = legacy_profile_header_fields(payload)
        return cls(
            name=name,
            header_fields=header_fields,
            modules=str(payload.get("modules", "")).strip(),
            conda_activate=str(payload.get("conda_activate", "")).strip(),
            shell_preamble=str(payload.get("shell_preamble", "")).strip(),
            partition=str(payload.get("partition", "")).strip(),
            time_limit=str(payload.get("time_limit", "")).strip(),
            gpus=str(payload.get("gpus", "")).strip(),
            cpus_per_task=str(payload.get("cpus_per_task", "")).strip(),
            mem=str(payload.get("mem", "")).strip(),
            mem_per_cpu=str(payload.get("mem_per_cpu", "")).strip(),
            job_name_template=str(payload.get("job_name_template", "")).strip(),
            output_log=str(payload.get("output_log", "")).strip(),
            error_log=str(payload.get("error_log", "")).strip(),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "header_fields": [field.to_dict() for field in self.header_fields if field.flag],
            "modules": self.modules,
            "conda_activate": self.conda_activate,
            "shell_preamble": self.shell_preamble,
        }


@dataclass
class SlurmSubmissionResult:
    job_id: str
    script_path: str
    stdout: str = ""


def get_project_slurm_profiles(project: ProjectData) -> list[SlurmProfile]:
    payload = project.state.slurm_profiles
    return [SlurmProfile.from_dict(item) for item in payload if isinstance(item, dict)]


def set_project_slurm_profiles(project: ProjectData, profiles: list[SlurmProfile]) -> None:
    project.state.slurm_profiles = [profile.to_dict() for profile in profiles if profile.name]


def slurm_profile_names(project: ProjectData) -> list[str]:
    return [profile.name for profile in get_project_slurm_profiles(project) if profile.name]


def find_slurm_profile(project: ProjectData, name: str) -> SlurmProfile | None:
    for profile in get_project_slurm_profiles(project):
        if profile.name == name:
            return profile
    return None


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned or "job"


def _normalize_flag(flag: str) -> str:
    cleaned = flag.strip()
    if cleaned.startswith("#SBATCH"):
        cleaned = cleaned.removeprefix("#SBATCH").strip()
    return cleaned


def make_header_field_key(flag: str, description: str, existing_keys: set[str]) -> str:
    base = _safe_name(description or flag or "header")
    key = base
    index = 2
    while key in existing_keys:
        key = f"{base}_{index}"
        index += 1
    return key


def legacy_profile_header_fields(payload: dict) -> list[SlurmHeaderField]:
    fields: list[SlurmHeaderField] = []
    existing_keys: set[str] = set()

    def add(key_hint: str, flag: str, description: str, value: str) -> None:
        value = str(value).strip()
        if not value:
            return
        key = make_header_field_key(key_hint, description, existing_keys)
        existing_keys.add(key)
        fields.append(
            SlurmHeaderField(
                key=key,
                flag=flag,
                description=description,
                value=value,
            )
        )

    add("partition", "--partition", "partition", payload.get("partition", ""))
    add("time", "-t", "time (hh:mm:ss)", payload.get("time_limit", ""))
    gpus = str(payload.get("gpus", "")).strip()
    add("gpus", "--gres", "GPU resources", f"gpu:{gpus}" if gpus else "")
    add("cpus_per_task", "-c", "CPUs per task", payload.get("cpus_per_task", ""))
    add("mem", "--mem", "memory", payload.get("mem", ""))
    add("mem_per_cpu", "--mem-per-cpu", "memory per CPU", payload.get("mem_per_cpu", ""))
    add("job_name", "-J", "job name", payload.get("job_name_template", "{job_name}"))
    add("output_log", "-o", "output log", payload.get("output_log", ""))
    add("error_log", "-e", "error log", payload.get("error_log", ""))
    return fields


def profile_header_fields(profile: SlurmProfile) -> list[SlurmHeaderField]:
    return [field for field in profile.header_fields if field.flag.strip()]


def profile_memory_field_keys(profile: SlurmProfile) -> list[str]:
    return [
        field.key
        for field in profile_header_fields(profile)
        if _normalize_flag(field.flag) in MEMORY_FLAGS
    ]


def profile_memory_choice(profile: SlurmProfile) -> str:
    memory_keys = profile_memory_field_keys(profile)
    return memory_keys[0] if memory_keys else ""


def profile_memory_mode(profile: SlurmProfile) -> str:
    choice = profile_memory_choice(profile)
    for field in profile_header_fields(profile):
        if field.key == choice:
            return "mem_per_cpu" if _normalize_flag(field.flag) == "--mem-per-cpu" else "mem"
    return "mem"


def format_header_value(value: str, dataset_name: str, job_name: str) -> str:
    text = str(value).strip()
    if not text:
        return ""
    try:
        return text.format(dataset_name=dataset_name, job_name=job_name)
    except Exception:
        return text


def encode_slurm_overrides(
    profile: SlurmProfile | None,
    values: dict[str, str],
    *,
    memory_choice: str = "",
) -> dict[str, str]:
    payload: dict[str, str] = {}
    for key, value in values.items():
        if str(value).strip():
            payload[f"slurm_header__{key}"] = str(value).strip()
    if profile is not None and memory_choice:
        payload["slurm_memory_choice"] = memory_choice
    return payload


def decode_slurm_overrides(
    profile: SlurmProfile | None,
    parameters: dict[str, str],
) -> tuple[dict[str, str], str]:
    values: dict[str, str] = {}
    for key, value in parameters.items():
        if key.startswith("slurm_header__"):
            values[key.removeprefix("slurm_header__")] = value
    memory_choice = parameters.get("slurm_memory_choice", "")
    if profile is not None and not values:
        for field in profile_header_fields(profile):
            legacy_value = legacy_override_value_for_flag(parameters, field.flag)
            if legacy_value:
                values[field.key] = legacy_value
        if not memory_choice:
            memory_choice = _legacy_memory_choice(profile, parameters)
    return values, memory_choice


def _legacy_memory_choice(profile: SlurmProfile, parameters: dict[str, str]) -> str:
    memory_keys = profile_memory_field_keys(profile)
    if not memory_keys:
        return ""
    if parameters.get("slurm_mem_per_cpu", "").strip():
        for field in profile_header_fields(profile):
            if field.key in memory_keys and _normalize_flag(field.flag) == "--mem-per-cpu":
                return field.key
    if parameters.get("slurm_mem", "").strip():
        for field in profile_header_fields(profile):
            if field.key in memory_keys and _normalize_flag(field.flag) == "--mem":
                return field.key
    return memory_keys[0]


def legacy_override_value_for_flag(parameters: dict[str, str], flag: str) -> str:
    normalized = _normalize_flag(flag)
    if normalized in {"--partition", "-p"}:
        return parameters.get("slurm_partition", "")
    if normalized in {"--time", "-t"}:
        return parameters.get("slurm_time", "")
    if normalized in {"--gres"}:
        return parameters.get("slurm_gpus", "")
    if normalized in {"--cpus-per-task", "-c"}:
        return parameters.get("slurm_cpus_per_task", "")
    if normalized == "--mem":
        return parameters.get("slurm_mem", "")
    if normalized == "--mem-per-cpu":
        return parameters.get("slurm_mem_per_cpu", "")
    return ""


def render_sbatch_script(
    command: str,
    profile: SlurmProfile,
    cwd: str | None,
    dataset_name: str,
    job_name: str,
    overrides: dict[str, str] | None = None,
) -> str:
    overrides = overrides or {}
    memory_choice = overrides.get("slurm_memory_choice", "") or overrides.get("memory_choice", "")
    lines = ["#!/bin/bash"]

    memory_keys = set(profile_memory_field_keys(profile))
    if memory_keys and not memory_choice:
        memory_choice = profile_memory_choice(profile)

    for field in profile_header_fields(profile):
        if field.key in memory_keys and memory_choice and field.key != memory_choice:
            continue
        value = overrides.get(field.key, "").strip() or legacy_override_value_for_flag(overrides, field.flag).strip() or field.value
        value = format_header_value(value, dataset_name, job_name)
        normalized_flag = _normalize_flag(field.flag)
        if not normalized_flag:
            continue
        if value:
            if normalized_flag.startswith("--") and " " not in normalized_flag:
                lines.append(f"#SBATCH {normalized_flag}={value}")
            else:
                lines.append(f"#SBATCH {normalized_flag} {value}")
        else:
            lines.append(f"#SBATCH {normalized_flag}")

    lines.extend(["", "set -e"])
    if cwd:
        lines.append(f"cd {shlex.quote(str(cwd))}")
    for module_name in [item.strip() for item in profile.modules.splitlines() if item.strip()]:
        lines.append(f"module load {module_name}")
    if profile.conda_activate:
        lines.append(f"source {shlex.quote(profile.conda_activate)}")
    if profile.shell_preamble:
        lines.append(profile.shell_preamble)
    lines.append(command)
    lines.append("")
    return "\n".join(lines)


def write_sbatch_script(
    root_dir: str | Path,
    command: str,
    profile: SlurmProfile,
    cwd: str | None,
    dataset_name: str,
    job_name: str,
    overrides: dict[str, str] | None = None,
) -> Path:
    scripts_dir = Path(root_dir) / ".cryopal_slurm"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    base_name = _safe_name(f"{dataset_name}_{job_name}" if dataset_name else job_name)
    index = 1
    while True:
        script_path = scripts_dir / f"{base_name}_{index:03d}.sbatch"
        if not script_path.exists():
            break
        index += 1
    script_path.write_text(
        render_sbatch_script(command, profile, cwd, dataset_name, job_name, overrides),
        encoding="utf-8",
    )
    return script_path


def submit_sbatch_script(script_path: str | Path) -> SlurmSubmissionResult:
    result = subprocess.run(
        ["sbatch", str(script_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    stdout = result.stdout.strip()
    match = re.search(r"Submitted batch job\s+(\d+)", stdout)
    job_id = match.group(1) if match else ""
    return SlurmSubmissionResult(job_id=job_id, script_path=str(script_path), stdout=stdout)


def _normalize_slurm_state(value: str) -> str:
    state = str(value).strip()
    if not state:
        return ""
    if "|" in state:
        state = state.split("|", 1)[0]
    state = state.split()[0]
    state = state.split("+", 1)[0]
    state = state.split("(", 1)[0]
    return state.strip().upper()


def query_slurm_job_state(job_id: str) -> str:
    if not str(job_id).strip():
        return ""
    job_id = str(job_id).strip()

    try:
        result = subprocess.run(
            ["squeue", "-h", "-j", job_id, "-o", "%T"],
            check=False,
            capture_output=True,
            text=True,
        )
        state = _normalize_slurm_state(result.stdout)
        if state:
            return state
    except FileNotFoundError:
        pass

    try:
        result = subprocess.run(
            ["sacct", "-X", "-n", "-P", "-j", job_id, "--format=State"],
            check=False,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            state = _normalize_slurm_state(line)
            if state:
                return state
    except FileNotFoundError:
        pass

    return ""


def wait_for_slurm_job(
    job_id: str,
    *,
    poll_interval_seconds: float = 5.0,
    max_unknown_polls: int = 60,
) -> tuple[bool, str]:
    unknown_polls = 0
    while True:
        state = query_slurm_job_state(job_id)
        if state in SLURM_SUCCESS_STATES:
            return True, state
        if state in SLURM_FAILURE_STATES:
            return False, state
        if not state:
            unknown_polls += 1
            if unknown_polls >= max_unknown_polls:
                return False, "UNKNOWN"
        else:
            unknown_polls = 0
        time.sleep(poll_interval_seconds)


def export_slurm_profiles(path: str | Path, profiles: list[SlurmProfile]) -> Path:
    target = Path(path)
    if not str(target).endswith(".cryopal.slurm.json"):
        target = target.with_name(f"{target.name}.cryopal.slurm.json")
    target.write_text(
        json.dumps([profile.to_dict() for profile in profiles], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return target


def import_slurm_profiles(path: str | Path) -> list[SlurmProfile]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Invalid Slurm profile file.")
    return [SlurmProfile.from_dict(item) for item in payload if isinstance(item, dict)]
