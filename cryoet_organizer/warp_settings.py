from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass
class WarpSettingsSummary:
    settings_path: str
    data_folder: str = ""
    gain_path: str = ""
    processing_folder: str = ""
    pixel_size: float = 0.0
    exposure: float = 0.0
    tomo_x: int = 0
    tomo_y: int = 0
    tomo_z: int = 0


def _resolve_settings_path(settings_file: Path, value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""

    candidate = Path(cleaned).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    return str((settings_file.parent / candidate).resolve())


def _read_param_map(section: ET.Element | None) -> dict[str, str]:
    if section is None:
        return {}
    result: dict[str, str] = {}
    for child in section.findall("Param"):
        name = child.attrib.get("Name", "").strip()
        if name:
            result[name] = child.attrib.get("Value", "").strip()
    return result


def parse_warp_settings(path: str | Path) -> WarpSettingsSummary:
    settings_file = Path(path).expanduser().resolve()
    root = ET.parse(settings_file).getroot()

    import_params = _read_param_map(root.find("Import"))
    tomo_params = _read_param_map(root.find("Tomo"))

    exposure_raw = import_params.get("DosePerAngstromFrame", "0").strip()
    try:
        exposure = abs(float(exposure_raw))
    except ValueError:
        exposure = 0.0

    def _as_float(value: str) -> float:
        try:
            return float(value)
        except ValueError:
            return 0.0

    def _as_int(value: str) -> int:
        try:
            return int(float(value))
        except ValueError:
            return 0

    return WarpSettingsSummary(
        settings_path=str(settings_file),
        data_folder=_resolve_settings_path(settings_file, import_params.get("DataFolder", "")),
        gain_path=_resolve_settings_path(settings_file, import_params.get("GainPath", "")),
        processing_folder=_resolve_settings_path(
            settings_file,
            import_params.get("ProcessingFolder", ""),
        ),
        pixel_size=_as_float(import_params.get("PixelSize", "0")),
        exposure=exposure,
        tomo_x=_as_int(tomo_params.get("DimensionsX", "0")),
        tomo_y=_as_int(tomo_params.get("DimensionsY", "0")),
        tomo_z=_as_int(tomo_params.get("DimensionsZ", "0")),
    )
