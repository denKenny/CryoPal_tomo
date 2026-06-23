from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path


GROUPS = ("General", "Frame series", "Tilt series")
DOC_GROUP_HEADINGS = {
    "Frame Series": "Frame series",
    "Tilt Series": "Tilt series",
}


@dataclass(frozen=True)
class WarpToolFlag:
    name: str
    aliases: tuple[str, ...]
    description: str
    required: bool
    default_value: str
    widget: str
    browse_mode: str


@dataclass(frozen=True)
class WarpToolJob:
    group: str
    command: str
    flags: tuple[WarpToolFlag, ...]


def _reference_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "API_references.txt"


def _clean_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(" e.g. ", " e.g. ")
    return text


def _extract_default_value(text: str) -> str:
    match = re.search(r"Default:\s*(.*?)(?:\.\s|$)", text)
    if not match:
        return ""
    return _clean_text(match.group(1))


def _strip_required_and_default(text: str) -> tuple[str, bool, str]:
    required = "REQUIRED" in text
    text = text.replace("REQUIRED", "").strip()
    default_value = _extract_default_value(text)
    if default_value:
        text = re.sub(r"Default:\s*.*?(?:\.\s|$)", "", text, count=1).strip()
    return _clean_text(text), required, default_value


def _infer_widget(flag_name: str, description: str) -> tuple[str, str]:
    lower_flag = flag_name.lower().lstrip("-")
    lower_desc = description.lower()

    explicit_dir_flags = {
        "folder_processing",
        "folder_data",
        "mdocs",
        "frameseries",
        "to",
        "input_directory",
        "input_processing",
        "output_processing",
        "folder",
        "directory",
    }
    explicit_file_flags = {
        "output_star",
        "input_star",
        "particles_star",
        "new_settings",
        "settings",
        "template_path",
        "gain_path",
        "defects_path",
    }
    if lower_flag in explicit_dir_flags or "folder" in lower_flag or "directory" in lower_flag:
        return "path", "dir"
    if (
        lower_flag in explicit_file_flags
        or lower_flag.endswith("_path")
        or lower_flag.endswith("_file")
        or lower_flag.endswith("_star")
        or lower_flag.endswith("_settings")
    ):
        return "path", "file"
    if lower_desc.startswith("path to a folder") or lower_desc.startswith("path to the folder"):
        return "path", "dir"
    if lower_desc.startswith("path to ") or lower_desc.startswith("directory containing"):
        browse_mode = "dir"
        if "directory" not in lower_desc and "folder" not in lower_desc:
            browse_mode = "file"
        return "path", browse_mode

    exact_bool_flags = {
        "recursive",
        "histograms",
        "select",
        "deselect",
        "null",
        "invert",
        "mask",
        "gain_transpose",
        "template_flip",
        "whiten",
        "reuse_results",
        "delete_intermediate",
        "relative_output_paths",
        "normalized_coords",
        "check",
        "2d",
        "3d",
        "auto_zero",
        "fit_phase",
        "use_sum",
        "auto_hand",
        "check_hand",
        "out_averages",
        "out_average_halves",
        "averages",
        "average_halves",
        "halfmap_frames",
        "halfmap_tilts",
        "deconv",
        "template_flip",
    }
    bool_prefixes = (
        "dont_",
        "fit_",
        "use_",
        "gain_flip_",
        "keep_",
        "dont_normalize_",
        "set_",
    )
    if lower_flag in exact_bool_flags or lower_flag.startswith(bool_prefixes):
        return "bool", "none"

    if any(
        lower_desc.startswith(prefix)
        for prefix in (
            "fit ",
            "use ",
            "flip ",
            "transpose ",
            "change ",
            "invert ",
            "apply ",
            "export ",
            "delete ",
            "make ",
            "mirror ",
            "perform ",
            "reuse ",
            "ignore ",
            "only check ",
            "also produce ",
            "don't ",
        )
    ):
        return "bool", "none"

    return "text", "none"


@lru_cache(maxsize=1)
def load_warptools_jobs() -> tuple[WarpToolJob, ...]:
    reference_path = _reference_path()
    if not reference_path.exists():
        return ()

    text = reference_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    command_name_pattern = re.compile(r"^[a-z0-9_]+$")
    flag_pattern = re.compile(
        r"^(?P<aliases>(?:-[^,\s]+,\s+)?--[a-z0-9_][a-z0-9_\-]*)\s{2,}(?P<rest>.+)$"
    )

    group = "General"
    jobs: list[WarpToolJob] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped in DOC_GROUP_HEADINGS:
            group = DOC_GROUP_HEADINGS[stripped]
            index += 1
            continue

        if stripped and command_name_pattern.fullmatch(stripped):
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            if next_index < len(lines) and lines[next_index].startswith(
                "WarpTools - a collection"
            ):
                command = stripped
                index = next_index
                while index < len(lines) and "Showing all available options for command" not in lines[index]:
                    index += 1
                index += 1

                parsed_flags: list[WarpToolFlag] = []
                current_flag: dict | None = None

                while index < len(lines):
                    raw_line = lines[index].rstrip("\n")
                    stripped_line = raw_line.strip()

                    if stripped_line in DOC_GROUP_HEADINGS:
                        index -= 1
                        break

                    if stripped_line and command_name_pattern.fullmatch(stripped_line):
                        lookahead = index + 1
                        while lookahead < len(lines) and not lines[lookahead].strip():
                            lookahead += 1
                        if lookahead < len(lines) and lines[lookahead].startswith(
                            "WarpTools - a collection"
                        ):
                            index -= 1
                            break

                    flag_match = flag_pattern.match(raw_line)
                    if flag_match:
                        if current_flag:
                            parsed_flags.append(_build_flag(current_flag))
                        aliases = tuple(flag_match.group("aliases").split(", "))
                        current_flag = {
                            "name": aliases[-1],
                            "aliases": aliases,
                            "text": flag_match.group("rest").strip(),
                        }
                    elif current_flag and stripped_line and "-----" not in stripped_line:
                        current_flag["text"] += f" {stripped_line}"

                    index += 1

                if current_flag:
                    parsed_flags.append(_build_flag(current_flag))

                jobs.append(
                    WarpToolJob(
                        group=group,
                        command=command,
                        flags=tuple(parsed_flags),
                    )
                )

        index += 1

    return tuple(jobs)


def _build_flag(payload: dict) -> WarpToolFlag:
    description, required, default_value = _strip_required_and_default(payload["text"])
    widget, browse_mode = _infer_widget(payload["name"], description)
    return WarpToolFlag(
        name=payload["name"],
        aliases=tuple(payload["aliases"]),
        description=description,
        required=required,
        default_value=default_value,
        widget=widget,
        browse_mode=browse_mode,
    )


@lru_cache(maxsize=1)
def jobs_by_group() -> dict[str, tuple[WarpToolJob, ...]]:
    grouped: dict[str, list[WarpToolJob]] = {group: [] for group in GROUPS}
    create_settings_job: WarpToolJob | None = None
    for job in load_warptools_jobs():
        if job.command == "create_settings":
            create_settings_job = job
            continue
        grouped.setdefault(job.group, []).append(job)

    if create_settings_job is not None:
        grouped["Frame series"].insert(
            0,
            replace(create_settings_job, group="Frame series"),
        )
        grouped["Tilt series"].insert(
            0,
            replace(create_settings_job, group="Tilt series"),
        )
    return {group: tuple(items) for group, items in grouped.items()}
