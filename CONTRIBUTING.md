# Contributing

Bug reports, workflow compatibility reports, documentation improvements, and code contributions are welcome through the [GitHub issue tracker](https://github.com/denKenny/CryoPal_tomo/issues).

## Development setup

1. Create the environment with `conda env create -f environment.yml` and activate `cryopal-tomo`.
2. Install development tools with `python -m pip install -e ".[dev]"`.
3. Run `python -m unittest discover -s tests -v` before submitting a change.
4. Run `python -m compileall -q cryoet_organizer benchmarks` and build with `python -m build`.
5. Run the scoped core type check shown in `.github/workflows/ci.yml`.

The CI gate preserves the current whole-application branch-coverage baseline of 23%. New code should raise this baseline; the 1.0 release target is at least 70%, with higher coverage for scientific I/O, migration, deletion, and command-execution modules. CI type-checks that core now; the legacy Tk UI remains outside the scoped type gate until its annotation backlog is resolved.

Changes to STAR, MRC, coordinate, migration, deletion, or command-execution behavior must include a focused regression test and, where scientific semantics change, an independently generated reference fixture.

Use a feature branch, keep commits focused, do not include private datasets or credentials, and describe the operating system, Python version, external-tool versions, and validation performed in the pull request.
