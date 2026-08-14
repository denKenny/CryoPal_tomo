from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from cryoet_organizer.project import DatasetRecord, ProjectData
from cryoet_organizer.tabs.tomograms import TomogramsTab


def _dataset(name: str, root: Path) -> DatasetRecord:
    return DatasetRecord(
        dataset_name=name,
        sample="Sample",
        pixel_size=1.0,
        exposure=1.0,
        tomogram_x=1,
        tomogram_y=1,
        tomogram_z=1,
        raw_frames_folder="",
        mdocs_folder="",
        tilt_series_data_folder=str(root / "tomostar"),
        prepared_mdoc_map={},
    )


def _tab(project: ProjectData) -> TomogramsTab:
    tab = TomogramsTab.__new__(TomogramsTab)
    tab.app = SimpleNamespace(project=project)
    return tab


class TomogramMaskMatchingTests(unittest.TestCase):
    def test_mask_matching_uses_canonical_gallery_ts_stems(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tomostar = root / "tomostar"
            tomostar.mkdir()
            (tomostar / "Position_18.tomostar").write_text("ts", encoding="utf-8")
            masks = root / "masks"
            masks.mkdir()
            mask = masks / "Position_18_ExtractionMask_10A.mrc"
            mask.write_text("mask", encoding="utf-8")

            dataset = _dataset("Dataset", root)
            tab = _tab(ProjectData(datasets=[dataset]))

            message, mapping = tab._matching_mask_message(
                str(masks),
                ["Position_18"],
                [("Dataset", "Position_18")],
            )

            self.assertEqual(message, "1/1 tomogram masks found.")
            self.assertEqual(mapping["Position_18"], mask)

    def test_mask_matching_does_not_reuse_numbered_sibling_for_base_ts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tomostar = root / "tomostar"
            tomostar.mkdir()
            for ts_name in ("Position_18", "Position_18_2"):
                (tomostar / f"{ts_name}.tomostar").write_text("ts", encoding="utf-8")
            masks = root / "masks"
            masks.mkdir()
            sibling_mask = masks / "Position_18_2_ExtractionMask_10A.mrc"
            sibling_mask.write_text("mask", encoding="utf-8")

            dataset = _dataset("Dataset", root)
            tab = _tab(ProjectData(datasets=[dataset]))

            message, mapping = tab._matching_mask_message(
                str(masks),
                ["Position_18", "Position_18_2"],
                [("Dataset", "Position_18"), ("Dataset", "Position_18_2")],
            )

            self.assertEqual(message, "1/2 tomogram masks found.")
            self.assertNotIn("Position_18", mapping)
            self.assertEqual(mapping["Position_18_2"], sibling_mask)

    def test_pytom_job_file_identity_uses_project_ts_groundtruth_before_mask_matching(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tomostar = root / "tomostar"
            tomostar.mkdir()
            for ts_name in ("Position_12", "Position_12_2"):
                (tomostar / f"{ts_name}.tomostar").write_text("ts", encoding="utf-8")
            tm_output = root / "tm_output"
            tm_output.mkdir()
            job_file = tm_output / "Position_12_2_10.00Apx_job.json"
            job_file.write_text("{}", encoding="utf-8")
            masks = root / "masks"
            masks.mkdir()
            mask = masks / "Position_12_2_ExtractionMask_10A.mrc"
            mask.write_text("mask", encoding="utf-8")

            dataset = _dataset("Dataset", root)
            tab = _tab(ProjectData(datasets=[dataset]))
            identities = tab._extract_job_identities([job_file])

            self.assertEqual(identities, [(job_file, dataset, "Position_12_2")])

            ts_names = [ts_name for _job_file, _dataset, ts_name in identities]
            selections = [
                (matched_dataset.dataset_name if matched_dataset is not None else None, ts_name)
                for _job_file, matched_dataset, ts_name in identities
            ]
            message, mapping = tab._matching_mask_message(str(masks), ts_names, selections)

            self.assertEqual(message, "1/1 tomogram masks found.")
            self.assertEqual(mapping["Position_12_2"], mask)

    def test_extract_mask_values_ignore_only_missing_masks_unless_global_ignore_is_enabled(self) -> None:
        tab = _tab(ProjectData())
        mask = Path("/masks/Position_12_2_ExtractionMask_10A.mrc")

        tab.extract_ignore_tomogram_mask_var = SimpleNamespace(get=lambda: False)
        self.assertEqual(
            tab._extract_tomogram_mask_values({"Position_12_2": mask}, "Position_12_2"),
            (str(mask), ""),
        )
        self.assertEqual(
            tab._extract_tomogram_mask_values({"Position_12_2": mask}, "Position_13"),
            ("", "true"),
        )

        tab.extract_ignore_tomogram_mask_var = SimpleNamespace(get=lambda: True)
        self.assertEqual(
            tab._extract_tomogram_mask_values({"Position_12_2": mask}, "Position_12_2"),
            ("", "true"),
        )


if __name__ == "__main__":
    unittest.main()
