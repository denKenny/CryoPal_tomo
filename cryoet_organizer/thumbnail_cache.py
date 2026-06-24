from __future__ import annotations

from pathlib import Path

from cryoet_organizer.preferences import project_preference
from cryoet_organizer.project import DatasetRecord, ProjectData, _sanitize_dataset_folder_name


def effective_thumbnail_source_folder(dataset: DatasetRecord) -> str:
    if dataset.thumbnail_folder:
        return dataset.thumbnail_folder
    base_processing_folder = dataset.tilt_series_processing_folder or str(
        Path(dataset.processing_folder) / "warp_tiltseries"
    )
    base_path = Path(base_processing_folder)
    candidates = [
        base_path / "reconstructions",
        base_path / "reconstruction",
    ]
    image_suffixes = {".png", ".jpg", ".jpeg"}
    for candidate in candidates:
        if candidate.exists() and any(
            item.is_file() and item.suffix.lower() in image_suffixes for item in candidate.iterdir()
        ):
            return str(candidate)
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


def _dataset_cache_base(dataset: DatasetRecord) -> Path:
    tilt_series_processing = dataset.tilt_series_processing_folder.strip()
    if tilt_series_processing:
        return Path(tilt_series_processing).expanduser().parent

    processing_folder = dataset.processing_folder.strip()
    if processing_folder:
        return Path(processing_folder).expanduser()

    frame_series_processing = dataset.frame_series_processing_folder.strip()
    if frame_series_processing:
        return Path(frame_series_processing).expanduser().parent

    thumbnail_source = effective_thumbnail_source_folder(dataset).strip()
    if thumbnail_source:
        thumbnail_path = Path(thumbnail_source).expanduser()
        if thumbnail_path.name.casefold() in {"reconstruction", "reconstructions"} and thumbnail_path.parent.name:
            return thumbnail_path.parent.parent
        return thumbnail_path.parent

    processing_root = dataset.processing_root_folder.strip()
    if processing_root:
        return Path(processing_root).expanduser()

    return Path(".")


def thumbnail_cache_location(project: ProjectData) -> str:
    return project_preference(
        project,
        "thumbnail_cache_location",
        "dataset/thumbnail-cache",
    ).strip() or "dataset/thumbnail-cache"


def resolve_thumbnail_cache_dir(project: ProjectData, dataset: DatasetRecord) -> Path:
    location = thumbnail_cache_location(project)
    dataset_base = _dataset_cache_base(dataset)
    normalized = location.replace("\\", "/").strip()
    if not normalized or normalized == "dataset/thumbnail-cache":
        return dataset_base / "thumbnail-cache"
    if normalized.startswith("dataset/"):
        relative = normalized.removeprefix("dataset/").strip("/")
        return dataset_base / relative
    base = Path(location).expanduser()
    if base.is_absolute():
        return base / _sanitize_dataset_folder_name(dataset.dataset_name)
    return dataset_base / normalized
