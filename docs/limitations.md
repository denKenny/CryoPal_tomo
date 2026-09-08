# Known limitations

- Python 3.14, Windows, and WSL have not yet completed the supported-platform test matrix.
- External processing tools are installed and versioned separately. Availability in the GUI does not prove that a command is compatible with the installed external version.
- Imported projects/settings may contain executable custom commands. Inspect and trust their source before running them.
- STAR support is tested around RELION/Warp-style data blocks used by CryoPal. The internal parser supports one scalar section or one loop per data block; semicolon-delimited multiline values and uncommon CIF constructs are not supported. Release tests validate generated RELION-style output with `starfile`.
- Distance intersection provides deterministic maximum-cardinality pairwise matching against the first input file. The common set across more than two inputs is the intersection of those pairwise assignments.
- MRC handedness is not universally defined by MRC2014. Euler-angle, origin, axis, and coordinate conventions must be reported with a manuscript analysis.
- Project paths outside the project directory remain absolute. Moving a project may require updating paths through the UI.
- Gallery deletion is recoverable through `.cryopal_trash`, but there is not yet an in-application restore/purge browser.
- A running local process prevents project switching or shutdown. Slurm jobs continue independently after submission unless cancelled through Slurm.
- Static type checking is enforced for scientific I/O, persistence, deletion, Slurm, and execution-provenance modules. The legacy Tk layer has a substantial annotation backlog; full application typing remains a pre-1.0 target.
