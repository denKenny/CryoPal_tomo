from __future__ import annotations

import math
import os
import struct
import tempfile
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from cryoet_organizer.project import best_matching_path_for_ts
from cryoet_organizer.star_merge import OperationAborted, parse_star


class ExtractionMaskError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExtractionMaskParticle:
    ts_name: str
    x: float
    y: float
    z: float
    rot: float = 0.0
    tilt: float = 0.0
    psi: float = 0.0


@dataclass(frozen=True)
class ExtractionMaskOutput:
    ts_name: str
    path: Path
    particle_count: int
    used_existing_mask: bool = False


@dataclass(frozen=True)
class ExtractionMaskResult:
    input_star_path: Path
    output_directory: Path
    dimensions: tuple[int, int, int]
    input_angpix: float
    output_angpix: float
    cleanup_distance_angstrom: float
    outputs: list[ExtractionMaskOutput] = field(default_factory=list)

    @property
    def total_ts(self) -> int:
        return len(self.outputs)

    @property
    def total_particles(self) -> int:
        return sum(output.particle_count for output in self.outputs)


@dataclass(frozen=True)
class MrcHeader:
    nx: int
    ny: int
    nz: int
    mode: int
    data_offset: int
    voxel_size: tuple[float, float, float]
    endian: str = "<"


@dataclass(frozen=True)
class ShapeMask:
    path: Path
    dimensions: tuple[int, int, int]
    center: tuple[float, float, float]
    scale_to_output_px: float
    active: bytes


def _check_cancel(cancel_event=None) -> None:
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise OperationAborted("Aborted by user.")


def _header_index(headers: list[str], name: str, *, required: bool = True) -> int:
    target = name.casefold()
    for index, header in enumerate(headers):
        if header.casefold() == target:
            return index
    if required:
        raise ExtractionMaskError(f"Missing STAR column {name}.")
    return -1


def detect_extraction_star_angpix(path: str | Path) -> float | None:
    document = parse_star(path)
    for block in document.blocks:
        if block.kind == "loop":
            pixel_index = _header_index(block.headers, "_rlnImagePixelSize", required=False)
            if pixel_index < 0:
                continue
            for row in block.rows:
                if pixel_index >= len(row):
                    continue
                try:
                    value = float(row[pixel_index])
                except ValueError:
                    continue
                if value > 0:
                    return value
        else:
            for key, value in block.values:
                if key.casefold() != "_rlnimagepixelsize":
                    continue
                try:
                    parsed = float(value)
                except ValueError:
                    continue
                if parsed > 0:
                    return parsed
    return None


def extraction_star_ts_names(path: str | Path) -> list[str]:
    block = _particle_coordinate_block(path)
    ts_index = _ts_name_index(block.headers)
    names: OrderedDict[str, None] = OrderedDict()
    for row in block.rows:
        if ts_index < len(row):
            name = row[ts_index].strip()
            if name:
                names.setdefault(name, None)
    return list(names.keys())


def _particle_coordinate_block(path: str | Path):
    document = parse_star(path)
    coordinate_headers = {"_rlnCoordinateX", "_rlnCoordinateY", "_rlnCoordinateZ"}
    for block in document.blocks:
        if block.kind != "loop":
            continue
        available = {header.casefold() for header in block.headers}
        if not {header.casefold() for header in coordinate_headers}.issubset(available):
            continue
        if _ts_name_index(block.headers, required=False) >= 0:
            return block
    raise ExtractionMaskError(
        "Could not find a particle STAR loop with coordinate X/Y/Z and MicrographName/TomoName columns."
    )


def _ts_name_index(headers: list[str], *, required: bool = True) -> int:
    for name in ("_rlnMicrographName", "_rlnTomoName"):
        index = _header_index(headers, name, required=False)
        if index >= 0:
            return index
    if required:
        raise ExtractionMaskError("Missing STAR column _rlnMicrographName or _rlnTomoName.")
    return -1


def grouped_extraction_particles(
    input_star_path: str | Path,
    *,
    input_angpix: float,
    output_angpix: float,
    require_angles: bool = False,
) -> OrderedDict[str, list[ExtractionMaskParticle]]:
    if input_angpix <= 0:
        raise ExtractionMaskError("Input STAR Angpix must be greater than 0.")
    if output_angpix <= 0:
        raise ExtractionMaskError("Output mask Angpix must be greater than 0.")

    block = _particle_coordinate_block(input_star_path)
    ts_index = _ts_name_index(block.headers)
    x_index = _header_index(block.headers, "_rlnCoordinateX")
    y_index = _header_index(block.headers, "_rlnCoordinateY")
    z_index = _header_index(block.headers, "_rlnCoordinateZ")
    origin_x_ang_index = _header_index(block.headers, "_rlnOriginXAngst", required=False)
    origin_y_ang_index = _header_index(block.headers, "_rlnOriginYAngst", required=False)
    origin_z_ang_index = _header_index(block.headers, "_rlnOriginZAngst", required=False)
    origin_x_px_index = _header_index(block.headers, "_rlnOriginX", required=False)
    origin_y_px_index = _header_index(block.headers, "_rlnOriginY", required=False)
    origin_z_px_index = _header_index(block.headers, "_rlnOriginZ", required=False)
    rot_index = _header_index(block.headers, "_rlnAngleRot", required=require_angles)
    tilt_index = _header_index(block.headers, "_rlnAngleTilt", required=require_angles)
    psi_index = _header_index(block.headers, "_rlnAnglePsi", required=require_angles)

    scale = input_angpix / output_angpix
    grouped: OrderedDict[str, list[ExtractionMaskParticle]] = OrderedDict()
    for row_number, row in enumerate(block.rows, start=1):
        try:
            ts_name = row[ts_index].strip()
            coord_x = float(row[x_index])
            coord_y = float(row[y_index])
            coord_z = float(row[z_index])
        except (IndexError, ValueError) as exc:
            raise ExtractionMaskError(f"Invalid particle coordinate row {row_number}.") from exc
        if not ts_name:
            continue

        origin_x_ang = _row_float(row, origin_x_ang_index)
        origin_y_ang = _row_float(row, origin_y_ang_index)
        origin_z_ang = _row_float(row, origin_z_ang_index)
        if origin_x_ang is None:
            origin_x_ang = (_row_float(row, origin_x_px_index) or 0.0) * input_angpix
        if origin_y_ang is None:
            origin_y_ang = (_row_float(row, origin_y_px_index) or 0.0) * input_angpix
        if origin_z_ang is None:
            origin_z_ang = (_row_float(row, origin_z_px_index) or 0.0) * input_angpix

        particle = ExtractionMaskParticle(
            ts_name=ts_name,
            x=coord_x * scale - origin_x_ang / output_angpix,
            y=coord_y * scale - origin_y_ang / output_angpix,
            z=coord_z * scale - origin_z_ang / output_angpix,
            rot=_row_float(row, rot_index) or 0.0,
            tilt=_row_float(row, tilt_index) or 0.0,
            psi=_row_float(row, psi_index) or 0.0,
        )
        grouped.setdefault(ts_name, []).append(particle)
    if not grouped:
        raise ExtractionMaskError("No particles with tomogram names were found in the STAR file.")
    return grouped


def _row_float(row: list[str], index: int) -> float | None:
    if index < 0 or index >= len(row):
        return None
    try:
        return float(row[index])
    except ValueError:
        return None


def ts_stem_from_star_name(value: str) -> str:
    normalized = str(value).strip().replace("\\", "/")
    if not normalized:
        return "TS"
    return Path(normalized).stem or "TS"


def format_angpix_for_filename(value: float) -> str:
    if math.isclose(value, round(value), rel_tol=0.0, abs_tol=1e-8):
        return str(int(round(value)))
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text.replace(os.sep, "_")


def output_mask_path(output_directory: str | Path, ts_name: str, output_angpix: float) -> Path:
    stem = ts_stem_from_star_name(ts_name)
    angpix_text = format_angpix_for_filename(output_angpix)
    return Path(output_directory) / f"{stem}_ExtractionMask_{angpix_text}A.mrc"


def read_mrc_header(path: str | Path) -> MrcHeader:
    path = Path(path)
    with path.open("rb") as handle:
        header = handle.read(1024)
    if len(header) < 1024:
        raise ExtractionMaskError(f"{path.name} is too small to be a valid MRC file.")

    endian = "<"
    nx, ny, nz, mode = struct.unpack_from("<4i", header, 0)
    if nx <= 0 or ny <= 0 or nz <= 0 or mode not in {0, 1, 2, 6}:
        nx, ny, nz, mode = struct.unpack_from(">4i", header, 0)
        endian = ">"
    if nx <= 0 or ny <= 0 or nz <= 0:
        raise ExtractionMaskError(f"Could not read dimensions from {path.name}.")
    if mode not in {0, 1, 2, 6}:
        raise ExtractionMaskError(f"MRC mode {mode} in {path.name} is not supported for mask input.")

    nsymbt = struct.unpack_from(f"{endian}i", header, 92)[0]
    cell_x, cell_y, cell_z = struct.unpack_from(f"{endian}3f", header, 40)
    voxel_size = (
        cell_x / nx if cell_x > 0 else 1.0,
        cell_y / ny if cell_y > 0 else 1.0,
        cell_z / nz if cell_z > 0 else 1.0,
    )
    return MrcHeader(
        nx=nx,
        ny=ny,
        nz=nz,
        mode=mode,
        data_offset=1024 + max(0, nsymbt),
        voxel_size=voxel_size,
        endian=endian,
    )


def _read_mask_slice(handle, header: MrcHeader) -> bytearray:
    voxel_count = header.nx * header.ny
    if header.mode == 0:
        data = handle.read(voxel_count)
        if len(data) != voxel_count:
            raise ExtractionMaskError("Unexpected end of MRC data.")
        return bytearray(1 if value else 0 for value in data)

    if header.mode == 1:
        data = handle.read(voxel_count * 2)
        if len(data) != voxel_count * 2:
            raise ExtractionMaskError("Unexpected end of MRC data.")
        return bytearray(1 if value > 0 else 0 for (value,) in struct.iter_unpack(f"{header.endian}h", data))

    if header.mode == 6:
        data = handle.read(voxel_count * 2)
        if len(data) != voxel_count * 2:
            raise ExtractionMaskError("Unexpected end of MRC data.")
        return bytearray(1 if value > 0 else 0 for (value,) in struct.iter_unpack(f"{header.endian}H", data))

    data = handle.read(voxel_count * 4)
    if len(data) != voxel_count * 4:
        raise ExtractionMaskError("Unexpected end of MRC data.")
    return bytearray(1 if value > 0 else 0 for (value,) in struct.iter_unpack(f"{header.endian}f", data))


def _read_mrc_slice_values(handle, header: MrcHeader) -> list[float]:
    voxel_count = header.nx * header.ny
    if header.mode == 0:
        data = handle.read(voxel_count)
        if len(data) != voxel_count:
            raise ExtractionMaskError("Unexpected end of MRC data.")
        return [float(value) for value in data]

    if header.mode == 1:
        data = handle.read(voxel_count * 2)
        if len(data) != voxel_count * 2:
            raise ExtractionMaskError("Unexpected end of MRC data.")
        return [float(value) for (value,) in struct.iter_unpack(f"{header.endian}h", data)]

    if header.mode == 6:
        data = handle.read(voxel_count * 2)
        if len(data) != voxel_count * 2:
            raise ExtractionMaskError("Unexpected end of MRC data.")
        return [float(value) for (value,) in struct.iter_unpack(f"{header.endian}H", data)]

    data = handle.read(voxel_count * 4)
    if len(data) != voxel_count * 4:
        raise ExtractionMaskError("Unexpected end of MRC data.")
    return [float(value) for (value,) in struct.iter_unpack(f"{header.endian}f", data)]


def _read_shape_mask(
    path: str | Path,
    *,
    output_angpix: float,
    rescale_to_output_angpix: bool,
    binarize: bool,
) -> ShapeMask:
    path = Path(path)
    if output_angpix <= 0:
        raise ExtractionMaskError("Output mask Angpix must be greater than 0.")
    header = read_mrc_header(path)
    scale_to_output_px = 1.0
    if rescale_to_output_angpix:
        voxel_values = [value for value in header.voxel_size if value > 0]
        if not voxel_values:
            raise ExtractionMaskError(f"Could not determine voxel size for shape mask {path.name}.")
        voxel_size = sum(voxel_values) / len(voxel_values)
        scale_to_output_px = voxel_size / output_angpix
        if scale_to_output_px <= 0:
            raise ExtractionMaskError(f"Invalid voxel size in shape mask {path.name}.")
    active = bytearray()
    with path.open("rb") as handle:
        handle.seek(header.data_offset)
        for _z_index in range(header.nz):
            if binarize:
                active.extend(1 if value > 0 else 0 for value in _read_mrc_slice_values(handle, header))
            else:
                active.extend(
                    1 if math.isclose(value, 1.0, rel_tol=0.0, abs_tol=1e-6) else 0
                    for value in _read_mrc_slice_values(handle, header)
                )
    if not any(active):
        raise ExtractionMaskError(f"Shape mask {path.name} does not contain any positive voxels.")
    return ShapeMask(
        path=path,
        dimensions=(header.nx, header.ny, header.nz),
        center=((header.nx - 1) / 2.0, (header.ny - 1) / 2.0, (header.nz - 1) / 2.0),
        scale_to_output_px=scale_to_output_px,
        active=bytes(active),
    )


def _relion_euler_matrix(rot: float, tilt: float, psi: float) -> tuple[tuple[float, float, float], ...]:
    phi = math.radians(rot)
    theta = math.radians(tilt)
    psi_rad = math.radians(psi)
    cphi = math.cos(phi)
    sphi = math.sin(phi)
    ctheta = math.cos(theta)
    stheta = math.sin(theta)
    cpsi = math.cos(psi_rad)
    spsi = math.sin(psi_rad)
    return (
        (cpsi * ctheta * cphi - spsi * sphi, cpsi * ctheta * sphi + spsi * cphi, -cpsi * stheta),
        (-spsi * ctheta * cphi - cpsi * sphi, -spsi * ctheta * sphi + cpsi * cphi, spsi * stheta),
        (stheta * cphi, stheta * sphi, ctheta),
    )


def _matrix_transpose(matrix: tuple[tuple[float, float, float], ...]) -> tuple[tuple[float, float, float], ...]:
    return (
        (matrix[0][0], matrix[1][0], matrix[2][0]),
        (matrix[0][1], matrix[1][1], matrix[2][1]),
        (matrix[0][2], matrix[1][2], matrix[2][2]),
    )


def _matrix_vector_product(matrix: tuple[tuple[float, float, float], ...], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1] + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1] + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1] + matrix[2][2] * vector[2],
    )


def _shape_mask_active_at(shape_mask: ShapeMask, local_x: float, local_y: float, local_z: float) -> bool:
    sx, sy, sz = shape_mask.dimensions
    cx, cy, cz = shape_mask.center
    scale = shape_mask.scale_to_output_px
    if scale <= 0:
        return False
    x_index = int(round(local_x / scale + cx))
    y_index = int(round(local_y / scale + cy))
    z_index = int(round(local_z / scale + cz))
    if not (0 <= x_index < sx and 0 <= y_index < sy and 0 <= z_index < sz):
        return False
    return bool(shape_mask.active[x_index + y_index * sx + z_index * sx * sy])


def _write_mrc_header(
    handle,
    *,
    dimensions: tuple[int, int, int],
    output_angpix: float,
    mean_value: float = 1.0,
) -> None:
    nx, ny, nz = dimensions
    header = bytearray(1024)
    struct.pack_into("<4i", header, 0, nx, ny, nz, 0)
    struct.pack_into("<3i", header, 16, 0, 0, 0)
    struct.pack_into("<3i", header, 28, nx, ny, nz)
    struct.pack_into("<3f", header, 40, nx * output_angpix, ny * output_angpix, nz * output_angpix)
    struct.pack_into("<3f", header, 52, 90.0, 90.0, 90.0)
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    struct.pack_into("<3f", header, 76, 0.0, 1.0, mean_value)
    struct.pack_into("<2i", header, 88, 0, 0)
    struct.pack_into("<3f", header, 196, 0.0, 0.0, 0.0)
    header[208:212] = b"MAP "
    header[212:216] = bytes((0x44, 0x41, 0x00, 0x00))
    struct.pack_into("<f", header, 216, 0.0)
    struct.pack_into("<i", header, 220, 1)
    label = b"Created by CryoPal_tomo extraction-mask job"
    header[224 : 224 + len(label)] = label
    handle.seek(0)
    handle.write(header)


def _existing_mask_for_ts(
    output_directory: Path,
    expected_output_path: Path,
    ts_name: str,
    all_ts_names: list[str],
) -> Path | None:
    if expected_output_path.exists():
        return expected_output_path
    candidates = sorted(
        [path for path in output_directory.glob("*.mrc") if path.is_file()],
        key=lambda path: path.name.casefold(),
    )
    if not candidates:
        return None
    stems = [ts_stem_from_star_name(name) for name in all_ts_names]
    return best_matching_path_for_ts(candidates, ts_stem_from_star_name(ts_name), stems)


def write_extraction_masks(
    *,
    input_star_path: str | Path,
    output_directory: str | Path,
    input_angpix: float,
    output_angpix: float,
    cleanup_distance_angstrom: float,
    dimensions: tuple[int, int, int],
    add_to_pre_existing: bool = False,
    mask_mode: str = "radius",
    shape_mask_path: str | Path | None = None,
    binarize_shape_input: bool = False,
    rescale_shape_to_output_angpix: bool = False,
    progress_callback: Callable[[int, int, str], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
    cancel_event=None,
) -> ExtractionMaskResult:
    normalized_mode = mask_mode.strip().casefold()
    if normalized_mode not in {"radius", "shape"}:
        raise ExtractionMaskError("Mask mode must be 'radius' or 'shape'.")
    if normalized_mode == "radius" and cleanup_distance_angstrom < 0:
        raise ExtractionMaskError("Distance cleanup must be 0 or greater.")
    if normalized_mode == "shape" and not str(shape_mask_path or "").strip():
        raise ExtractionMaskError("Shape mask path is required when masking by shape.")
    nx, ny, nz = dimensions
    if nx <= 0 or ny <= 0 or nz <= 0:
        raise ExtractionMaskError("Tomogram dimensions must be positive integers.")

    input_star_path = Path(input_star_path)
    output_directory = Path(output_directory)
    output_directory.mkdir(parents=True, exist_ok=True)

    grouped = grouped_extraction_particles(
        input_star_path,
        input_angpix=input_angpix,
        output_angpix=output_angpix,
        require_angles=normalized_mode == "shape",
    )
    all_ts_names = list(grouped.keys())
    outputs: list[ExtractionMaskOutput] = []
    radius_px = cleanup_distance_angstrom / output_angpix if output_angpix > 0 else 0.0
    shape_mask = (
        _read_shape_mask(
            shape_mask_path,
            output_angpix=output_angpix,
            rescale_to_output_angpix=rescale_shape_to_output_angpix,
            binarize=binarize_shape_input,
        )
        if normalized_mode == "shape"
        else None
    )

    for ts_index, (ts_name, particles) in enumerate(grouped.items(), start=1):
        _check_cancel(cancel_event)
        if progress_callback is not None:
            progress_callback(ts_index, len(grouped), ts_name)

        destination = output_mask_path(output_directory, ts_name, output_angpix)
        existing_path = (
            _existing_mask_for_ts(output_directory, destination, ts_name, all_ts_names)
            if add_to_pre_existing
            else None
        )
        if normalized_mode == "shape" and shape_mask is not None:
            _write_single_shape_mask(
                destination=destination,
                particles=particles,
                dimensions=dimensions,
                output_angpix=output_angpix,
                shape_mask=shape_mask,
                existing_path=existing_path,
                cancel_event=cancel_event,
            )
        else:
            _write_single_mask(
                destination=destination,
                particles=particles,
                dimensions=dimensions,
                output_angpix=output_angpix,
                radius_px=radius_px,
                existing_path=existing_path,
                cancel_event=cancel_event,
            )
        if log_callback is not None:
            source_text = f" using {existing_path.name}" if existing_path is not None else ""
            mode_text = "shape" if normalized_mode == "shape" else "radius"
            log_callback(f"Wrote {destination.name} for {len(particles)} particles by {mode_text}{source_text}.")
        outputs.append(
            ExtractionMaskOutput(
                ts_name=ts_name,
                path=destination,
                particle_count=len(particles),
                used_existing_mask=existing_path is not None,
            )
        )

    return ExtractionMaskResult(
        input_star_path=input_star_path,
        output_directory=output_directory,
        dimensions=dimensions,
        input_angpix=input_angpix,
        output_angpix=output_angpix,
        cleanup_distance_angstrom=cleanup_distance_angstrom,
        outputs=outputs,
    )


def _write_single_mask(
    *,
    destination: Path,
    particles: list[ExtractionMaskParticle],
    dimensions: tuple[int, int, int],
    output_angpix: float,
    radius_px: float,
    existing_path: Path | None,
    cancel_event=None,
) -> None:
    nx, ny, nz = dimensions
    existing_header: MrcHeader | None = None
    existing_handle = None
    if existing_path is not None:
        existing_header = read_mrc_header(existing_path)
        if (existing_header.nx, existing_header.ny, existing_header.nz) != dimensions:
            raise ExtractionMaskError(
                f"Existing mask {existing_path.name} has dimensions "
                f"{existing_header.nx}x{existing_header.ny}x{existing_header.nz}, expected {nx}x{ny}x{nz}."
            )
        existing_handle = existing_path.open("rb")
        existing_handle.seek(existing_header.data_offset)

    z_buckets: dict[int, list[tuple[float, float, float, float]]] = defaultdict(list)
    radius_sq = radius_px * radius_px
    for particle in particles:
        min_z = max(0, math.ceil(particle.z - radius_px))
        max_z = min(nz - 1, math.floor(particle.z + radius_px))
        for z_index in range(min_z, max_z + 1):
            dz_sq = (z_index - particle.z) ** 2
            if dz_sq <= radius_sq:
                z_buckets[z_index].append((particle.x, particle.y, radius_sq, dz_sq))

    temp_path = Path(tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)[1])
    ones_count = 0
    try:
        with temp_path.open("wb+") as output_handle:
            _write_mrc_header(output_handle, dimensions=dimensions, output_angpix=output_angpix)
            for z_index in range(nz):
                _check_cancel(cancel_event)
                if existing_handle is not None and existing_header is not None:
                    slice_data = _read_mask_slice(existing_handle, existing_header)
                else:
                    slice_data = bytearray(b"\x01") * (nx * ny)
                for center_x, center_y, particle_radius_sq, dz_sq in z_buckets.get(z_index, []):
                    remaining_sq = particle_radius_sq - dz_sq
                    if remaining_sq < 0:
                        continue
                    xy_radius = math.sqrt(remaining_sq)
                    min_y = max(0, math.ceil(center_y - xy_radius))
                    max_y = min(ny - 1, math.floor(center_y + xy_radius))
                    for y_index in range(min_y, max_y + 1):
                        dy_sq = (y_index - center_y) ** 2
                        x_radius_sq = remaining_sq - dy_sq
                        if x_radius_sq < 0:
                            continue
                        x_radius = math.sqrt(x_radius_sq)
                        min_x = max(0, math.ceil(center_x - x_radius))
                        max_x = min(nx - 1, math.floor(center_x + x_radius))
                        row_offset = y_index * nx
                        for x_index in range(min_x, max_x + 1):
                            slice_data[row_offset + x_index] = 0
                ones_count += sum(1 for value in slice_data if value)
                output_handle.write(slice_data)
            total_voxels = nx * ny * nz
            mean_value = ones_count / total_voxels if total_voxels else 0.0
            _write_mrc_header(output_handle, dimensions=dimensions, output_angpix=output_angpix, mean_value=mean_value)
        temp_path.replace(destination)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        if existing_handle is not None:
            existing_handle.close()


def _write_single_shape_mask(
    *,
    destination: Path,
    particles: list[ExtractionMaskParticle],
    dimensions: tuple[int, int, int],
    output_angpix: float,
    shape_mask: ShapeMask,
    existing_path: Path | None,
    cancel_event=None,
) -> None:
    nx, ny, nz = dimensions
    existing_header: MrcHeader | None = None
    existing_handle = None
    if existing_path is not None:
        existing_header = read_mrc_header(existing_path)
        if (existing_header.nx, existing_header.ny, existing_header.nz) != dimensions:
            raise ExtractionMaskError(
                f"Existing mask {existing_path.name} has dimensions "
                f"{existing_header.nx}x{existing_header.ny}x{existing_header.nz}, expected {nx}x{ny}x{nz}."
            )
        existing_handle = existing_path.open("rb")
        existing_handle.seek(existing_header.data_offset)

    sx, sy, sz = shape_mask.dimensions
    cx, cy, cz = shape_mask.center
    scale = shape_mask.scale_to_output_px
    shape_radius = math.sqrt((cx * scale) ** 2 + (cy * scale) ** 2 + (cz * scale) ** 2) + max(1.0, scale)
    particle_shapes: list[tuple[ExtractionMaskParticle, tuple[tuple[float, float, float], ...]]] = [
        (particle, _matrix_transpose(_relion_euler_matrix(particle.rot, particle.tilt, particle.psi)))
        for particle in particles
    ]

    z_buckets: dict[int, list[tuple[ExtractionMaskParticle, tuple[tuple[float, float, float], ...]]]] = defaultdict(list)
    for particle, inverse_matrix in particle_shapes:
        min_z = max(0, math.floor(particle.z - shape_radius))
        max_z = min(nz - 1, math.ceil(particle.z + shape_radius))
        for z_index in range(min_z, max_z + 1):
            z_buckets[z_index].append((particle, inverse_matrix))

    temp_path = Path(tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)[1])
    ones_count = 0
    try:
        with temp_path.open("wb+") as output_handle:
            _write_mrc_header(output_handle, dimensions=dimensions, output_angpix=output_angpix)
            for z_index in range(nz):
                _check_cancel(cancel_event)
                if existing_handle is not None and existing_header is not None:
                    slice_data = _read_mask_slice(existing_handle, existing_header)
                else:
                    slice_data = bytearray(b"\x01") * (nx * ny)
                for particle, inverse_matrix in z_buckets.get(z_index, []):
                    dz = z_index - particle.z
                    min_y = max(0, math.floor(particle.y - shape_radius))
                    max_y = min(ny - 1, math.ceil(particle.y + shape_radius))
                    min_x = max(0, math.floor(particle.x - shape_radius))
                    max_x = min(nx - 1, math.ceil(particle.x + shape_radius))
                    for y_index in range(min_y, max_y + 1):
                        dy = y_index - particle.y
                        row_offset = y_index * nx
                        for x_index in range(min_x, max_x + 1):
                            dx = x_index - particle.x
                            local = _matrix_vector_product(inverse_matrix, (dx, dy, dz))
                            if _shape_mask_active_at(shape_mask, *local):
                                slice_data[row_offset + x_index] = 0
                ones_count += sum(1 for value in slice_data if value)
                output_handle.write(slice_data)
            total_voxels = nx * ny * nz
            mean_value = ones_count / total_voxels if total_voxels else 0.0
            _write_mrc_header(output_handle, dimensions=dimensions, output_angpix=output_angpix, mean_value=mean_value)
        temp_path.replace(destination)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        if existing_handle is not None:
            existing_handle.close()
