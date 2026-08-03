from __future__ import annotations

from cryoet_organizer.job_catalog import CatalogField, CatalogJob
from cryoet_organizer.warptools_catalog import jobs_by_group


PARTICLE_JOBS: tuple[CatalogJob, ...] = (
    CatalogJob(
        namespace="Particles",
        group="Export particles",
        job_key="ts_export_particles",
        title="Export particles",
    ),
    CatalogJob(
        namespace="Particles",
        group="Distance clean",
        job_key="distance_clean",
        title="Distance clean",
        fields=(
            CatalogField("radius_px", "Clearing radius in px", required=True),
            CatalogField("radius_angstrom", "Clearing radius in A", required=True),
            CatalogField("output_star", "Output star", default_value="Output.star"),
            CatalogField(
                "write_cleaned",
                "Distance cleaned coordinates",
                widget="bool",
                default_value="true",
                advanced=True,
            ),
            CatalogField(
                "write_dublicates",
                "Dublicate coordinates",
                widget="bool",
                advanced=True,
            ),
        ),
    ),
    CatalogJob(
        namespace="Particles",
        group="Intersect .star-files",
        job_key="intersect_star_files",
        title="Intersect .star-files",
        fields=(
            CatalogField(
                "identification_mode",
                "Identification mode",
                widget="choice",
                default_value="By distance",
                required=True,
                options=("By distance", "By name"),
            ),
            CatalogField("radius_px", "Distance in px", required=True),
            CatalogField("radius_angstrom", "Distance in A", required=True),
            CatalogField("output_star", "Output star", default_value="Output.star"),
            CatalogField(
                "write_common",
                "Only common coordinates",
                widget="bool",
                default_value="true",
                advanced=True,
            ),
            CatalogField(
                "write_unique",
                "Only unique coordinates",
                widget="bool",
                advanced=True,
            ),
        ),
    ),
    CatalogJob(
        namespace="Particles",
        group="Merge/Split .star-files",
        job_key="merge_split_star_files",
        title="Merge/Split .star-files",
        fields=(
            CatalogField(
                "mode",
                "Mode",
                widget="choice",
                default_value="Merge .star files",
                required=True,
                options=("Merge .star files", "Split .star file"),
            ),
            CatalogField(
                "output_directory",
                "Output directory",
                widget="path",
                required=True,
                default_value="",
            ),
            CatalogField(
                "output_name",
                "Output name",
                required=True,
                default_value="Output.star",
            ),
        ),
    ),
    CatalogJob(
        namespace="Particles",
        group="Create tomogram extraction-mask from .star-file",
        job_key="create_tomogram_extraction_mask_from_star",
        title="Create tomogram extraction-mask from .star-file",
        fields=(
            CatalogField(
                "input_star",
                "Input STAR file",
                widget="path",
                required=True,
                description="Particle STAR file containing tomogram names and 3D coordinates.",
            ),
            CatalogField(
                "input_star_angpix",
                "Angpix of input .star-file",
                required=True,
                description="Pixel size used by rlnCoordinateX/Y/Z in the input STAR file.",
            ),
            CatalogField(
                "output_mask_angpix",
                "Angpix of output mask",
                required=True,
                description="Pixel size of the generated extraction masks.",
            ),
            CatalogField(
                "distance_cleanup_angstrom",
                "Distance cleanup in A",
                required=True,
                description="3D radius around each particle coordinate that will be set to zero.",
            ),
            CatalogField(
                "output_directory",
                "Output directory",
                widget="path",
                required=True,
                description="Directory where one MRC mask per tomogram will be written.",
            ),
            CatalogField(
                "tomogram_dimensions",
                "Tomogram dimensions X/Y/Z",
                required=True,
                description="Output mask dimensions in voxels, written as X,Y,Z.",
            ),
            CatalogField(
                "add_to_pre_existing",
                "Add to pre-existing",
                widget="bool",
                advanced=True,
                description="Load matching existing masks from the output directory and add the new zeroed regions.",
            ),
        ),
    ),
    CatalogJob(
        namespace="Particles",
        group="Plot particle abundance",
        job_key="plot_particle_abundance",
        title="Plot particle abundance",
        fields=(
            CatalogField(
                "compare_samples",
                "Compare Samples",
                widget="bool",
                advanced=True,
            ),
            CatalogField(
                "plot_mode",
                "Plot mode",
                widget="choice",
                default_value="Plot total particle numbers",
                required=True,
                options=("Plot total particle numbers", "Plot particle density"),
            ),
        ),
    ),
    CatalogJob(
        namespace="Particles",
        group="Plot classification convergence",
        job_key="plot_classification_convergence",
        title="Plot classification convergence",
        fields=(
            CatalogField(
                "input_directory",
                "Classification directory",
                widget="path",
                required=True,
                default_value="",
            ),
        ),
    ),
)


def particle_jobs_by_title() -> dict[str, CatalogJob]:
    return {job.title: job for job in PARTICLE_JOBS}


def particle_job_titles() -> tuple[str, ...]:
    return tuple(job.title for job in PARTICLE_JOBS)


def export_particles_warp_job():
    return next(job for job in jobs_by_group()["Tilt series"] if job.command == "ts_export_particles")
