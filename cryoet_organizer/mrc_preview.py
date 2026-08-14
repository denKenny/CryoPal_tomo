from __future__ import annotations

from dataclasses import dataclass
import mmap
from pathlib import Path
import struct

from cryoet_organizer.extraction_mask import MrcHeader, read_mrc_header


@dataclass(frozen=True)
class MrcPlanePreview:
    label: str
    width: int
    height: int
    pixels: bytes


@dataclass(frozen=True)
class MrcPreview:
    path: Path
    dimensions: tuple[int, int, int]
    mode: int
    planes: tuple[MrcPlanePreview, ...]


def mrc_preview_cache_key(path: str | Path, kind: str) -> tuple[str, float, int, str]:
    candidate = Path(path)
    stat = candidate.stat()
    return (str(candidate), stat.st_mtime, stat.st_size, kind)


def read_mrc_preview(path: str | Path, *, stack_2d: bool = False, max_size: int = 96) -> MrcPreview:
    candidate = Path(path)
    header = read_mrc_header(candidate)
    with candidate.open("rb") as handle:
        with mmap.mmap(handle.fileno(), length=0, access=mmap.ACCESS_READ) as data:
            if stack_2d:
                planes = (_read_xy_plane(data, header, header.nz // 2, max_size=max_size, label="XY"),)
            else:
                planes = (
                    _read_xy_plane(data, header, header.nz // 2, max_size=max_size, label="XY"),
                    _read_xz_plane(data, header, header.ny // 2, max_size=max_size, label="XZ"),
                )
    return MrcPreview(
        path=candidate,
        dimensions=(header.nx, header.ny, header.nz),
        mode=header.mode,
        planes=planes,
    )


def _sample_indices(length: int, max_size: int) -> list[int]:
    if max_size <= 1 or length <= 1:
        return [0]
    return [round(index * (length - 1) / (max_size - 1)) for index in range(max_size)]


def _bytes_per_value(header: MrcHeader) -> int:
    if header.mode == 0:
        return 1
    if header.mode in {1, 6}:
        return 2
    if header.mode == 2:
        return 4
    raise ValueError(f"MRC mode {header.mode} is not supported for preview.")


def _row_offset(header: MrcHeader, z_index: int, y_index: int) -> int:
    return header.data_offset + ((z_index * header.ny + y_index) * header.nx * _bytes_per_value(header))


def _unpack_values(header: MrcHeader, data: bytes) -> list[float]:
    if header.mode == 0:
        return [float(value) for value in data]
    if header.mode == 1:
        return [float(value) for (value,) in struct.iter_unpack(f"{header.endian}h", data)]
    if header.mode == 6:
        return [float(value) for (value,) in struct.iter_unpack(f"{header.endian}H", data)]
    return [float(value) for (value,) in struct.iter_unpack(f"{header.endian}f", data)]


def _read_row(data: mmap.mmap, header: MrcHeader, z_index: int, y_index: int) -> list[float]:
    offset = _row_offset(header, z_index, y_index)
    row_size = header.nx * _bytes_per_value(header)
    row_data = data[offset : offset + row_size]
    if len(row_data) != row_size:
        raise ValueError("Unexpected end of MRC data while reading preview row.")
    return _unpack_values(header, row_data)


def _read_xy_plane(data: mmap.mmap, header: MrcHeader, z_index: int, *, max_size: int, label: str) -> MrcPlanePreview:
    x_indices = _sample_indices(header.nx, max_size)
    y_indices = _sample_indices(header.ny, max_size)
    values: list[float] = []
    for y_index in y_indices:
        row = _read_row(data, header, z_index, y_index)
        values.extend(row[x_index] for x_index in x_indices)
    return _plane_from_values(label, len(x_indices), len(y_indices), values)


def _read_xz_plane(data: mmap.mmap, header: MrcHeader, y_index: int, *, max_size: int, label: str) -> MrcPlanePreview:
    x_indices = _sample_indices(header.nx, max_size)
    z_indices = _sample_indices(header.nz, max_size)
    values: list[float] = []
    for z_index in z_indices:
        row = _read_row(data, header, z_index, y_index)
        values.extend(row[x_index] for x_index in x_indices)
    return _plane_from_values(label, len(x_indices), len(z_indices), values)


def _plane_from_values(label: str, width: int, height: int, values: list[float]) -> MrcPlanePreview:
    finite_values = [value for value in values if value == value]
    if not finite_values:
        pixels = bytes(width * height)
        return MrcPlanePreview(label=label, width=width, height=height, pixels=pixels)
    low = min(finite_values)
    high = max(finite_values)
    if high <= low:
        shade = 128
        pixels = bytes([shade for _value in values])
        return MrcPlanePreview(label=label, width=width, height=height, pixels=pixels)
    scale = 255.0 / (high - low)
    pixels = bytes(
        max(0, min(255, int(round((value - low) * scale)))) if value == value else 0
        for value in values
    )
    return MrcPlanePreview(label=label, width=width, height=height, pixels=pixels)
