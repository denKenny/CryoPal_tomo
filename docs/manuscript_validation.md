# Manuscript validation protocol

Use a tagged release candidate and preserve every raw result rather than copying only summary values into the manuscript.

## Correctness fixtures

1. Select redistributable 2D and 3D RELION/Warp STAR fixtures with quoted values, missing values, multiple optics groups, and known particle identities.
2. Compare merge, split, distance-clean, abundance, convergence, and intersection outputs with independently generated reference results.
3. Validate every generated STAR file with `starfile` and every generated mask with `mrcfile.validate` plus visual inspection in an independent viewer.
4. Exercise migrations from every historical project schema and verify both current output and all backup generations.
5. Run deletion fault-injection tests and restore quarantined files before comparing checksums with the originals.
6. Exercise local success, local failure, cancellation, Slurm success, Slurm failure, unknown state, and timeout histories on a real cluster.

## Performance protocol

Run `python benchmarks/benchmark_scientific_core.py --particles 10000 --output result.json` after a warm-up. Repeat at least five times for 1,000, 10,000, and 100,000 particles. Report the median and range together with CPU, memory, operating system, Python version, CryoPal_tomo commit, input checksums, and whether storage was local or networked.

Also measure project-open time, gallery first paint, thumbnail scan time, and peak resident memory on small, medium, and realistically large projects. Define the datasets and acceptance thresholds before collecting release-candidate results.

## Reporting rules

- Separate unit-test coverage from biological/scientific validation.
- Report unsupported STAR/CIF constructs, coordinate conventions, matching rules, and failure handling explicitly.
- Archive scripts, fixtures, raw JSON/CSV results, environment locks, logs, and expected-output checksums with the versioned software release.
- Do not describe external-tool compatibility as validated until the exact tool versions and representative end-to-end workflows have passed.
