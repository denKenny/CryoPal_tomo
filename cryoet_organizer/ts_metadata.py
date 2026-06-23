from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any
from xml.etree import ElementTree as ET

from cryoet_organizer.file_resolver import resolve_dataset_file
from cryoet_organizer.project import DatasetRecord, ProjectData
from cryoet_organizer.warp_settings import parse_warp_settings

_TS_METADATA_CACHE: "OrderedDict[tuple[Any, ...], TsMetadata]" = OrderedDict()
_TS_METADATA_CACHE_MAX_ITEMS = 256


@dataclass
class TiltEntry:
    angle: float | None = None
    dose: float | None = None
    defocus: float | None = None
    frame_path: str = ""
    datetime_text: str = ""
    selected: bool | None = None
    axis_angle: float | None = None


@dataclass
class TsMetadata:
    dataset_name: str
    sample: str
    ts_name: str
    mdoc_path: str = ""
    tomostar_path: str = ""
    xml_path: str = ""
    tomogram_path: str = ""
    thumbnail_path: str = ""
    frame_series_settings_path: str = ""
    tilt_series_settings_path: str = ""
    raw_frames_folder: str = ""
    mdocs_folder: str = ""
    tomostar_folder: str = ""
    frame_series_processing_folder: str = ""
    tilt_series_processing_folder: str = ""
    pixel_size: float = 0.0
    voltage_kv: float = 0.0
    axis_angle: float | None = None
    ctf_resolution_estimate: float | None = None
    are_angles_inverted: bool | None = None
    plane_normal: str = ""
    tilt_count: int = 0
    selected_tilt_count: int = 0
    excluded_tilt_count: int = 0
    tilt_min: float | None = None
    tilt_max: float | None = None
    total_dose: float | None = None
    dose_per_tilt: float | None = None
    defocus_min: float | None = None
    defocus_max: float | None = None
    acquisition_start: str = ""
    acquisition_end: str = ""
    first_frame_path: str = ""
    last_frame_path: str = ""
    tilt_angles_text: str = ""
    cumulative_dose_text: str = ""
    selected_tilt_angles_text: str = ""
    excluded_tilt_angles_text: str = ""
    warnings: list[str] = field(default_factory=list)


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _safe_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = str(value).strip().casefold()
    if lowered in {"true", "1", "yes", "on"}:
        return True
    if lowered in {"false", "0", "no", "off"}:
        return False
    return None


def _resolve_relative(base_file: Path, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    candidate = Path(cleaned).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    return str((base_file.parent / candidate).resolve())


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    for fmt in ("%d-%b-%Y  %H:%M:%S", "%d-%b-%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def parse_mdoc(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    current: dict[str, str] | None = None
    global_map: dict[str, str] = {}
    tilts: list[dict[str, str]] = []
    for raw_line in file_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("[ZValue"):
            current = {}
            tilts.append(current)
            continue
        if line.startswith("[") and line.endswith("]"):
            continue
        if "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if current is None:
            global_map[key] = value
        else:
            current[key] = value
    return {"global": global_map, "tilts": tilts}


def parse_tomostar(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    lines = file_path.read_text(encoding="utf-8-sig", errors="ignore").splitlines()
    fields: list[str] = []
    rows: list[dict[str, str]] = []
    in_loop = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("data_"):
            continue
        if line == "loop_":
            in_loop = True
            fields = []
            continue
        if in_loop and line.startswith("_"):
            fields.append(line.split()[0])
            continue
        if in_loop and fields:
            values = raw_line.split()
            if len(values) < len(fields):
                continue
            rows.append({field: values[index] for index, field in enumerate(fields)})
    return {"rows": rows}


def parse_tiltseries_xml(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    root = ET.parse(file_path).getroot()

    def list_text(tag: str) -> list[str]:
        node = root.find(tag)
        if node is None or node.text is None:
            return []
        return [line.strip() for line in node.text.splitlines() if line.strip()]

    movie_paths = [_resolve_relative(file_path, item) for item in list_text("MoviePath")]
    return {
        "attributes": dict(root.attrib),
        "angles": list_text("Angles"),
        "dose": list_text("Dose"),
        "use_tilt": list_text("UseTilt"),
        "axis_angle": list_text("AxisAngle"),
        "movie_paths": movie_paths,
    }


def _tilts_from_mdoc(payload: dict[str, Any]) -> list[TiltEntry]:
    result: list[TiltEntry] = []
    for item in payload.get("tilts", []):
        result.append(
            TiltEntry(
                angle=_safe_float(item.get("TiltAngle")),
                dose=_safe_float(item.get("ExposureDose")),
                defocus=_safe_float(item.get("Defocus")),
                frame_path=item.get("SubFramePath", "").strip(),
                datetime_text=item.get("DateTime", "").strip(),
            )
        )
    return result


def _tilts_from_tomostar(payload: dict[str, Any], source_file: str) -> list[TiltEntry]:
    result: list[TiltEntry] = []
    base = Path(source_file)
    for item in payload.get("rows", []):
        result.append(
            TiltEntry(
                angle=_safe_float(item.get("_wrpAngleTilt")),
                dose=_safe_float(item.get("_wrpDose")),
                axis_angle=_safe_float(item.get("_wrpAxisAngle")),
                frame_path=_resolve_relative(base, item.get("_wrpMovieName", "")),
            )
        )
    return result


def _apply_xml_to_tilts(tilts: list[TiltEntry], payload: dict[str, Any]) -> list[TiltEntry]:
    if not tilts:
        for index, angle in enumerate(payload.get("angles", [])):
            tilts.append(
                TiltEntry(
                    angle=_safe_float(angle),
                    dose=_safe_float(payload.get("dose", [None] * 10_000)[index] if index < len(payload.get("dose", [])) else None),
                    selected=_safe_bool(payload.get("use_tilt", [None] * 10_000)[index] if index < len(payload.get("use_tilt", [])) else None),
                    axis_angle=_safe_float(payload.get("axis_angle", [None] * 10_000)[index] if index < len(payload.get("axis_angle", [])) else None),
                    frame_path=payload.get("movie_paths", ["" for _ in range(10_000)])[index] if index < len(payload.get("movie_paths", [])) else "",
                )
            )
        return tilts

    for index, tilt in enumerate(tilts):
        if index < len(payload.get("angles", [])):
            tilt.angle = tilt.angle if tilt.angle is not None else _safe_float(payload["angles"][index])
        if index < len(payload.get("dose", [])):
            tilt.dose = tilt.dose if tilt.dose is not None else _safe_float(payload["dose"][index])
        if index < len(payload.get("use_tilt", [])):
            tilt.selected = _safe_bool(payload["use_tilt"][index])
        if index < len(payload.get("axis_angle", [])):
            tilt.axis_angle = tilt.axis_angle if tilt.axis_angle is not None else _safe_float(payload["axis_angle"][index])
        if index < len(payload.get("movie_paths", [])) and not tilt.frame_path:
            tilt.frame_path = payload["movie_paths"][index]
    return tilts


def collect_ts_metadata(
    project: ProjectData,
    dataset: DatasetRecord,
    ts_name: str,
    thumbnail_path: str = "",
    mrc_path: str = "",
) -> TsMetadata:
    mdoc_record = resolve_dataset_file(project, dataset, ts_name, "mdoc")
    tomostar_record = resolve_dataset_file(project, dataset, ts_name, "tomostar")
    xml_record = resolve_dataset_file(project, dataset, ts_name, "ts_xml")
    resolved_tomogram_path = mrc_path or resolve_dataset_file(project, dataset, ts_name, "tomogram").path
    cache_key = _cache_key(
        dataset,
        ts_name,
        thumbnail_path,
        resolved_tomogram_path,
        [
            mdoc_record.path,
            tomostar_record.path,
            xml_record.path,
            dataset.frame_series_settings_file,
            dataset.tilt_series_settings_file,
        ],
    )
    cached = _TS_METADATA_CACHE.get(cache_key)
    if cached is not None:
        _TS_METADATA_CACHE.move_to_end(cache_key)
        return cached
    metadata = TsMetadata(
        dataset_name=dataset.dataset_name,
        sample=dataset.sample,
        ts_name=ts_name,
        mdoc_path=mdoc_record.path,
        tomostar_path=tomostar_record.path,
        xml_path=xml_record.path,
        tomogram_path=resolved_tomogram_path,
        thumbnail_path=thumbnail_path,
        frame_series_settings_path=dataset.frame_series_settings_file,
        tilt_series_settings_path=dataset.tilt_series_settings_file,
        raw_frames_folder=dataset.raw_frames_folder,
        mdocs_folder=dataset.mdocs_source_folder or dataset.mdocs_folder,
        tomostar_folder=dataset.tilt_series_data_folder,
        frame_series_processing_folder=dataset.frame_series_processing_folder,
        tilt_series_processing_folder=dataset.tilt_series_processing_folder,
    )

    mdoc_payload = parse_mdoc(metadata.mdoc_path) if metadata.mdoc_path and Path(metadata.mdoc_path).exists() else {}
    tomostar_payload = parse_tomostar(metadata.tomostar_path) if metadata.tomostar_path and Path(metadata.tomostar_path).exists() else {}
    xml_payload = parse_tiltseries_xml(metadata.xml_path) if metadata.xml_path and Path(metadata.xml_path).exists() else {}

    tilts = (
        _tilts_from_tomostar(tomostar_payload, metadata.tomostar_path)
        if tomostar_payload
        else _tilts_from_mdoc(mdoc_payload)
    )
    if xml_payload:
        tilts = _apply_xml_to_tilts(tilts, xml_payload)

    frame_settings = (
        parse_warp_settings(metadata.frame_series_settings_path)
        if metadata.frame_series_settings_path and Path(metadata.frame_series_settings_path).exists()
        else None
    )
    tilt_settings = (
        parse_warp_settings(metadata.tilt_series_settings_path)
        if metadata.tilt_series_settings_path and Path(metadata.tilt_series_settings_path).exists()
        else None
    )

    global_map = mdoc_payload.get("global", {}) if isinstance(mdoc_payload, dict) else {}
    pixel_candidates = [
        dataset.pixel_size,
        _safe_float(global_map.get("PixelSpacing")) or 0.0,
        frame_settings.pixel_size if frame_settings else 0.0,
        tilt_settings.pixel_size if tilt_settings else 0.0,
    ]
    metadata.pixel_size = next((value for value in pixel_candidates if value and value > 0), 0.0)
    metadata.voltage_kv = _safe_float(global_map.get("Voltage")) or 0.0

    angles = [tilt.angle for tilt in tilts if tilt.angle is not None]
    doses = [tilt.dose for tilt in tilts if tilt.dose is not None]
    defocus_values = [tilt.defocus for tilt in tilts if tilt.defocus is not None]
    selected_flags = [tilt.selected for tilt in tilts if tilt.selected is not None]
    dates = [_parse_datetime(tilt.datetime_text) for tilt in tilts if tilt.datetime_text]
    dates = [item for item in dates if item is not None]
    frame_paths = [tilt.frame_path for tilt in tilts if tilt.frame_path]

    metadata.tilt_count = len(tilts)
    if selected_flags:
        metadata.selected_tilt_count = sum(1 for flag in selected_flags if flag)
        metadata.excluded_tilt_count = sum(1 for flag in selected_flags if not flag)
    else:
        metadata.selected_tilt_count = metadata.tilt_count
        metadata.excluded_tilt_count = 0
    metadata.tilt_min = min(angles) if angles else None
    metadata.tilt_max = max(angles) if angles else None
    if doses:
        if tomostar_payload or xml_payload:
            metadata.total_dose = max(doses)
            positive_doses = [dose for dose in doses if dose > 0]
            metadata.dose_per_tilt = min(positive_doses) if positive_doses else min(doses)
            metadata.cumulative_dose_text = _format_float_list(doses)
        else:
            metadata.total_dose = sum(doses)
            metadata.dose_per_tilt = mean(doses)
    metadata.defocus_min = min(defocus_values) if defocus_values else None
    metadata.defocus_max = max(defocus_values) if defocus_values else None
    metadata.acquisition_start = min(dates).isoformat(sep=" ", timespec="seconds") if dates else ""
    metadata.acquisition_end = max(dates).isoformat(sep=" ", timespec="seconds") if dates else ""
    metadata.first_frame_path = frame_paths[0] if frame_paths else ""
    metadata.last_frame_path = frame_paths[-1] if frame_paths else ""
    metadata.tilt_angles_text = _format_angle_list(angles)
    selected_angles = [tilt.angle for tilt in tilts if tilt.angle is not None and tilt.selected is not False]
    excluded_angles = [tilt.angle for tilt in tilts if tilt.angle is not None and tilt.selected is False]
    metadata.selected_tilt_angles_text = _format_angle_list(selected_angles)
    metadata.excluded_tilt_angles_text = _format_angle_list(excluded_angles)

    axis_candidates: list[float] = []
    if xml_payload:
        xml_axis = _safe_float(xml_payload.get("attributes", {}).get("AxisAngle"))
        if xml_axis is not None:
            axis_candidates.append(xml_axis)
    if tomostar_payload:
        first_axis = next((tilt.axis_angle for tilt in tilts if tilt.axis_angle is not None), None)
        if first_axis is not None:
            axis_candidates.append(first_axis)
    header_axis = None
    title_line = global_map.get("T =   TiltAxisAngle") or global_map.get("TiltAxisAngle")
    if title_line:
        header_axis = _safe_float(title_line.split()[0])
    if header_axis is not None:
        axis_candidates.append(header_axis)
    metadata.axis_angle = axis_candidates[0] if axis_candidates else None

    if xml_payload:
        attrs = xml_payload.get("attributes", {})
        metadata.ctf_resolution_estimate = _safe_float(attrs.get("CTFResolutionEstimate"))
        metadata.are_angles_inverted = _safe_bool(attrs.get("AreAnglesInverted"))
        metadata.plane_normal = attrs.get("PlaneNormal", "")

    pixel_values = [value for value in {
        round(dataset.pixel_size, 4) if dataset.pixel_size else None,
        round((_safe_float(global_map.get("PixelSpacing")) or 0.0), 4) if global_map.get("PixelSpacing") else None,
        round(frame_settings.pixel_size, 4) if frame_settings and frame_settings.pixel_size else None,
        round(tilt_settings.pixel_size, 4) if tilt_settings and tilt_settings.pixel_size else None,
    } if value]
    if len(pixel_values) > 1:
        metadata.warnings.append("Pixel size differs across dataset, MDOC, or Warp settings.")
    if metadata.tilt_count == 0:
        metadata.warnings.append("No tilt entries could be parsed for this TS.")
    if metadata.xml_path and metadata.selected_tilt_count != metadata.tilt_count:
        metadata.warnings.append(
            f"{metadata.excluded_tilt_count} tilt(s) are currently excluded according to the XML metadata."
        )

    _TS_METADATA_CACHE[cache_key] = metadata
    _TS_METADATA_CACHE.move_to_end(cache_key)
    while len(_TS_METADATA_CACHE) > _TS_METADATA_CACHE_MAX_ITEMS:
        _TS_METADATA_CACHE.popitem(last=False)
    return metadata


def clear_ts_metadata_cache(dataset_name: str = "", ts_name: str = "") -> None:
    dataset_key = dataset_name.casefold().strip()
    ts_key = ts_name.casefold().strip()
    if not dataset_key and not ts_key:
        _TS_METADATA_CACHE.clear()
        return
    for key in list(_TS_METADATA_CACHE.keys()):
        key_dataset = str(key[0]).casefold().strip() if len(key) > 0 else ""
        key_ts = str(key[2]).casefold().strip() if len(key) > 2 else ""
        if dataset_key and key_dataset != dataset_key:
            continue
        if ts_key and key_ts != ts_key:
            continue
        _TS_METADATA_CACHE.pop(key, None)


def _cache_key(
    dataset: DatasetRecord,
    ts_name: str,
    thumbnail_path: str,
    tomogram_path: str,
    dependency_paths: list[str],
) -> tuple[Any, ...]:
    mtimes: list[tuple[str, float | None]] = []
    for path_str in dependency_paths:
        cleaned = str(path_str).strip()
        if not cleaned:
            mtimes.append(("", None))
            continue
        path = Path(cleaned)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = None
        mtimes.append((cleaned, mtime))
    return (
        dataset.dataset_name,
        dataset.sample,
        ts_name,
        thumbnail_path,
        tomogram_path,
        dataset.pixel_size,
        dataset.frame_series_processing_folder,
        dataset.tilt_series_processing_folder,
        tuple(mtimes),
    )


def ts_metadata_sections(metadata: TsMetadata) -> list[tuple[str, list[tuple[str, str]]]]:
    def fmt_float(value: float | None, decimals: int = 2) -> str:
        if value is None:
            return "-"
        return f"{value:.{decimals}f}"

    sections: list[tuple[str, list[tuple[str, str]]]] = [
        (
            "General",
            [
                ("Dataset", metadata.dataset_name),
                ("Sample", metadata.sample or "-"),
                ("TS name", metadata.ts_name),
                ("Thumbnail", metadata.thumbnail_path or "-"),
                ("Associated tomogram", metadata.tomogram_path or "-"),
            ],
        ),
        (
            "Files",
            [
                ("MDOC file", metadata.mdoc_path or "-"),
                ("Tomostar file", metadata.tomostar_path or "-"),
                ("XML file", metadata.xml_path or "-"),
                ("Frame-series settings", metadata.frame_series_settings_path or "-"),
                ("Tilt-series settings", metadata.tilt_series_settings_path or "-"),
                ("Raw frames folder", metadata.raw_frames_folder or "-"),
                ("MDOC source folder", metadata.mdocs_folder or "-"),
                ("Tomostar folder", metadata.tomostar_folder or "-"),
                ("Frame-series processing folder", metadata.frame_series_processing_folder or "-"),
                ("Tilt-series processing folder", metadata.tilt_series_processing_folder or "-"),
            ],
        ),
        (
            "Acquisition",
            [
                ("Pixel size (A/px)", fmt_float(metadata.pixel_size, 4) if metadata.pixel_size else "-"),
                ("Voltage (kV)", fmt_float(metadata.voltage_kv, 0) if metadata.voltage_kv else "-"),
                ("Number of tilts", str(metadata.tilt_count)),
                ("Selected tilts", str(metadata.selected_tilt_count)),
                ("Excluded tilts", str(metadata.excluded_tilt_count)),
                ("Tilt range", f"{fmt_float(metadata.tilt_min)} to {fmt_float(metadata.tilt_max)}"),
                ("Tilt angles", metadata.tilt_angles_text or "-"),
                ("Cumulative dose", metadata.cumulative_dose_text or "-"),
                ("Total dose", fmt_float(metadata.total_dose, 2)),
                ("Dose per tilt", fmt_float(metadata.dose_per_tilt, 2)),
                ("Selected tilt angles", metadata.selected_tilt_angles_text or "-"),
                ("Excluded tilt angles", metadata.excluded_tilt_angles_text or "-"),
                ("Axis angle", fmt_float(metadata.axis_angle, 3)),
                ("Defocus range", f"{fmt_float(metadata.defocus_min)} to {fmt_float(metadata.defocus_max)}"),
                ("Acquisition start", metadata.acquisition_start or "-"),
                ("Acquisition end", metadata.acquisition_end or "-"),
            ],
        ),
        (
            "Processing",
            [
                ("CTF resolution estimate", fmt_float(metadata.ctf_resolution_estimate, 2)),
                ("Angles inverted", "-" if metadata.are_angles_inverted is None else str(metadata.are_angles_inverted)),
                ("Plane normal", metadata.plane_normal or "-"),
                ("First frame path", metadata.first_frame_path or "-"),
                ("Last frame path", metadata.last_frame_path or "-"),
            ],
        ),
    ]
    if metadata.warnings:
        sections.append(("Warnings", [(f"Warning {index}", item) for index, item in enumerate(metadata.warnings, start=1)]))
    return sections


def _format_angle_list(values: list[float | None]) -> str:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return ""
    return ", ".join(f"{value:.2f}" for value in cleaned)


def _format_float_list(values: list[float | None]) -> str:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return ""
    return ", ".join(f"{value:.2f}" for value in cleaned)
