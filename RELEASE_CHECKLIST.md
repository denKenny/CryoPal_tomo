# Release checklist

- [ ] Resolve every critical/high correctness issue and freeze the project schema.
- [ ] Run unit, integration, GUI smoke, scientific fixture, and performance tests on supported platforms.
- [ ] Build and install wheel and source distribution in clean environments.
- [ ] Confirm `cryopal-tomo doctor` passes on Linux and macOS.
- [ ] Review dependency licenses, security alerts, asset provenance, and SBOM.
- [ ] Update version, changelog, user guide, compatibility matrix, and `CITATION.cff` together.
- [ ] Verify copyright holder, ORCIDs, author list, DOI metadata, and support contact.
- [ ] Tag the exact release commit and publish artifacts with SHA-256 checksums.
- [ ] Archive the tag on Zenodo and verify the version DOI resolves to the correct files and metadata.
- [ ] Preserve benchmark inputs, raw results, hardware/software metadata, and manuscript reproduction instructions.
- [ ] Raise whole-application branch coverage from the enforced 23% baseline to at least 70%.
- [x] Enable a scoped `mypy` CI gate for scientific and persistence core modules.
- [ ] Resolve the remaining static-type backlog and expand the gate to the full application.
- [ ] Validate representative RELION/Warp STAR files, MRC volumes, and Slurm behavior against real external tools.
