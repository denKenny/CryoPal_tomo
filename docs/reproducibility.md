# Reproducibility guidance

For every reported analysis, archive:

1. The exact CryoPal_tomo version/tag and version DOI.
2. The `.cryopal.json` project after jobs have completed.
3. Job-history CSV or HTML export, including completion state and exit code.
4. External tool versions, environment/container definitions, Slurm profile, and relevant hardware.
5. Input dataset identifiers and checksums where licensing permits.
6. Output paths, checksums, QC decisions, and any manual interventions.
7. Coordinate units, pixel sizes, RELION convention/version, MRC axes/origin/handedness, and matching radii.

Run `cryopal-tomo doctor` on each execution host and preserve its output with the analysis. Do not place passwords, tokens, or private keys in projects or exported settings.

The manuscript reproduction package should contain redistributable STAR/MRC fixtures, expected outputs produced independently, benchmark scripts, raw benchmark results, and a machine-readable environment definition.
