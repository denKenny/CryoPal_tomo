from __future__ import annotations

import struct
import tempfile
import unittest
import importlib.util
import io
from pathlib import Path

from cryoet_organizer.extraction_mask import (
    ExtractionMaskError,
    detect_extraction_star_angpix,
    grouped_extraction_particles,
    output_mask_path,
    read_mrc_header,
    write_extraction_masks,
)


def _write_extraction_star(path: Path) -> None:
    lines = [
        "data_optics",
        "",
        "loop_",
        "_rlnImagePixelSize #1",
        "10.0",
        "",
        "data_particles",
        "",
        "loop_",
        "_rlnCoordinateX #1",
        "_rlnCoordinateY #2",
        "_rlnCoordinateZ #3",
        "_rlnMicrographName #4",
        "_rlnOriginXAngst #5",
        "_rlnOriginYAngst #6",
        "_rlnOriginZAngst #7",
        "10 20 30 TS_01.tomostar 5 2 8",
        "1 1 1 TS_02.tomostar 0 0 0",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_shape_star(path: Path) -> None:
    lines = [
        "data_particles",
        "",
        "loop_",
        "_rlnCoordinateX #1",
        "_rlnCoordinateY #2",
        "_rlnCoordinateZ #3",
        "_rlnMicrographName #4",
        "_rlnAngleRot #5",
        "_rlnAngleTilt #6",
        "_rlnAnglePsi #7",
        "3 3 3 TS_01.tomostar 0 0 0",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_mode0_mrc(
    path: Path,
    dimensions: tuple[int, int, int],
    active_indices: set[tuple[int, int, int]],
    angpix: float = 1.0,
    active_value: int = 1,
) -> None:
    nx, ny, nz = dimensions
    header = bytearray(1024)
    struct.pack_into("<4i", header, 0, nx, ny, nz, 0)
    struct.pack_into("<3i", header, 28, nx, ny, nz)
    struct.pack_into("<3f", header, 40, nx * angpix, ny * angpix, nz * angpix)
    struct.pack_into("<3f", header, 52, 90.0, 90.0, 90.0)
    struct.pack_into("<3i", header, 64, 1, 2, 3)
    header[208:212] = b"MAP "
    header[212:216] = bytes((0x44, 0x41, 0x00, 0x00))
    data = bytearray(nx * ny * nz)
    for x_index, y_index, z_index in active_indices:
        data[x_index + y_index * nx + z_index * nx * ny] = active_value & 0xFF
    path.write_bytes(bytes(header) + bytes(data))


def _read_mode0_data(path: Path) -> bytes:
    header = read_mrc_header(path)
    with path.open("rb") as handle:
        handle.seek(header.data_offset)
        return handle.read(header.nx * header.ny * header.nz)


class ExtractionMaskTests(unittest.TestCase):
    def test_star_coordinates_are_rescaled_and_shifted_by_angstrom_origins(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            star_path = Path(tmpdir) / "particles.star"
            _write_extraction_star(star_path)

            self.assertEqual(detect_extraction_star_angpix(star_path), 10.0)
            grouped = grouped_extraction_particles(star_path, input_angpix=10.0, output_angpix=5.0)

            particle = grouped["TS_01.tomostar"][0]
            self.assertAlmostEqual(particle.x, 19.0)
            self.assertAlmostEqual(particle.y, 39.6)
            self.assertAlmostEqual(particle.z, 58.4)

    def test_output_mask_name_uses_tomostar_stem_and_angpix(self) -> None:
        path = output_mask_path("/tmp/out", "20220718_TS_012.tomostar", 10.0)
        self.assertEqual(path.name, "20220718_TS_012_ExtractionMask_10A.mrc")

    def test_write_mrc_mask_zeros_3d_cleanup_sphere(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            star_path = root / "particles.star"
            _write_extraction_star(star_path)

            result = write_extraction_masks(
                input_star_path=star_path,
                output_directory=root,
                input_angpix=10.0,
                output_angpix=10.0,
                cleanup_distance_angstrom=10.0,
                dimensions=(5, 5, 5),
            )

            output = next(item for item in result.outputs if item.ts_name == "TS_02.tomostar")
            header = read_mrc_header(output.path)
            self.assertEqual((header.nx, header.ny, header.nz), (5, 5, 5))
            self.assertEqual(header.mode, 0)

            data = _read_mode0_data(output.path)
            self.assertEqual(data[1 + 1 * 5 + 1 * 25], 0)
            self.assertEqual(data[4 + 4 * 5 + 4 * 25], 1)

    @unittest.skipUnless(importlib.util.find_spec("mrcfile"), "mrcfile validation dependency is not installed")
    def test_written_mask_is_valid_mrc2014(self) -> None:
        import mrcfile

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            star_path = root / "particles.star"
            _write_extraction_star(star_path)
            result = write_extraction_masks(
                input_star_path=star_path,
                output_directory=root,
                input_angpix=10.0,
                output_angpix=10.0,
                cleanup_distance_angstrom=10.0,
                dimensions=(5, 5, 5),
            )

            validation_output = io.StringIO()
            self.assertTrue(mrcfile.validate(result.outputs[0].path, print_file=validation_output))

    def test_add_to_existing_mask_preserves_existing_zero_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            first_star = root / "first.star"
            first_star.write_text(
                "\n".join(
                    [
                        "data_particles",
                        "",
                        "loop_",
                        "_rlnCoordinateX #1",
                        "_rlnCoordinateY #2",
                        "_rlnCoordinateZ #3",
                        "_rlnMicrographName #4",
                        "1 1 1 TS_01.tomostar",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            second_star = root / "second.star"
            second_star.write_text(
                "\n".join(
                    [
                        "data_particles",
                        "",
                        "loop_",
                        "_rlnCoordinateX #1",
                        "_rlnCoordinateY #2",
                        "_rlnCoordinateZ #3",
                        "_rlnMicrographName #4",
                        "3 3 3 TS_01.tomostar",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            write_extraction_masks(
                input_star_path=first_star,
                output_directory=root,
                input_angpix=1.0,
                output_angpix=1.0,
                cleanup_distance_angstrom=0.0,
                dimensions=(5, 5, 5),
            )
            write_extraction_masks(
                input_star_path=second_star,
                output_directory=root,
                input_angpix=1.0,
                output_angpix=1.0,
                cleanup_distance_angstrom=0.0,
                dimensions=(5, 5, 5),
                add_to_pre_existing=True,
            )

            output = output_mask_path(root, "TS_01.tomostar", 1.0)
            data = _read_mode0_data(output)
            self.assertEqual(data[1 + 1 * 5 + 1 * 25], 0)
            self.assertEqual(data[3 + 3 * 5 + 3 * 25], 0)
            self.assertEqual(data[0], 1)

    def test_shape_mask_zeros_oriented_shape_without_interpolation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            star_path = root / "particles.star"
            shape_path = root / "shape.mrc"
            _write_shape_star(star_path)
            _write_mode0_mrc(shape_path, (3, 3, 3), {(1, 1, 1), (2, 1, 1)})

            result = write_extraction_masks(
                input_star_path=star_path,
                output_directory=root,
                input_angpix=1.0,
                output_angpix=1.0,
                cleanup_distance_angstrom=0.0,
                dimensions=(7, 7, 7),
                mask_mode="shape",
                shape_mask_path=shape_path,
                binarize_shape_input=True,
            )

            output = result.outputs[0]
            data = _read_mode0_data(output.path)
            self.assertEqual(data[3 + 3 * 7 + 3 * 49], 0)
            self.assertEqual(data[4 + 3 * 7 + 3 * 49], 0)
            self.assertEqual(data[2 + 3 * 7 + 3 * 49], 1)

    def test_shape_mask_requires_relion_euler_angles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            star_path = root / "particles.star"
            shape_path = root / "shape.mrc"
            _write_extraction_star(star_path)
            _write_mode0_mrc(shape_path, (3, 3, 3), {(1, 1, 1)})

            with self.assertRaises(ExtractionMaskError):
                write_extraction_masks(
                    input_star_path=star_path,
                    output_directory=root,
                    input_angpix=1.0,
                    output_angpix=1.0,
                    cleanup_distance_angstrom=0.0,
                    dimensions=(7, 7, 7),
                    mask_mode="shape",
                    shape_mask_path=shape_path,
                )

    def test_binarize_shape_input_uses_positive_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            star_path = root / "particles.star"
            shape_path = root / "shape.mrc"
            _write_shape_star(star_path)
            _write_mode0_mrc(shape_path, (3, 3, 3), {(1, 1, 1)}, active_value=2)

            with self.assertRaises(ExtractionMaskError):
                write_extraction_masks(
                    input_star_path=star_path,
                    output_directory=root / "without_binarize",
                    input_angpix=1.0,
                    output_angpix=1.0,
                    cleanup_distance_angstrom=0.0,
                    dimensions=(7, 7, 7),
                    mask_mode="shape",
                    shape_mask_path=shape_path,
                    binarize_shape_input=False,
                )

            result = write_extraction_masks(
                input_star_path=star_path,
                output_directory=root / "with_binarize",
                input_angpix=1.0,
                output_angpix=1.0,
                cleanup_distance_angstrom=0.0,
                dimensions=(7, 7, 7),
                mask_mode="shape",
                shape_mask_path=shape_path,
                binarize_shape_input=True,
            )
            data = _read_mode0_data(result.outputs[0].path)
            self.assertEqual(data[3 + 3 * 7 + 3 * 49], 0)

    def test_mode0_negative_values_are_not_treated_as_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            star_path = root / "particles.star"
            shape_path = root / "negative-shape.mrc"
            _write_shape_star(star_path)
            _write_mode0_mrc(shape_path, (3, 3, 3), {(1, 1, 1)}, active_value=-1)

            with self.assertRaisesRegex(ExtractionMaskError, "does not contain any positive voxels"):
                write_extraction_masks(
                    input_star_path=star_path,
                    output_directory=root / "negative",
                    input_angpix=1.0,
                    output_angpix=1.0,
                    cleanup_distance_angstrom=0.0,
                    dimensions=(7, 7, 7),
                    mask_mode="shape",
                    shape_mask_path=shape_path,
                    binarize_shape_input=True,
                )


if __name__ == "__main__":
    unittest.main()
