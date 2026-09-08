# Architecture

CryoPal_tomo is a Tk desktop application around a serializable project model and adapters for external cryo-ET tools.

## Layers

- `project.py` owns schema migration, validation, datasets, application state, and execution history.
- Scientific helpers such as `star_merge.py`, `extraction_mask.py`, and `mrc_preview.py` operate independently of widgets and are covered by deterministic tests.
- Execution helpers render local or Slurm commands and maintain job state. Custom scripts and Slurm preambles deliberately retain shell semantics.
- `file_resolver.py` provides central path resolution. Tk tabs should consume these services rather than duplicate path, parsing, or process logic.
- `tabs/` contains presentation and interaction code. Background workers return results through Tk callbacks and must not mutate widgets directly.

## Persistent interfaces

Project files use the suffix `.cryopal.json`. Schema 8 adds optional execution provenance fields. Schemas 1–7 are migrated on load; schemas newer than the running application are rejected without modification. Saving uses a temporary file, filesystem replacement, and up to three `.bakN` recovery generations.

Job-history records include a stable entry ID, execution state, submission/start/finish timestamps, exit code, failure reason, working directory, host, application/tool versions, and environment fingerprint. Older records remain valid and receive empty/default provenance values.

## Design constraints

CryoPal coordinates external software rather than reproducing its algorithms. A command preview is therefore part of the scientific record. Built-in structured fields should avoid shell interpretation; custom jobs, shortcuts, environment activation, and Slurm preambles are explicitly executable content.

