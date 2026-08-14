from __future__ import annotations

import tempfile
import time
import unittest
import os
import struct
from pathlib import Path

from cryoet_organizer.mrc_preview import read_mrc_preview
from cryoet_organizer.project import PROJECT_SCHEMA_VERSION, ProjectData
from cryoet_organizer.settings_bundle import (
    apply_settings_import,
    build_settings_export_payload,
    importable_settings_groups,
)
from cryoet_organizer.relion_pipeline import (
    RelionPipelineJob,
    class3d_preview_files,
    parse_relion_pipeline_processes,
    refine3d_preview_file,
    relion_job_files,
)
from cryoet_organizer.relion_projects import (
    RelionProjectDefinition,
    get_project_relion_default,
    get_project_relion_projects,
    set_project_relion_default,
    set_project_relion_projects,
)


PIPELINE_STAR = """
# version 50001

data_pipeline_processes

loop_
C #1
_rlnPipeLineProcessAlias #2
_rlnPipeLineProcessTypeLabel #3
_rlnPipeLineProcessStatusLabel #4
Class3D/job001/ None relion.class3d Succeeded
Select/job002/ Select/c2_job001/ relion.select.interactive Succeeded
Class3D/job003/ None relion.class3d Aborted
Refine3D/job004/ None relion.refine3d.tomo Succeeded
Class3D/job009/ None relion.class3d Succeeded

data_pipeline_nodes
"""


class RelionProjectsTests(unittest.TestCase):
    def test_parser_groups_process_paths_from_pipeline_star(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "default_pipeline.star"
            path.write_text(PIPELINE_STAR, encoding="utf-8")

            jobs = parse_relion_pipeline_processes(path)

        self.assertEqual(
            [(job.job_type, job.job_id, job.relative_path) for job in jobs],
            [
                ("Class3D", "job001", "Class3D/job001"),
                ("Class3D", "job003", "Class3D/job003"),
                ("Class3D", "job009", "Class3D/job009"),
                ("Refine3D", "job004", "Refine3D/job004"),
                ("Select", "job002", "Select/job002"),
            ],
        )
        self.assertEqual(jobs[1].status, "Aborted")
        self.assertEqual(next(job for job in jobs if job.job_id == "job002").alias, "Select/c2_job001/")

    def test_relion_projects_roundtrip_through_project_state(self) -> None:
        project = ProjectData()
        set_project_relion_default(
            project,
            RelionProjectDefinition(
                name="Default Relion",
                root_directory="/default/relion",
                startup_command="conda activate relion",
            ),
        )
        set_project_relion_projects(
            project,
            [
                RelionProjectDefinition(
                    name="Proj",
                    root_directory="/data/relion",
                    startup_command="conda activate relion\nrelion",
                )
            ],
        )

        restored = ProjectData.from_dict(project.to_dict())
        default = get_project_relion_default(restored)
        projects = get_project_relion_projects(restored)

        self.assertEqual(restored.schema_version, PROJECT_SCHEMA_VERSION)
        self.assertEqual(default.name, "Default Relion")
        self.assertEqual(default.root_directory, "/default/relion")
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "Proj")
        self.assertEqual(projects[0].startup_command, "conda activate relion\nrelion")

    def test_settings_bundle_exports_and_imports_relion_projects(self) -> None:
        source = ProjectData()
        set_project_relion_default(
            source,
            RelionProjectDefinition(
                name="Template",
                root_directory="/templates/relion",
                startup_command="module load relion",
            ),
        )
        set_project_relion_projects(
            source,
            [
                RelionProjectDefinition(
                    name="Project A",
                    root_directory="/data/project_a",
                    startup_command="relion",
                ),
                RelionProjectDefinition(
                    name="Project B",
                    root_directory="/data/project_b",
                    startup_command="conda activate relion\nrelion",
                ),
            ],
        )

        payload = build_settings_export_payload(
            source,
            ["relion_projects::__default__", "relion_projects::Project B"],
        )
        groups = importable_settings_groups(payload)
        target = ProjectData()
        applied, skipped = apply_settings_import(
            target,
            payload,
            ["relion_projects::__default__", "relion_projects::Project B"],
            overwrite_existing=False,
        )

        self.assertEqual(skipped, [])
        self.assertEqual(applied, ["relion_projects::__default__", "relion_projects::Project B"])
        self.assertEqual(groups[0].key, "relion_projects")
        self.assertEqual(get_project_relion_default(target).name, "Template")
        projects = get_project_relion_projects(target)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0].name, "Project B")
        self.assertEqual(projects[0].root_directory, "/data/project_b")

    def test_v6_payload_migrates_relion_projects_state(self) -> None:
        project = ProjectData.from_dict({"name": "Legacy", "schema_version": 6, "state": {}, "datasets": []})

        self.assertEqual(project.schema_version, PROJECT_SCHEMA_VERSION)
        self.assertEqual(project.state.relion_projects, [])

    def test_job_files_sort_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            job_dir = root / "Class3D" / "job001"
            job_dir.mkdir(parents=True)
            older = job_dir / "older.star"
            newer = job_dir / "newer.mrc"
            older.write_text("old", encoding="utf-8")
            newer.write_text("new", encoding="utf-8")
            now = time.time()
            older_time = now - 20
            newer_time = now - 5
            older.touch()
            newer.touch()

            os.utime(older, (older_time, older_time))
            os.utime(newer, (newer_time, newer_time))

            files = relion_job_files(root, RelionPipelineJob("Class3D/job001/", "Class3D", "job001", "Class3D/job001"))

        self.assertEqual([path.name for path in files], ["newer.mrc", "older.star"])

    def test_preview_file_helpers_pick_highest_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            class_dir = root / "Class3D" / "job009"
            refine_dir = root / "Refine3D" / "job010"
            class_dir.mkdir(parents=True)
            refine_dir.mkdir(parents=True)
            for name in (
                "run_it001_class001.mrc",
                "run_it005_class002.mrc",
                "run_it005_class001.mrc",
                "run_it005_data.star",
                "run_it027_class001.mrc",
                "run_it027_class002.mrc",
                "run_it027_class003.mrc",
                "run_it027_class004.mrc",
            ):
                (class_dir / name).write_text("x", encoding="utf-8")
            (refine_dir / "run_it003_class001.mrc").write_text("x", encoding="utf-8")
            (refine_dir / "run_it007_half1_class001_unfil.mrc").write_text("x", encoding="utf-8")
            (refine_dir / "run_it007_class001.mrc").write_text("x", encoding="utf-8")

            iteration, class_files = class3d_preview_files(
                root,
                RelionPipelineJob("Class3D/job009/", "Class3D", "job009", "Class3D/job009"),
            )
            refine_iteration, refine_file = refine3d_preview_file(
                root,
                RelionPipelineJob("Refine3D/job010/", "Refine3D", "job010", "Refine3D/job010"),
            )

        self.assertEqual(iteration, 27)
        self.assertEqual(
            [path.name for path in class_files],
            [
                "run_it027_class001.mrc",
                "run_it027_class002.mrc",
                "run_it027_class003.mrc",
                "run_it027_class004.mrc",
            ],
        )
        self.assertEqual(refine_iteration, 7)
        self.assertEqual(refine_file.name if refine_file is not None else "", "run_it007_class001.mrc")

    def test_mrc_preview_reads_xy_xz_planes_but_mrcs_reads_one_plane(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mrc = root / "volume.mrc"
            mrcs = root / "stack.mrcs"
            _write_mode2_mrc(mrc, dimensions=(4, 3, 2))
            _write_mode2_mrc(mrcs, dimensions=(4, 3, 5))

            volume_preview = read_mrc_preview(mrc, max_size=8)
            stack_preview = read_mrc_preview(mrcs, stack_2d=True, max_size=8)

        self.assertEqual(volume_preview.dimensions, (4, 3, 2))
        self.assertEqual([plane.label for plane in volume_preview.planes], ["XY", "XZ"])
        self.assertEqual([plane.label for plane in stack_preview.planes], ["XY"])
        self.assertEqual([(plane.width, plane.height) for plane in volume_preview.planes], [(8, 8), (8, 8)])
        self.assertEqual([(plane.width, plane.height) for plane in stack_preview.planes], [(8, 8)])


def _write_mode2_mrc(path: Path, *, dimensions: tuple[int, int, int]) -> None:
    nx, ny, nz = dimensions
    header = bytearray(1024)
    struct.pack_into("<4i", header, 0, nx, ny, nz, 2)
    struct.pack_into("<3i", header, 28, nx, ny, nz)
    struct.pack_into("<3f", header, 40, float(nx), float(ny), float(nz))
    header[208:212] = b"MAP "
    values = [float(index) for index in range(nx * ny * nz)]
    with path.open("wb") as handle:
        handle.write(header)
        handle.write(struct.pack(f"<{len(values)}f", *values))


if __name__ == "__main__":
    unittest.main()
