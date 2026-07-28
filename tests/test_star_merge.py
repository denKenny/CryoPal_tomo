from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryoet_organizer.star_merge import particle_abundance_plot_data, particle_classification_convergence_data


def _write_classification_star(path: Path, rows: list[tuple[str, str, str]]) -> None:
    lines = [
        "data_optics",
        "",
        "loop_",
        "_rlnImagePixelSize #1",
        "1.5",
        "",
        "data_particles",
        "",
        "loop_",
        "_rlnMicrographName #1",
        "_rlnImageName #2",
        "_rlnClassNumber #3",
    ]
    for micrograph_name, image_name, class_number in rows:
        lines.append(f"{micrograph_name} {image_name} {class_number}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_tomogram_classification_star(path: Path, rows: list[tuple[str, str, str]]) -> None:
    lines = [
        "data_optics",
        "",
        "loop_",
        "_rlnImagePixelSize #1",
        "1.5",
        "",
        "data_particles",
        "",
        "loop_",
        "_rlnTomoName #1",
        "_rlnTomoParticleId #2",
        "_rlnClassNumber #3",
    ]
    for tomo_name, particle_id, class_number in rows:
        lines.append(f"{tomo_name} {particle_id} {class_number}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_abundance_star(path: Path, tomo_names: list[str]) -> None:
    lines = [
        "data_particles",
        "",
        "loop_",
        "_rlnTomoName #1",
    ]
    lines.extend(tomo_names)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class StarMergeTests(unittest.TestCase):
    def test_classification_convergence_uses_matching_particles_and_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _write_classification_star(
                base / "run_it001_data.star",
                [
                    ("DS1_tomoA.mrc", "1@stack.mrcs", "1"),
                    ("DS1_tomoA.mrc", "2@stack.mrcs", "2"),
                    ("OTHER_tomo.mrc", "3@stack.mrcs", "5"),
                ],
            )
            _write_classification_star(
                base / "run_it002_data.star",
                [
                    ("DS1_tomoA.mrc", "1@stack.mrcs", "2"),
                    ("DS1_tomoA.mrc", "2@stack.mrcs", "2"),
                    ("OTHER_tomo.mrc", "3@stack.mrcs", "5"),
                ],
            )

            first = particle_classification_convergence_data(base, ["DS1"])
            second = particle_classification_convergence_data(base, ["DS1"])

        self.assertIs(first, second)
        self.assertEqual(first.mode, "3d")
        self.assertEqual(first.pixel_size, 1.5)
        self.assertEqual(first.dataset_count, 1)
        self.assertEqual(first.tomogram_count, 1)
        self.assertEqual(len(first.iterations), 2)
        self.assertEqual(first.iterations[0].class_counts, {"1": 1, "2": 1})
        self.assertEqual(first.iterations[0].changed_count, 0)
        self.assertEqual(first.iterations[1].class_counts, {"2": 2})
        self.assertEqual(first.iterations[1].changed_count, 1)

    def test_classification_convergence_matches_tomostar_alias_to_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            _write_tomogram_classification_star(
                base / "run_it001_data.star",
                [
                    ("TS_01.tomostar", "1@stack.mrcs", "1"),
                    ("TS_01.tomostar", "2@stack.mrcs", "2"),
                    ("TS_02.tomostar", "3@stack.mrcs", "5"),
                ],
            )
            _write_tomogram_classification_star(
                base / "run_it002_data.star",
                [
                    ("TS_01.tomostar", "1@stack.mrcs", "2"),
                    ("TS_01.tomostar", "2@stack.mrcs", "2"),
                    ("TS_02.tomostar", "3@stack.mrcs", "5"),
                ],
            )

            plot = particle_classification_convergence_data(
                base,
                {"DatasetA": ["TS_01", "TS_01.tomostar"]},
            )

        self.assertEqual(plot.mode, "2d")
        self.assertEqual(plot.dataset_count, 1)
        self.assertEqual(plot.tomogram_count, 1)
        self.assertEqual(plot.particle_count, 2)
        self.assertEqual(plot.iterations[1].changed_count, 1)

    def test_abundance_matches_tomostar_alias_to_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            star_path = Path(tmpdir) / "particles.star"
            _write_abundance_star(
                star_path,
                ["TS_01.tomostar", "TS_01.tomostar", "TS_02.tomostar"],
            )

            plot = particle_abundance_plot_data(
                star_path,
                {"DatasetA": "SampleA"},
                compare_samples=False,
                measure="total",
                dataset_aliases={"DatasetA": ["TS_01", "TS_01.tomostar"]},
            )

        self.assertEqual(plot.conditions[0].label, "DatasetA")
        self.assertEqual(plot.conditions[0].values, [2.0])
        self.assertEqual(plot.conditions[0].dataset_count, 1)
        self.assertEqual(plot.conditions[0].tomogram_count, 1)


if __name__ == "__main__":
    unittest.main()
