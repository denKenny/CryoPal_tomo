from __future__ import annotations

import math
import re
import shlex
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


class StarMergeError(RuntimeError):
    pass


class OperationAborted(RuntimeError):
    pass


def _check_cancel(cancel_event=None) -> None:
    if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
        raise OperationAborted("Aborted by user.")


@dataclass
class StarBlock:
    name: str
    kind: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    values: list[tuple[str, str]] = field(default_factory=list)

    def clone(self) -> "StarBlock":
        return StarBlock(
            name=self.name,
            kind=self.kind,
            headers=list(self.headers),
            rows=[list(row) for row in self.rows],
            values=list(self.values),
        )


@dataclass
class StarDocument:
    blocks: list[StarBlock]

    def block(self, name: str) -> StarBlock:
        for block in self.blocks:
            if block.name == name:
                return block
        raise StarMergeError(f"Missing STAR block: {name}")


@dataclass
class MergeResult:
    merged_particles_path: Path
    merged_tomograms_path: Path | None = None
    merged_optimisation_set_path: Path | None = None


@dataclass
class SplitResult:
    mode: str
    image_pixel_size: float
    output_paths: list[Path] = field(default_factory=list)
    ts_names: list[str] = field(default_factory=list)


@dataclass
class DistanceCleanOutputs:
    mode: str
    image_pixel_size: float
    cleaned_path: Path | None = None
    duplicates_path: Path | None = None
    total_particles: int = 0
    considered_particles: int = 0
    cleaned_count: int = 0
    duplicate_count: int = 0


@dataclass
class IntersectOutputs:
    mode: str
    image_pixel_size: float
    common_path: Path | None = None
    unique_paths: list[Path] = field(default_factory=list)
    total_particles_per_file: list[int] = field(default_factory=list)
    considered_particles_per_file: list[int] = field(default_factory=list)
    common_particles_per_file: list[int] = field(default_factory=list)
    unique_particles_per_file: list[int] = field(default_factory=list)
    common_total: int = 0


@dataclass
class AbundanceCondition:
    label: str
    values: list[float] = field(default_factory=list)
    pooled_total: int = 0
    tomogram_count: int = 0
    dataset_count: int = 0


@dataclass
class ParticleAbundancePlot:
    star_path: Path
    mode: str
    measure: str
    compare_samples: bool
    conditions: list[AbundanceCondition] = field(default_factory=list)
    all_condition: AbundanceCondition | None = None


@dataclass
class ClassificationIteration:
    iteration: int
    class_counts: dict[str, int] = field(default_factory=dict)
    changed_count: int = 0
    particle_count: int = 0


@dataclass
class ParticleClassificationConvergencePlot:
    directory: Path
    mode: str
    pixel_size: float
    iterations: list[ClassificationIteration] = field(default_factory=list)
    class_labels: list[str] = field(default_factory=list)
    particle_count: int = 0
    dataset_count: int = 0
    tomogram_count: int = 0


@dataclass
class _ClassificationStarSummary:
    mode: str
    pixel_size: float
    assignments: dict[tuple[str, str], str] = field(default_factory=dict)
    class_counts: dict[str, int] = field(default_factory=dict)
    matched_dataset_names: set[str] = field(default_factory=set)
    matched_tomograms: set[str] = field(default_factory=set)


@dataclass
class _ParticleAbundanceSummary:
    mode: str
    counts_by_dataset: dict[str, dict[str, int]] = field(default_factory=dict)


_CLASSIFICATION_CONVERGENCE_CACHE: dict[
    tuple[str, int, tuple[str, ...]],
    ParticleClassificationConvergencePlot,
] = {}
_PARTICLE_ABUNDANCE_SUMMARY_CACHE: dict[
    tuple[str, int, tuple[str, ...]],
    _ParticleAbundanceSummary,
] = {}


def parse_star(path: str | Path) -> StarDocument:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    blocks: list[StarBlock] = []
    index = 0

    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        if not stripped.startswith("data_"):
            index += 1
            continue

        name = stripped
        index += 1
        while index < len(lines) and (not lines[index].strip() or lines[index].strip().startswith("#")):
            index += 1

        if index < len(lines) and lines[index].strip() == "loop_":
            index += 1
            headers: list[str] = []
            while index < len(lines):
                stripped = lines[index].strip()
                if stripped.startswith("_"):
                    headers.append(stripped.split()[0])
                    index += 1
                    continue
                break

            rows: list[list[str]] = []
            while index < len(lines):
                stripped = lines[index].strip()
                if not stripped:
                    index += 1
                    break
                if stripped.startswith("#"):
                    index += 1
                    continue
                if stripped.startswith("data_") or stripped == "loop_" or stripped.startswith("_"):
                    break
                rows.append(shlex.split(lines[index], posix=True))
                index += 1

            blocks.append(StarBlock(name=name, kind="loop", headers=headers, rows=rows))
            continue

        values: list[tuple[str, str]] = []
        while index < len(lines):
            stripped = lines[index].strip()
            if not stripped:
                index += 1
                break
            if stripped.startswith("#"):
                index += 1
                continue
            if stripped.startswith("data_") or stripped == "loop_":
                break
            if stripped.startswith("_"):
                parts = stripped.split(None, 1)
                values.append((parts[0], parts[1] if len(parts) > 1 else ""))
            index += 1

        blocks.append(StarBlock(name=name, kind="values", values=values))

    return StarDocument(blocks=blocks)


def write_star(path: str | Path, document: StarDocument) -> Path:
    output_lines: list[str] = []

    for block in document.blocks:
        output_lines.append(block.name)
        output_lines.append("")
        if block.kind == "loop":
            output_lines.append("loop_")
            for offset, header in enumerate(block.headers, start=1):
                output_lines.append(f"{header} #{offset}")
            for row in block.rows:
                output_lines.append("  " + "  ".join(row))
        else:
            for key, value in block.values:
                output_lines.append(f"{key}   {value}".rstrip())
        output_lines.append("")
        output_lines.append("")

    output_path = Path(path)
    output_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    return output_path


def merge_particle_exports(output_paths: list[str | Path], merged_output_path: str | Path, is_2d: bool) -> MergeResult:
    particle_paths = [Path(path) for path in output_paths]
    if not particle_paths:
        raise StarMergeError("No particle STAR files were provided for merging.")

    merged_particles_path = Path(merged_output_path)
    remaps = _merge_particle_stars(particle_paths, merged_particles_path)
    result = MergeResult(merged_particles_path=merged_particles_path)

    if is_2d:
        tomogram_paths = [path.with_name(f"{path.stem}_tomograms.star") for path in particle_paths]
        optimisation_paths = [path.with_name(f"{path.stem}_optimisation_set.star") for path in particle_paths]
        merged_tomograms_path = merged_particles_path.with_name(
            f"{merged_particles_path.stem}_tomograms{merged_particles_path.suffix}"
        )
        merged_optimisation_path = merged_particles_path.with_name(
            f"{merged_particles_path.stem}_optimisation_set{merged_particles_path.suffix}"
        )
        _merge_tomogram_stars(tomogram_paths, merged_tomograms_path, remaps)
        _merge_optimisation_set(
            optimisation_paths[0],
            merged_optimisation_path,
            merged_particles_path.name,
            merged_tomograms_path.name,
        )
        result.merged_tomograms_path = merged_tomograms_path
        result.merged_optimisation_set_path = merged_optimisation_path

    return result


def merge_particle_star_files(
    input_star_paths: list[str | Path],
    output_path: str | Path,
    log_callback=None,
    cancel_event=None,
) -> MergeResult:
    star_paths = [Path(path) for path in input_star_paths if str(path).strip()]
    if len(star_paths) < 2:
        raise StarMergeError("Please provide at least two STAR files for merging.")

    if log_callback is not None:
        log_callback("Read in .star file metadata")

    modes: list[str] = []
    pixel_sizes: list[float] = []
    for path in star_paths:
        _check_cancel(cancel_event)
        mode = detect_particle_star_mode(path)
        pixel_size = particle_star_pixel_size(path)
        modes.append(mode)
        pixel_sizes.append(pixel_size)
        if log_callback is not None:
            log_callback(f"{path.name}: mode={mode}, pixel size={pixel_size:.4f} A")

    if len(set(modes)) != 1:
        raise StarMergeError("All input STAR files must have the same type, either all 2D or all 3D.")
    rounded_pixel_sizes = {round(value, 6) for value in pixel_sizes}
    if len(rounded_pixel_sizes) != 1:
        raise StarMergeError("All input STAR files must have the same image pixel size.")

    if log_callback is not None:
        log_callback("Merging compatible STAR files")

    merged_particles_path = Path(output_path)
    _merge_particle_stars(star_paths, merged_particles_path)
    if log_callback is not None:
        log_callback(f"Wrote merged STAR file: {merged_particles_path}")
    return MergeResult(merged_particles_path=merged_particles_path)


def split_particle_star_file(
    input_star_path: str | Path,
    output_directory: str | Path,
    output_name_stem: str,
    log_callback=None,
    cancel_event=None,
) -> SplitResult:
    input_path = Path(input_star_path)
    output_dir = Path(output_directory)
    output_dir.mkdir(parents=True, exist_ok=True)

    if log_callback is not None:
        log_callback("Read in .star file")
    document = parse_star(input_path)
    mode = detect_particle_star_mode(input_path)
    pixel_size = particle_star_pixel_size(input_path)
    particles_block = document.block("data_particles")
    identifier_header = "_rlnTomoName" if mode == "2d" else "_rlnMicrographName"
    identifier_index = _header_index(particles_block.headers, identifier_header)

    grouped_rows: dict[str, list[list[str]]] = defaultdict(list)
    for row in particles_block.rows:
        _check_cancel(cancel_event)
        identifier = row[identifier_index]
        ts_name = Path(Path(identifier).name).stem
        grouped_rows[ts_name].append(list(row))

    if not grouped_rows:
        raise StarMergeError("The selected STAR file contains no particle rows to split.")

    stem = Path(_ensure_star_name(output_name_stem)).stem
    output_paths: list[Path] = []
    for ts_name in sorted(grouped_rows.keys(), key=str.casefold):
        _check_cancel(cancel_event)
        output_path = output_dir / f"{stem}_{ts_name}.star"
        write_star(output_path, _with_particle_rows(document, grouped_rows[ts_name]))
        output_paths.append(output_path)
        if log_callback is not None:
            log_callback(f"Wrote {output_path.name} ({len(grouped_rows[ts_name])} particles)")

    return SplitResult(
        mode=mode,
        image_pixel_size=pixel_size,
        output_paths=output_paths,
        ts_names=sorted(grouped_rows.keys(), key=str.casefold),
    )


def detect_particle_star_mode(path: str | Path) -> str:
    document = parse_star(path)
    particles_block = document.block("data_particles")
    if "_rlnTomoName" in particles_block.headers:
        return "2d"
    if "_rlnMicrographName" in particles_block.headers:
        return "3d"
    raise StarMergeError("Could not detect whether the particle STAR file is 2D or 3D.")


def particle_star_pixel_size(path: str | Path) -> float:
    document = parse_star(path)
    optics_block = document.block("data_optics")
    pixel_header = "_rlnImagePixelSize"
    if pixel_header not in optics_block.headers:
        raise StarMergeError("The particle STAR file does not contain _rlnImagePixelSize in data_optics.")
    if not optics_block.rows:
        raise StarMergeError("The particle STAR file has no optics rows to read _rlnImagePixelSize from.")
    index = optics_block.headers.index(pixel_header)
    return float(optics_block.rows[0][index])


def distance_clean_particles(
    input_star_path: str | Path,
    dataset_names: list[str],
    radius_px: float,
    output_name: str,
    write_cleaned: bool,
    write_duplicates: bool,
    log_callback=None,
    cancel_event=None,
) -> DistanceCleanOutputs:
    if radius_px <= 0:
        raise StarMergeError("Clearing radius must be greater than 0.")
    if not write_cleaned and not write_duplicates:
        raise StarMergeError("Please select at least one output STAR mode.")

    input_path = Path(input_star_path)
    if log_callback is not None:
        log_callback("Read in .star file")
    document = parse_star(input_path)
    particles_block = document.block("data_particles")
    mode = detect_particle_star_mode(input_path)
    identifier_header = "_rlnTomoName" if mode == "2d" else "_rlnMicrographName"
    identifier_index = _header_index(particles_block.headers, identifier_header)
    x_index = _header_index(particles_block.headers, "_rlnCoordinateX")
    y_index = _header_index(particles_block.headers, "_rlnCoordinateY")
    z_index = _header_index(particles_block.headers, "_rlnCoordinateZ")

    dataset_names = [name for name in dataset_names if name]
    selected_indices: list[int] = []
    selected_by_identifier: dict[str, list[int]] = {}

    for index, row in enumerate(particles_block.rows):
        _check_cancel(cancel_event)
        identifier = row[identifier_index]
        if _matches_any_dataset(identifier, dataset_names):
            selected_indices.append(index)
            selected_by_identifier.setdefault(identifier, []).append(index)
    if log_callback is not None:
        log_callback(
            f"1) Considering {len(selected_indices)} particles from {len(particles_block.rows)} in total"
        )

    duplicate_indices: set[int] = set()
    for row_indices in selected_by_identifier.values():
        _check_cancel(cancel_event)
        duplicate_indices.update(
            _distance_clean_per_tomo(
                row_indices, particles_block, x_index, y_index, z_index, radius_px, cancel_event=cancel_event
            )
        )
    if log_callback is not None:
        log_callback(f"2) Identified {len(duplicate_indices)} duplicate particles")

    selected_index_set = set(selected_indices)
    cleaned_rows = [list(particles_block.rows[index]) for index in selected_indices if index not in duplicate_indices]
    duplicate_rows = [list(particles_block.rows[index]) for index in selected_indices if index in duplicate_indices]

    output_base = _ensure_star_name(output_name)
    output_stem = Path(output_base).stem
    output_dir = input_path.parent
    cleaned_path = output_dir / f"{output_stem}_cleaned.star" if write_cleaned else None
    duplicates_path = output_dir / f"{output_stem}_duplicates.star" if write_duplicates else None

    if log_callback is not None:
        log_callback(
            f"3) Found {len(cleaned_rows)} cleaned particles and {len(duplicate_rows)} duplicates"
        )
        log_callback("Writing out .star file")
    if cleaned_path is not None:
        write_star(cleaned_path, _with_particle_rows(document, cleaned_rows))
    if duplicates_path is not None:
        write_star(duplicates_path, _with_particle_rows(document, duplicate_rows))

    return DistanceCleanOutputs(
        mode=mode,
        image_pixel_size=particle_star_pixel_size(input_path),
        cleaned_path=cleaned_path,
        duplicates_path=duplicates_path,
        total_particles=len(particles_block.rows),
        considered_particles=len(selected_indices),
        cleaned_count=len(cleaned_rows),
        duplicate_count=len(duplicate_rows),
    )


def intersect_particle_stars(
    input_star_paths: list[str | Path],
    dataset_names: list[str],
    output_name: str,
    write_common: bool,
    write_unique: bool,
    identification_mode: str,
    radius_ang: float = 0.0,
    log_callback=None,
    cancel_event=None,
) -> IntersectOutputs:
    star_paths = [Path(path) for path in input_star_paths if str(path).strip()]
    if len(star_paths) < 2:
        raise StarMergeError("Please provide at least two STAR files for intersection.")
    if not write_common and not write_unique:
        raise StarMergeError("Please select at least one output STAR mode.")
    if identification_mode not in {"distance", "name"}:
        raise StarMergeError("Identification mode must be either 'distance' or 'name'.")
    if identification_mode == "distance" and radius_ang <= 0:
        raise StarMergeError("Distance in A must be greater than 0 for distance-based intersection.")

    if log_callback is not None:
        log_callback("Read in .star file(s)")

    parsed = [_prepare_particle_document(path, dataset_names, cancel_event=cancel_event) for path in star_paths]
    modes = {item["mode"] for item in parsed}
    if write_common and len(modes) != 1:
        raise StarMergeError("All input STAR files must have the same mode, either all 2D or all 3D.")
    mode = parsed[0]["mode"] if len(modes) == 1 else "mixed"
    pixel_size = parsed[0]["pixel_size"]

    total_per_file = [len(item["particles_block"].rows) for item in parsed]
    considered_per_file = [len(item["selected_indices"]) for item in parsed]
    if log_callback is not None:
        for path, total_count, considered_count in zip(star_paths, total_per_file, considered_per_file):
            log_callback(
                f"1) {path.name}: considering {considered_count} particles from {total_count} in total"
            )

    if write_common:
        common_by_file = _intersect_selected_particles(parsed, identification_mode, radius_ang, cancel_event=cancel_event)
    else:
        common_by_file = [set() for _ in parsed]
    common_counts = [len(indices) for indices in common_by_file]
    unique_counts = [considered - common for considered, common in zip(considered_per_file, common_counts)]
    common_total = common_counts[0] if common_counts else 0

    if log_callback is not None:
        for path, common_count, unique_count in zip(star_paths, common_counts, unique_counts):
            log_callback(
                f"2) {path.name}: found {common_count} common particles and {unique_count} unique particles"
            )
        log_callback(
            f"3) Total common particles across all files: {common_total}; total unique particles across all files: {sum(unique_counts)}"
        )
        log_callback("Writing out .star file")

    output_base = _ensure_star_name(output_name)
    output_stem = Path(output_base).stem
    output_dir = star_paths[0].parent
    common_path = output_dir / f"{output_stem}_common.star" if write_common else None
    unique_paths: list[Path] = []

    if common_path is not None:
        first_item = parsed[0]
        common_rows = [
            list(first_item["particles_block"].rows[index]) for index in sorted(common_by_file[0])
        ]
        write_star(common_path, _with_particle_rows(first_item["document"], common_rows))

    if write_unique:
        for item, common_indices in zip(parsed, common_by_file):
            unique_rows = [
                list(item["particles_block"].rows[index])
                for index in item["selected_indices"]
                if index not in common_indices
            ]
            unique_path = output_dir / f"{output_stem}_unique_{item['path'].stem}.star"
            write_star(unique_path, _with_particle_rows(item["document"], unique_rows))
            unique_paths.append(unique_path)

    return IntersectOutputs(
        mode=mode,
        image_pixel_size=pixel_size,
        common_path=common_path,
        unique_paths=unique_paths,
        total_particles_per_file=total_per_file,
        considered_particles_per_file=considered_per_file,
        common_particles_per_file=common_counts,
        unique_particles_per_file=unique_counts,
        common_total=common_total,
    )


def particle_abundance_plot_data(
    input_star_path: str | Path,
    dataset_to_sample: dict[str, str],
    compare_samples: bool,
    measure: str,
    cancel_event=None,
) -> ParticleAbundancePlot:
    if measure not in {"total", "density"}:
        raise StarMergeError("Measure must be either 'total' or 'density'.")

    input_path = Path(input_star_path)
    counts_by_dataset: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    ordered_dataset_names = sorted(dataset_to_sample.keys(), key=lambda value: (-len(value), value.casefold()))
    summary = _read_particle_abundance_summary(
        input_path,
        ordered_dataset_names,
        cancel_event=cancel_event,
    )
    mode = summary.mode
    counts_by_dataset = defaultdict(lambda: defaultdict(int))
    for dataset_name, tomo_counts in summary.counts_by_dataset.items():
        counts_by_dataset[dataset_name].update(tomo_counts)

    if not counts_by_dataset:
        raise StarMergeError("No particles in the STAR file matched any loaded dataset names.")

    grouped_counts: dict[str, list[int]] = defaultdict(list)
    grouped_dataset_totals: dict[str, list[int]] = defaultdict(list)
    grouped_tomogram_count: dict[str, int] = defaultdict(int)

    for dataset_name, tomo_counts in counts_by_dataset.items():
        values = list(tomo_counts.values())
        if not values:
            continue
        condition = dataset_to_sample.get(dataset_name, dataset_name) if compare_samples else dataset_name
        grouped_counts[condition].extend(values)
        grouped_dataset_totals[condition].append(sum(values))
        grouped_tomogram_count[condition] += len(values)

    if not grouped_counts:
        raise StarMergeError("No matching datasets with particle counts were available for plotting.")

    conditions: list[AbundanceCondition] = []
    for label in sorted(grouped_counts.keys(), key=str.casefold):
        tomo_values = grouped_counts[label]
        dataset_totals = grouped_dataset_totals[label]
        plotted_values = [float(value) for value in (dataset_totals if measure == "total" else tomo_values)]
        conditions.append(
            AbundanceCondition(
                label=label,
                values=plotted_values,
                pooled_total=sum(tomo_values),
                tomogram_count=grouped_tomogram_count[label],
                dataset_count=len(dataset_totals),
            )
        )

    all_tomo_values = [value for condition in conditions for value in grouped_counts[condition.label]]
    all_dataset_totals = [value for condition in conditions for value in grouped_dataset_totals[condition.label]]
    all_condition = AbundanceCondition(
        label="All",
        values=[float(value) for value in (all_dataset_totals if measure == "total" else all_tomo_values)],
        pooled_total=sum(all_tomo_values),
        tomogram_count=sum(condition.tomogram_count for condition in conditions),
        dataset_count=sum(condition.dataset_count for condition in conditions),
    )

    return ParticleAbundancePlot(
        star_path=input_path,
        mode=mode,
        measure=measure,
        compare_samples=compare_samples,
        conditions=conditions,
        all_condition=all_condition,
    )


def _read_particle_abundance_summary(
    path: Path,
    dataset_names: list[str],
    cancel_event=None,
) -> _ParticleAbundanceSummary:
    cache_key = (
        str(path.resolve()),
        path.stat().st_mtime_ns,
        tuple(dataset_names),
    )
    cached = _PARTICLE_ABUNDANCE_SUMMARY_CACHE.get(cache_key)
    if cached is not None:
        return cached

    matcher = _dataset_matcher(dataset_names)
    current_block = ""
    in_loop = False
    headers: list[str] = []
    mode: str | None = None
    identifier_index: int | None = None
    counts_by_dataset: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            _check_cancel(cancel_event)
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("data_"):
                current_block = stripped
                in_loop = False
                headers = []
                continue
            if stripped == "loop_":
                in_loop = True
                headers = []
                continue
            if not in_loop:
                continue
            if stripped.startswith("_"):
                headers.append(stripped.split()[0])
                continue
            if current_block != "data_particles":
                continue

            if mode is None:
                if "_rlnTomoName" in headers:
                    mode = "2d"
                elif "_rlnMicrographName" in headers:
                    mode = "3d"
                else:
                    raise StarMergeError("Could not detect whether the particle STAR file is 2D or 3D.")
                identifier_index = _header_index(
                    headers,
                    "_rlnTomoName" if mode == "2d" else "_rlnMicrographName",
                )

            assert identifier_index is not None
            columns = stripped.split()
            if identifier_index >= len(columns):
                continue
            identifier = columns[identifier_index]
            dataset_name = matcher(identifier)
            if dataset_name is None:
                continue
            counts_by_dataset[dataset_name][identifier] += 1

    if mode is None:
        raise StarMergeError("Could not detect whether the particle STAR file is 2D or 3D.")

    summary = _ParticleAbundanceSummary(
        mode=mode,
        counts_by_dataset={dataset: dict(tomos) for dataset, tomos in counts_by_dataset.items()},
    )
    _PARTICLE_ABUNDANCE_SUMMARY_CACHE[cache_key] = summary
    return summary


def particle_classification_convergence_data(
    input_dir: str | Path,
    dataset_names: list[str],
    cancel_event=None,
) -> ParticleClassificationConvergencePlot:
    directory = Path(input_dir)
    if not directory.is_dir():
        raise StarMergeError("Please provide a valid classification directory.")

    iteration_files: list[tuple[int, Path]] = []
    for path in directory.glob("run_it*_data.star"):
        match = re.fullmatch(r"run_it(\d{3})_data\.star", path.name)
        if match is None:
            continue
        iteration = int(match.group(1))
        if iteration <= 0:
            continue
        iteration_files.append((iteration, path))
    iteration_files.sort(key=lambda item: item[0])

    if not iteration_files:
        raise StarMergeError("No run_it???_data.star files were found in the selected directory.")

    ordered_dataset_names = sorted(
        [name for name in dataset_names if name],
        key=lambda value: (-len(value), value.casefold()),
    )
    if not ordered_dataset_names:
        raise StarMergeError("No datasets are currently loaded in the project catalog.")

    cache_key = (
        str(directory.resolve()),
        max(path.stat().st_mtime_ns for _iteration, path in iteration_files),
        tuple(ordered_dataset_names),
    )
    cached = _CLASSIFICATION_CONVERGENCE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    matcher = _dataset_matcher(ordered_dataset_names)

    iteration_summaries: list[ClassificationIteration] = []
    class_labels: set[str] = set()
    matched_dataset_names: set[str] = set()
    matched_tomograms: set[str] = set()
    previous_assignments: dict[tuple[str, str], str] | None = None
    mode: str | None = None
    pixel_size = 0.0

    for iteration, path in iteration_files:
        _check_cancel(cancel_event)
        summary = _read_classification_star_summary(
            path,
            matcher=matcher,
            expected_mode=mode,
            cancel_event=cancel_event,
        )
        if mode is None:
            mode = summary.mode
            pixel_size = summary.pixel_size

        assignments = summary.assignments
        class_counts = summary.class_counts
        class_labels.update(class_counts.keys())
        matched_dataset_names.update(summary.matched_dataset_names)
        matched_tomograms.update(summary.matched_tomograms)

        if previous_assignments is None:
            changed_count = 0
        else:
            changed_count = sum(
                1
                for key, class_number in assignments.items()
                if key in previous_assignments and previous_assignments[key] != class_number
            )

        iteration_summaries.append(
            ClassificationIteration(
                iteration=iteration,
                class_counts=dict(class_counts),
                changed_count=changed_count,
                particle_count=len(assignments),
            )
        )
        previous_assignments = assignments

    if not any(summary.particle_count for summary in iteration_summaries):
        raise StarMergeError("No particles in the classification STAR files matched any loaded dataset names.")

    def class_sort_key(value: str):
        stripped = value.strip()
        if stripped.isdigit():
            return (0, int(stripped))
        return (1, stripped.casefold())

    plot = ParticleClassificationConvergencePlot(
        directory=directory,
        mode=mode or "",
        pixel_size=pixel_size,
        iterations=iteration_summaries,
        class_labels=sorted(class_labels, key=class_sort_key),
        particle_count=iteration_summaries[-1].particle_count if iteration_summaries else 0,
        dataset_count=len(matched_dataset_names),
        tomogram_count=len(matched_tomograms),
    )
    _CLASSIFICATION_CONVERGENCE_CACHE[cache_key] = plot
    return plot


def _merge_particle_stars(
    particle_paths: list[Path],
    merged_output_path: Path,
) -> dict[Path, dict[str, dict[str, str]]]:
    parsed = [(path, parse_star(path)) for path in particle_paths]
    optics_headers = _merged_headers(
        [document.block("data_optics").headers for _path, document in parsed]
    )
    particle_headers = _merged_headers(
        [document.block("data_particles").headers for _path, document in parsed]
    )
    merged_optics_rows: list[list[str]] = []
    merged_particle_rows: list[list[str]] = []
    remaps: dict[Path, dict[str, dict[str, str]]] = {}
    next_group = 1

    first_document = parsed[0][1]
    optics_group_index = _header_index(optics_headers, "_rlnOpticsGroup")
    optics_name_index = _header_index(optics_headers, "_rlnOpticsGroupName", required=False)
    particle_group_index = _header_index(particle_headers, "_rlnOpticsGroup", required=False)
    particle_id_index = _header_index(particle_headers, "_rlnTomoParticleId", required=False)
    next_particle_id = 1

    for path, document in parsed:
        optics_block = document.block("data_optics")
        particle_block = document.block("data_particles")
        optics_row_map = _row_values_by_header(optics_block.headers, optics_headers)
        particle_row_map = _row_values_by_header(particle_block.headers, particle_headers)
        local_optics_group_index = _header_index(optics_block.headers, "_rlnOpticsGroup")
        local_optics_name_index = _header_index(optics_block.headers, "_rlnOpticsGroupName", required=False)
        local_particle_group_index = _header_index(particle_block.headers, "_rlnOpticsGroup", required=False)
        local_particle_id_index = _header_index(particle_block.headers, "_rlnTomoParticleId", required=False)

        group_map: dict[str, str] = {}
        name_map: dict[str, str] = {}

        for row in optics_block.rows:
            updated = optics_row_map(row)
            old_group = row[local_optics_group_index]
            new_group = str(next_group)
            group_map[old_group] = new_group
            updated[optics_group_index] = new_group
            if optics_name_index is not None:
                old_name = row[local_optics_name_index] if local_optics_name_index is not None else ""
                new_name = f"opticsGroup{next_group}"
                if old_name:
                    name_map[old_name] = new_name
                updated[optics_name_index] = new_name
            merged_optics_rows.append(updated)
            next_group += 1

        remaps[path] = {"groups": group_map, "names": name_map}

        for row in particle_block.rows:
            updated = particle_row_map(row)
            if particle_group_index is not None and local_particle_group_index is not None:
                old_group = row[local_particle_group_index]
                updated[particle_group_index] = group_map.get(old_group, old_group)
            if particle_id_index is not None and local_particle_id_index is not None:
                updated[particle_id_index] = str(next_particle_id)
                next_particle_id += 1
            merged_particle_rows.append(updated)

    merged_document = _replace_blocks(
        first_document,
        {
            "data_optics": StarBlock(
                name="data_optics",
                kind="loop",
                headers=list(optics_headers),
                rows=merged_optics_rows,
            ),
            "data_particles": StarBlock(
                name="data_particles",
                kind="loop",
                headers=list(particle_headers),
                rows=merged_particle_rows,
            ),
        },
    )
    write_star(merged_output_path, merged_document)
    return remaps


def _merge_tomogram_stars(
    tomogram_paths: list[Path],
    merged_output_path: Path,
    remaps: dict[Path, dict[str, dict[str, str]]],
) -> None:
    parsed = [(path, parse_star(path)) for path in tomogram_paths]
    first_document = parsed[0][1]
    global_template = first_document.block("data_global")
    global_rows: list[list[str]] = []
    trailing_blocks: list[StarBlock] = []
    optics_name_index = _header_index(global_template.headers, "_rlnOpticsGroupName", required=False)

    for path, document in parsed:
        global_block = document.block("data_global")
        name_map = remaps.get(path.with_name(path.name.replace("_tomograms.star", ".star")), {}).get("names", {})
        for row in global_block.rows:
            updated = list(row)
            if optics_name_index is not None:
                updated[optics_name_index] = name_map.get(row[optics_name_index], row[optics_name_index])
            global_rows.append(updated)
        for block in document.blocks:
            if block.name != "data_global":
                trailing_blocks.append(block.clone())

    merged_blocks = [
        StarBlock(
            name="data_global",
            kind="loop",
            headers=list(global_template.headers),
            rows=global_rows,
        )
    ]
    merged_blocks.extend(trailing_blocks)
    write_star(merged_output_path, StarDocument(blocks=merged_blocks))


def _merge_optimisation_set(
    template_path: Path,
    merged_output_path: Path,
    merged_particles_name: str,
    merged_tomograms_name: str,
) -> None:
    document = parse_star(template_path)
    for block in document.blocks:
        if block.kind != "values":
            continue
        updated_values: list[tuple[str, str]] = []
        for key, value in block.values:
            if key == "_rlnTomoParticlesFile":
                updated_values.append((key, merged_particles_name))
            elif key == "_rlnTomoTomogramsFile":
                updated_values.append((key, merged_tomograms_name))
            else:
                updated_values.append((key, value))
        block.values = updated_values
    write_star(merged_output_path, document)


def _replace_blocks(document: StarDocument, replacements: dict[str, StarBlock]) -> StarDocument:
    blocks: list[StarBlock] = []
    inserted: set[str] = set()
    for block in document.blocks:
        replacement = replacements.get(block.name)
        if replacement is not None:
            if block.name not in inserted:
                blocks.append(replacement)
                inserted.add(block.name)
            continue
        blocks.append(block.clone())
    for name, replacement in replacements.items():
        if name not in inserted:
            blocks.append(replacement)
    return StarDocument(blocks=blocks)


def _merged_headers(header_sets: list[list[str]]) -> list[str]:
    merged: list[str] = []
    for headers in header_sets:
        for header in headers:
            if header not in merged:
                merged.append(header)
    return merged


def _row_values_by_header(source_headers: list[str], merged_headers: list[str]) -> Callable[[list[str]], list[str]]:
    source_index = {header: index for index, header in enumerate(source_headers)}

    def map_row(row: list[str]) -> list[str]:
        values: list[str] = []
        for header in merged_headers:
            index = source_index.get(header)
            values.append(row[index] if index is not None and index < len(row) else "")
        return values

    return map_row


def _with_particle_rows(document: StarDocument, rows: list[list[str]]) -> StarDocument:
    particles_block = document.block("data_particles")
    return _replace_blocks(
        document,
        {
            "data_particles": StarBlock(
                name="data_particles",
                kind="loop",
                headers=list(particles_block.headers),
                rows=rows,
            )
        },
    )


def _ensure_star_name(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise StarMergeError("Please provide an output STAR name.")
    if stripped.endswith(".star"):
        return stripped
    return f"{stripped}.star"


def _matches_any_dataset(identifier: str, dataset_names: list[str]) -> bool:
    token = Path(identifier).name
    stem = Path(token).stem
    for dataset_name in dataset_names:
        if token.startswith(dataset_name) or stem.startswith(dataset_name):
            return True
    return False


def _matched_dataset_name(identifier: str, dataset_names: list[str]) -> str | None:
    token = Path(identifier).name
    stem = Path(token).stem
    for dataset_name in dataset_names:
        if token.startswith(dataset_name) or stem.startswith(dataset_name):
            return dataset_name
    return None


def _dataset_matcher(dataset_names: list[str]) -> Callable[[str], str | None]:
    cache: dict[str, str | None] = {}

    def match(identifier: str) -> str | None:
        if identifier in cache:
            return cache[identifier]
        dataset_name = _matched_dataset_name(identifier, dataset_names)
        cache[identifier] = dataset_name
        return dataset_name

    return match


def _read_classification_star_summary(
    path: Path,
    *,
    matcher: Callable[[str], str | None],
    expected_mode: str | None = None,
    cancel_event=None,
) -> _ClassificationStarSummary:
    current_block = ""
    in_loop = False
    headers: list[str] = []
    pixel_size: float | None = None
    mode: str | None = None
    identifier_index: int | None = None
    key_index: int | None = None
    class_index: int | None = None
    assignments: dict[tuple[str, str], str] = {}
    class_counts: dict[str, int] = defaultdict(int)
    matched_dataset_names: set[str] = set()
    matched_tomograms: set[str] = set()

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            _check_cancel(cancel_event)
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("data_"):
                current_block = stripped
                in_loop = False
                headers = []
                continue
            if stripped == "loop_":
                in_loop = True
                headers = []
                continue
            if not in_loop:
                continue
            if stripped.startswith("_"):
                headers.append(stripped.split()[0])
                continue

            columns = stripped.split()
            if current_block == "data_optics":
                if pixel_size is None:
                    pixel_column = _header_index(headers, "_rlnImagePixelSize", required=False)
                    if pixel_column is not None and pixel_column < len(columns):
                        pixel_size = float(columns[pixel_column])
                continue

            if current_block != "data_particles":
                continue

            if mode is None:
                if "_rlnTomoName" in headers:
                    mode = "2d"
                elif "_rlnMicrographName" in headers:
                    mode = "3d"
                else:
                    raise StarMergeError("Could not detect whether the classification STAR file is 2D or 3D.")
                if expected_mode is not None and mode != expected_mode:
                    raise StarMergeError(
                        "All classification STAR files must use the same mode, either all 2D or all 3D."
                    )
                identifier_index = _header_index(
                    headers,
                    "_rlnTomoName" if mode == "2d" else "_rlnMicrographName",
                )
                key_index = _header_index(headers, _name_key_header(mode))
                class_index = _header_index(headers, "_rlnClassNumber")

            assert identifier_index is not None
            assert key_index is not None
            assert class_index is not None
            if max(identifier_index, key_index, class_index) >= len(columns):
                continue

            identifier = columns[identifier_index]
            dataset_name = matcher(identifier)
            if dataset_name is None:
                continue
            class_number = columns[class_index].strip()
            particle_key = (identifier, columns[key_index])
            assignments[particle_key] = class_number
            class_counts[class_number] += 1
            matched_dataset_names.add(dataset_name)
            matched_tomograms.add(identifier)

    if mode is None:
        raise StarMergeError("Could not detect whether the classification STAR file is 2D or 3D.")
    if pixel_size is None:
        raise StarMergeError("The particle STAR file does not contain _rlnImagePixelSize in data_optics.")

    return _ClassificationStarSummary(
        mode=mode,
        pixel_size=pixel_size,
        assignments=assignments,
        class_counts=dict(class_counts),
        matched_dataset_names=matched_dataset_names,
        matched_tomograms=matched_tomograms,
    )


def _prepare_particle_document(path: Path, dataset_names: list[str], cancel_event=None) -> dict:
    document = parse_star(path)
    mode = detect_particle_star_mode(path)
    pixel_size = particle_star_pixel_size(path)
    particles_block = document.block("data_particles")
    identifier_header = "_rlnTomoName" if mode == "2d" else "_rlnMicrographName"
    identifier_index = _header_index(particles_block.headers, identifier_header)

    selected_indices: list[int] = []
    for index, row in enumerate(particles_block.rows):
        _check_cancel(cancel_event)
        identifier = row[identifier_index]
        if _matches_any_dataset(identifier, dataset_names):
            selected_indices.append(index)

    return {
        "path": path,
        "document": document,
        "mode": mode,
        "pixel_size": pixel_size,
        "particles_block": particles_block,
        "selected_indices": selected_indices,
        "identifier_index": identifier_index,
    }


def _intersect_selected_particles(parsed: list[dict], identification_mode: str, radius_ang: float, cancel_event=None) -> list[set[int]]:
    common_by_file: list[set[int]] = [set() for _ in parsed]
    if not parsed:
        return common_by_file

    if identification_mode == "name":
        common_keys = _common_name_keys(parsed)
        for file_index, item in enumerate(parsed):
            key_header = _name_key_header(item["mode"])
            key_index = _header_index(item["particles_block"].headers, key_header)
            for row_index in item["selected_indices"]:
                _check_cancel(cancel_event)
                row = item["particles_block"].rows[row_index]
                key = (row[item["identifier_index"]], row[key_index])
                if key in common_keys:
                    common_by_file[file_index].add(row_index)
        return common_by_file

    radius_squared = radius_ang * radius_ang
    used_by_file: list[set[int]] = [set() for _ in parsed]
    reference = parsed[0]
    x_index_ref = _header_index(reference["particles_block"].headers, "_rlnCoordinateX")
    y_index_ref = _header_index(reference["particles_block"].headers, "_rlnCoordinateY")
    z_index_ref = _header_index(reference["particles_block"].headers, "_rlnCoordinateZ")

    grouped_candidates: list[dict[str, list[int]]] = []
    for item in parsed[1:]:
        groups: dict[str, list[int]] = {}
        for row_index in item["selected_indices"]:
            _check_cancel(cancel_event)
            identifier = item["particles_block"].rows[row_index][item["identifier_index"]]
            groups.setdefault(identifier, []).append(row_index)
        grouped_candidates.append(groups)

    # Pre-compute coordinate column indices for all non-reference files once.
    other_coord_indices: list[tuple[int, int, int]] = [
        (
            _header_index(item["particles_block"].headers, "_rlnCoordinateX"),
            _header_index(item["particles_block"].headers, "_rlnCoordinateY"),
            _header_index(item["particles_block"].headers, "_rlnCoordinateZ"),
        )
        for item in parsed[1:]
    ]

    for ref_index in reference["selected_indices"]:
        _check_cancel(cancel_event)
        ref_row = reference["particles_block"].rows[ref_index]
        identifier = ref_row[reference["identifier_index"]]
        ref_point = (
            float(ref_row[x_index_ref]) * reference["pixel_size"],
            float(ref_row[y_index_ref]) * reference["pixel_size"],
            float(ref_row[z_index_ref]) * reference["pixel_size"],
        )
        matched_rows: list[int] = []
        found_in_all = True

        for file_offset, item in enumerate(parsed[1:], start=1):
            x_index, y_index, z_index = other_coord_indices[file_offset - 1]
            candidate_indices = grouped_candidates[file_offset - 1].get(identifier, [])
            matched_index = None
            for candidate_index in candidate_indices:
                if candidate_index in used_by_file[file_offset]:
                    continue
                row = item["particles_block"].rows[candidate_index]
                dx = float(row[x_index]) * item["pixel_size"] - ref_point[0]
                dy = float(row[y_index]) * item["pixel_size"] - ref_point[1]
                dz = float(row[z_index]) * item["pixel_size"] - ref_point[2]
                if dx * dx + dy * dy + dz * dz < radius_squared:
                    matched_index = candidate_index
                    break
            if matched_index is None:
                found_in_all = False
                break
            matched_rows.append(matched_index)

        if found_in_all:
            common_by_file[0].add(ref_index)
            used_by_file[0].add(ref_index)
            for file_offset, matched_index in enumerate(matched_rows, start=1):
                common_by_file[file_offset].add(matched_index)
                used_by_file[file_offset].add(matched_index)

    return common_by_file


def _common_name_keys(parsed: list[dict]) -> set[tuple[str, str]]:
    key_sets: list[set[tuple[str, str]]] = []
    for item in parsed:
        key_header = _name_key_header(item["mode"])
        key_index = _header_index(item["particles_block"].headers, key_header)
        keys: set[tuple[str, str]] = set()
        for row_index in item["selected_indices"]:
            row = item["particles_block"].rows[row_index]
            keys.add((row[item["identifier_index"]], row[key_index]))
        key_sets.append(keys)
    if not key_sets:
        return set()
    common = key_sets[0]
    for key_set in key_sets[1:]:
        common = common & key_set
    return common


def _name_key_header(mode: str) -> str:
    if mode == "2d":
        return "_rlnTomoParticleId"
    if mode == "3d":
        return "_rlnImageName"
    raise StarMergeError(f"Unsupported particle STAR mode: {mode}")


def _header_index(headers: list[str], name: str, required: bool = True) -> int | None:
    try:
        return headers.index(name)
    except ValueError:
        if required:
            raise StarMergeError(f"Missing STAR column: {name}") from None
        return None


def _grid_key(x: float, y: float, z: float, cell: float) -> tuple[int, int, int]:
    return (math.floor(x / cell), math.floor(y / cell), math.floor(z / cell))


def _distance_clean_per_tomo(
    row_indices: list[int],
    particles_block: "StarBlock",
    x_index: int,
    y_index: int,
    z_index: int,
    radius_px: float,
    cancel_event=None,
) -> set[int]:
    """Return the set of indices that are within *radius_px* of an earlier kept particle.

    Uses a spatial grid so average complexity is O(n) instead of O(n²).
    """
    radius_sq = radius_px * radius_px
    kept_grid: dict[tuple[int, int, int], list[tuple[float, float, float]]] = {}
    duplicate_indices: set[int] = set()

    for index in row_indices:
        _check_cancel(cancel_event)
        row = particles_block.rows[index]
        x = float(row[x_index])
        y = float(row[y_index])
        z = float(row[z_index])
        key = _grid_key(x, y, z, radius_px)
        cx, cy, cz = key

        is_dup = False
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                for dz in range(-1, 2):
                    for kx, ky, kz in kept_grid.get((cx + dx, cy + dy, cz + dz), ()):
                        if (x - kx) ** 2 + (y - ky) ** 2 + (z - kz) ** 2 < radius_sq:
                            is_dup = True
                            break
                    if is_dup:
                        break
                if is_dup:
                    break
            if is_dup:
                break

        if is_dup:
            duplicate_indices.add(index)
        else:
            kept_grid.setdefault(key, []).append((x, y, z))

    return duplicate_indices
