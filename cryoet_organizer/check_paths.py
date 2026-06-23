from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from cryoet_organizer.file_resolver import (
    file_role_order,
    resolve_dataset_role_map,
    role_title,
)
from cryoet_organizer.project import DatasetRecord, ProjectData


@dataclass
class PathCheckEntry:
    dataset_name: str
    category: str
    label: str
    status: str
    path: str = ""
    note: str = ""
    ts_name: str = ""
    show_in_summary: bool = False


@dataclass
class PathCheckReport:
    entries: list[PathCheckEntry] = field(default_factory=list)
    summary_missing: list[PathCheckEntry] = field(default_factory=list)

    @property
    def all_found(self) -> bool:
        return not self.summary_missing


ROLE_LABEL_OVERRIDES = {
    "tomogram": "Tomograms",
    "aligned_stack": "Aligned Stacks",
    "angle_file": "Angle files",
    "mdoc": "MDOC files",
    "tomostar": "Tomostar files",
    "ts_xml": "TS XML files",
}


def _role_label(project: ProjectData, role: str) -> str:
    return ROLE_LABEL_OVERRIDES.get(role, role_title(project, role))


def _normalized_existing_path(value: str) -> str:
    cleaned = str(value).strip()
    if not cleaned:
        return ""
    candidate = Path(cleaned).expanduser()
    try:
        return str(candidate.resolve())
    except OSError:
        return str(candidate)


def _path_exists(value: str, kind: str) -> bool:
    cleaned = value.strip()
    if not cleaned:
        return False
    candidate = Path(cleaned).expanduser()
    if kind == "dir":
        return candidate.is_dir()
    return candidate.is_file()


def _general_dataset_entries(dataset: DatasetRecord) -> list[PathCheckEntry]:
    checks = [
        ("Raw frames folder", dataset.raw_frames_folder, "dir"),
        ("MDOC source folder", dataset.mdocs_source_folder or dataset.mdocs_folder, "dir"),
        ("Active MDOC folder", dataset.mdocs_folder, "dir"),
        ("Prepared MDOC folder", dataset.unified_mdocs_folder, "dir"),
        ("Gain file", dataset.gain_file, "file"),
        ("Processing root folder", dataset.processing_root_folder, "dir"),
        ("Processing folder", dataset.processing_folder, "dir"),
        ("Frame-series *.settings file", dataset.frame_series_settings_file, "file"),
        ("Tilt-series *.settings file", dataset.tilt_series_settings_file, "file"),
        ("Frame-series processing folder", dataset.frame_series_processing_folder, "dir"),
        ("Tilt-series processing folder", dataset.tilt_series_processing_folder, "dir"),
        ("Tomostar folder", dataset.tilt_series_data_folder, "dir"),
        ("Thumbnail folder", dataset.thumbnail_folder, "dir"),
    ]
    entries: list[PathCheckEntry] = []
    for label, value, kind in checks:
        if not str(value).strip():
            continue
        found = _path_exists(str(value), kind)
        entries.append(
            PathCheckEntry(
                dataset_name=dataset.dataset_name,
                category=label,
                label=label,
                status="found" if found else "missing",
                path=str(value),
                note="" if found else "Stored path does not currently exist.",
                show_in_summary=not found,
            )
        )
    return entries


def _source_file_references_dataset(source_path: str, dataset: DatasetRecord, base_dir: str) -> bool:
    source_candidate = Path(source_path).expanduser()
    if not source_candidate.is_absolute():
        source_candidate = Path(base_dir).expanduser() / source_candidate
    try:
        resolved_source = source_candidate.resolve()
    except OSError:
        resolved_source = source_candidate
    if not resolved_source.exists():
        return False

    dataset_setting_paths = {
        _normalized_existing_path(dataset.frame_series_settings_file),
        _normalized_existing_path(dataset.tilt_series_settings_file),
    }
    dataset_setting_paths.discard("")
    if not dataset_setting_paths:
        return False

    try:
        root = ET.parse(resolved_source).getroot()
    except Exception:
        return False

    values: list[str] = []
    for element in root.iter():
        text = str(element.text or "").strip()
        if text:
            values.append(text)
        for attr_value in element.attrib.values():
            cleaned = str(attr_value).strip()
            if cleaned:
                values.append(cleaned)

    for value in values:
        if ".settings" not in value.casefold():
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = resolved_source.parent / candidate
        try:
            normalized_candidate = str(candidate.resolve())
        except OSError:
            normalized_candidate = str(candidate)
        if normalized_candidate in dataset_setting_paths:
            return True
    return False


def _thumbnail_entries(dataset: DatasetRecord) -> list[PathCheckEntry]:
    entries: list[PathCheckEntry] = []
    for thumbnail in dataset.thumbnails:
        if thumbnail.image_path:
            found = Path(thumbnail.image_path).expanduser().is_file()
            entries.append(
                PathCheckEntry(
                    dataset_name=dataset.dataset_name,
                    category="Gallery thumbnails",
                    label=thumbnail.ts_name or Path(thumbnail.image_path).name,
                    status="found" if found else "missing",
                    path=thumbnail.image_path,
                    note="" if found else "Associated thumbnail file is missing.",
                    ts_name=thumbnail.ts_name,
                )
            )
        mrc_path = thumbnail.mrc_path.strip()
        found_mrc = bool(mrc_path) and Path(mrc_path).expanduser().is_file()
        entries.append(
            PathCheckEntry(
                dataset_name=dataset.dataset_name,
                category="Gallery associated .mrc files",
                label=thumbnail.ts_name or (Path(mrc_path).name if mrc_path else "Unassigned"),
                status="found" if found_mrc else "missing",
                path=mrc_path,
                note=(
                    ""
                    if found_mrc
                    else "No associated .mrc file is stored."
                    if not mrc_path
                    else "Associated .mrc file is missing."
                ),
                ts_name=thumbnail.ts_name,
            )
        )
    return entries


def _append_role_entries(report: PathCheckReport, project: ProjectData, dataset: DatasetRecord) -> None:
    for role in file_role_order(project):
        role_records = resolve_dataset_role_map(project, dataset, role)
        if not role_records:
            continue
        category = _role_label(project, role)
        found_count = 0
        missing_entries: list[PathCheckEntry] = []
        for record in role_records:
            resolved_status = "found" if record.path and record.source not in {"missing", "ambiguous"} else "missing"
            entry = PathCheckEntry(
                dataset_name=dataset.dataset_name,
                category=category,
                label=record.ts_name,
                status=resolved_status,
                path=record.path,
                note=record.note,
                ts_name=record.ts_name,
            )
            report.entries.append(entry)
            if resolved_status == "found":
                found_count += 1
            else:
                missing_entries.append(entry)

        if not missing_entries:
            continue
        if len(missing_entries) == len(role_records):
            report.summary_missing.append(
                PathCheckEntry(
                    dataset_name=dataset.dataset_name,
                    category=category,
                    label=category,
                    status="missing",
                    note=f"Missing for all {len(role_records)} TS.",
                    show_in_summary=True,
                )
            )
        else:
            for entry in missing_entries:
                report.summary_missing.append(
                    PathCheckEntry(
                        dataset_name=dataset.dataset_name,
                        category=category,
                        label=f"{category} / {entry.ts_name}",
                        status="missing",
                        path=entry.path,
                        note=entry.note or f"Missing for TS {entry.ts_name}.",
                        ts_name=entry.ts_name,
                        show_in_summary=True,
                    )
                )


def _append_grouped_summary(report: PathCheckReport, entries: list[PathCheckEntry], category: str) -> None:
    if not entries:
        return
    missing_entries = [entry for entry in entries if entry.status == "missing"]
    if not missing_entries:
        return
    if len(missing_entries) == len(entries):
        report.summary_missing.append(
            PathCheckEntry(
                dataset_name=entries[0].dataset_name,
                category=category,
                label=category,
                status="missing",
                note=f"Missing for all {len(entries)} TS.",
                show_in_summary=True,
            )
        )
        return
    for entry in missing_entries:
        report.summary_missing.append(
            PathCheckEntry(
                dataset_name=entry.dataset_name,
                category=category,
                label=f"{category} / {entry.ts_name or entry.label}",
                status="missing",
                path=entry.path,
                note=entry.note,
                ts_name=entry.ts_name,
                show_in_summary=True,
            )
        )


def collect_project_path_report(project: ProjectData) -> PathCheckReport:
    report = PathCheckReport()
    for dataset in project.datasets:
        general_entries = _general_dataset_entries(dataset)
        report.entries.extend(general_entries)
        report.summary_missing.extend(
            entry for entry in general_entries if entry.status == "missing"
        )

        _append_role_entries(report, project, dataset)

        thumbnail_entries = _thumbnail_entries(dataset)
        report.entries.extend(thumbnail_entries)
        grouped: dict[str, list[PathCheckEntry]] = {}
        for entry in thumbnail_entries:
            grouped.setdefault(entry.category, []).append(entry)
        for category, items in grouped.items():
            _append_grouped_summary(report, items, category)
    return report
