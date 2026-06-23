from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class MToolFlag:
    name: str
    required: bool
    description: str
    widget: str = "text"
    default_value: str = ""
    browse_mode: str = "file"


@dataclass(frozen=True)
class MToolCommand:
    group: str
    executable: str
    command: str
    flags: tuple[MToolFlag, ...] = field(default_factory=tuple)


def _flag(
    name: str,
    required: bool,
    description: str,
    widget: str = "text",
    default_value: str = "",
    browse_mode: str = "file",
) -> MToolFlag:
    return MToolFlag(
        name=name,
        required=required,
        description=description,
        widget=widget,
        default_value=default_value,
        browse_mode=browse_mode,
    )


MTOOLS_COMMANDS: tuple[MToolCommand, ...] = (
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="create_source",
        flags=(
            _flag("--population", True, "Path to the .population file to which to add the new data source.", "path"),
            _flag(
                "--processing_settings",
                True,
                "Path to a .settings file used to pre-process the frame or tilt series this source should include.",
                "path",
            ),
            _flag("--name", True, "Name of the new data source."),
            _flag("--nframes", False, "Maximum number of tilts or frames to use in refinements. Leave empty or set to 0 to use the maximum available."),
            _flag("--files", False, "Optional STAR file with a list of files to intersect with the full list referenced by the settings.", "path"),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="create_species",
        flags=(
            _flag("--population", True, "Path to the .population file to which to add the new species.", "path"),
            _flag("--name", True, "Name of the new species."),
            _flag("--diameter", True, "Molecule diameter in Angstrom."),
            _flag("--half1", True, "Path to first half-map file.", "path"),
            _flag("--half2", True, "Path to second half-map file.", "path"),
            _flag("--mask", True, "Path to a tight binary mask file.", "path"),
            _flag("--sym", False, "Point symmetry, e.g. C1, D7, O.", default_value="C1"),
            _flag("--helical_units", False, "Number of helical asymmetric units.", default_value="1"),
            _flag("--helical_twist", False, "Helical twist in degrees."),
            _flag("--helical_rise", False, "Helical rise in Angstrom."),
            _flag("--helical_height", False, "Height of the helical segment along the Z axis in Angstrom."),
            _flag("--temporal_samples", False, "Number of temporal samples in each particle pose trajectory.", default_value="1"),
            _flag("--angpix", False, "Override pixel size value found in half-maps."),
            _flag("--angpix_resample", False, "Resample half-maps and masks to this pixel size."),
            _flag("--lowpass", False, "Optional low-pass filter in Angstrom."),
            _flag("--particles_relion", False, "Path to RELION particle metadata.", "path"),
            _flag("--particles_m", False, "Path to particle metadata from M.", "path"),
            _flag("--angpix_coords", False, "Override pixel size for RELION particle coordinates."),
            _flag("--angpix_shifts", False, "Override pixel size for RELION particle shifts."),
            _flag("--ignore_unmatched", False, "Do not fail if there are particles that do not match any data sources.", "bool"),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="rotate_species",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
            _flag("--species", True, "Path to the .species file, or its GUID."),
            _flag("--angle_rot", True, "First Euler angle (Rot in RELION) in degrees."),
            _flag("--angle_tilt", True, "Second Euler angle (Tilt in RELION) in degrees."),
            _flag("--angle_psi", True, "Third Euler angle (Psi in RELION) in degrees."),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="shift_species",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
            _flag("--species", True, "Path to the .species file, or its GUID."),
            _flag("-x", True, "Shift along the X axis in Angstrom."),
            _flag("-y", True, "Shift along the Y axis in Angstrom."),
            _flag("-z", True, "Shift along the Z axis in Angstrom."),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="expand_symmetry",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
            _flag("--species", True, "Path to the .species file, or its GUID."),
            _flag("--expand_from", False, "Symmetry to use for the expansion if different from the species symmetry."),
            _flag("--expand_to", False, "Remaining symmetry that will be set as the species symmetry."),
            _flag("--helical_units", False, "Number of asymmetric subunits in the helical symmetry to expand.", default_value="1"),
            _flag("--helical_twist", False, "Twist of the helical symmetry to expand, in degrees."),
            _flag("--helical_rise", False, "Rise of the helical symmetry to expand, in Angstrom."),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="resample_trajectories",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
            _flag("--species", True, "Path to the .species file, or its GUID."),
            _flag("--samples", True, "The new number of samples."),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="update_mask",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
            _flag("--species", True, "Path to the .species file, or its GUID."),
            _flag("--map", True, "Path to the MRC map to be used to create the new mask.", "path"),
            _flag("--threshold", True, "Binarization threshold to convert the input map to a mask."),
            _flag("--dilate", False, "Dilate the binary mask by this many voxels.", default_value="0"),
            _flag("--center", False, "Center the species around the new mask center of mass.", "bool"),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="list_species",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="list_sources",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="add_source",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
            _flag("--source", True, "Path to the .source file.", "path"),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="remove_species",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
            _flag("--species", True, "Path to the .species file, or its GUID."),
        ),
    ),
    MToolCommand(
        group="MTools",
        executable="MTools",
        command="remove_source",
        flags=(
            _flag("--population", True, "Path to the .population file.", "path"),
            _flag("--source", True, "Path to the .source file, or its GUID."),
        ),
    ),
    MToolCommand(
        group="MCore",
        executable="MCore",
        command="MCore",
        flags=(
            _flag("--population", True, "Path to the .population file containing descriptions of data sources and species.", "path"),
            _flag("--port", False, "Port to use for REST API calls.", default_value="14300"),
            _flag("--devicelist", False, "Space-separated list of GPU IDs to use for processing.", default_value="0"),
            _flag("--perdevice_preprocess", False, "Number of processes per GPU used for map pre-processing."),
            _flag("--perdevice_refine", False, "Number of processes per GPU used for refinement.", default_value="1"),
            _flag("--perdevice_postprocess", False, "Number of processes per GPU used for map post-processing."),
            _flag("--workers_preprocess", False, "List of remote workers for map pre-processing."),
            _flag("--workers_refine", False, "List of remote workers for refinement."),
            _flag("--workers_postprocess", False, "List of remote workers for map post-processing."),
            _flag("--iter", False, "Number of refinement sub-iterations.", default_value="3"),
            _flag("--first_iteration_fraction", False, "Use this fraction of available resolution for alignment in first sub-iteration.", default_value="1"),
            _flag("--min_particles", False, "Only use series with at least N particles in the field of view.", default_value="1"),
            _flag("--cpu_memory", False, "Use CPU memory to store particle images during refinement.", "bool"),
            _flag("--weight_threshold", False, "Refine each tilt/frame up to the resolution at which the exposure weighting function reaches this value.", default_value="0.05"),
            _flag("--refine_imagewarp", False, "Refine image warp with a grid of XxY dimensions."),
            _flag("--refine_particles", False, "Refine particle poses.", "bool"),
            _flag("--refine_mag", False, "Refine anisotropic magnification.", "bool"),
            _flag("--refine_doming", False, "Refine doming (frame series only).", "bool"),
            _flag("--refine_stageangles", False, "Refine stage angles (tilt series only).", "bool"),
            _flag("--refine_volumewarp", False, "Refine volume warp with a grid of XxYxZxT dimensions."),
            _flag("--refine_tiltmovies", False, "Refine tilt movie alignments (tilt series only).", "bool"),
            _flag("--ctf_batch", False, "Batch size for CTF refinements.", default_value="32"),
            _flag("--ctf_minresolution", False, "Use only species with at least this resolution for CTF refinement.", default_value="8"),
            _flag("--ctf_defocus", False, "Refine defocus using a local search.", "bool"),
            _flag("--ctf_defocusexhaustive", False, "Refine defocus using a more exhaustive search in the first sub-iteration.", "bool"),
            _flag("--ctf_phase", False, "Refine phase shift (phase plate data only).", "bool"),
            _flag("--ctf_cs", False, "Refine spherical aberration, also a proxy for pixel size.", "bool"),
            _flag("--ctf_zernike3", False, "Refine Zernike polynomials of 3rd order.", "bool"),
            _flag("--ctf_zernike5", False, "Refine Zernike polynomials of 5th order.", "bool"),
            _flag("--ctf_zernike2", False, "Refine Zernike polynomials of 2nd order.", "bool"),
            _flag("--ctf_zernike4", False, "Refine Zernike polynomials of 4th order.", "bool"),
        ),
    ),
)


M_GROUPS = ("MTools", "MCore")


def m_jobs_by_group() -> dict[str, tuple[MToolCommand, ...]]:
    grouped: dict[str, list[MToolCommand]] = {group: [] for group in M_GROUPS}
    for job in MTOOLS_COMMANDS:
        grouped.setdefault(job.group, []).append(job)
    return {group: tuple(grouped.get(group, [])) for group in M_GROUPS}
