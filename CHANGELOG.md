# Changelog

All notable changes are documented here. The project follows Semantic Versioning from version 1.0 onward.

## Unreleased

### Changed

- Added schema-8 execution provenance fields and strict project validation.
- Added recoverable, transactional gallery deletion with exact identifier boundaries.
- Added deterministic maximum-cardinality pairwise matching for distance-based particle intersection.
- Added installable package metadata, diagnostics, CI, and release documentation.
- Added explicit trust confirmation for imported command-bearing settings.
- Added job status, timing, exit-code, host, application-version, working-directory, and environment-fingerprint provenance.
- Added reference-library validation for generated RELION STAR and MRC2014 files.

### Fixed

- Prevented future project schemas from being silently downgraded.
- Preserved shortcuts during legacy project migration.
- Preserved quoted and missing STAR values during round trips.
- Prevented dataset identifiers such as `DS1` from matching `DS10`.
- Interpreted MRC mode 0 as signed 8-bit data and closed leaked mask temporary-file descriptors.
- Rejected Slurm submissions without a validated job ID and bounded job-state polling.
- Prevented project replacement and application shutdown while local jobs are active without an explicit abort.
- Neutralized spreadsheet formulas in CSV exports and escaped user-controlled HTML report fields.

## 0.1.0

- Initial public development release.
