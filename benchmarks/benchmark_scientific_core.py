from __future__ import annotations

import argparse
import json
import platform
import tempfile
import time
from pathlib import Path

from cryoet_organizer import __version__
from cryoet_organizer.star_merge import intersect_particle_stars


def _write_particles(path: Path, count: int, offset: float) -> None:
    lines = [
        "data_optics",
        "",
        "loop_",
        "_rlnImagePixelSize #1",
        "1.0",
        "",
        "data_particles",
        "",
        "loop_",
        "_rlnTomoName #1",
        "_rlnCoordinateX #2",
        "_rlnCoordinateY #3",
        "_rlnCoordinateZ #4",
    ]
    for index in range(count):
        x = (index % 100) * 4.0 + offset
        y = ((index // 100) % 100) * 4.0
        z = (index // 10_000) * 4.0
        lines.append(f"TS_01 {x:.3f} {y:.3f} {z:.3f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(particle_count: int) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="cryopal-benchmark-") as tmpdir:
        root = Path(tmpdir)
        first = root / "first.star"
        second = root / "second.star"
        _write_particles(first, particle_count, 0.0)
        _write_particles(second, particle_count, 0.25)
        started = time.perf_counter()
        result = intersect_particle_stars(
            [first, second],
            {"Dataset": ["TS_01"]},
            "benchmark.star",
            write_common=True,
            write_unique=False,
            identification_mode="distance",
            radius_ang=1.0,
        )
        elapsed = time.perf_counter() - started
    return {
        "benchmark": "distance_intersection",
        "particles_per_input": particle_count,
        "matched_particles": result.common_total,
        "elapsed_seconds": elapsed,
        "particles_per_second": (particle_count * 2) / elapsed,
        "cryopal_tomo_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "processor": platform.processor(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark CryoPal_tomo scientific-core operations")
    parser.add_argument("--particles", type=int, default=10_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.particles < 1:
        parser.error("--particles must be positive")
    payload = run(args.particles)
    rendered = json.dumps(payload, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
