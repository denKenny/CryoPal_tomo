from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
from pathlib import Path

from cryoet_organizer import __version__
from cryoet_organizer.project import load_project


def main() -> None:
    parser = argparse.ArgumentParser(prog="cryopal-tomo", description="CryoPal_tomo cryo-ET workflow organiser")
    parser.add_argument("--version", action="version", version=f"CryoPal_tomo {__version__}")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("doctor", help="check the local runtime and optional external tools")
    validate_parser = subcommands.add_parser("validate-project", help="validate a CryoPal_tomo project file")
    validate_parser.add_argument("path", type=Path)
    arguments = parser.parse_args()

    if arguments.command == "doctor":
        raise SystemExit(run_doctor())
    if arguments.command == "validate-project":
        project = load_project(arguments.path)
        print(json.dumps({"path": str(arguments.path), "name": project.name, "schema_version": project.schema_version}))
        return

    from cryoet_organizer.app import CryoETOrganizerApp

    app = CryoETOrganizerApp()
    app.run()


def run_doctor() -> int:
    checks: list[tuple[str, bool, str]] = []
    supported_python = (3, 10) <= sys.version_info[:2] < (3, 14)
    checks.append(("Python", supported_python, platform.python_version()))
    try:
        import tkinter

        tk_detail = f"Tk {tkinter.TkVersion}"
        checks.append(("Tkinter", True, tk_detail))
    except Exception as exc:
        checks.append(("Tkinter", False, str(exc)))

    try:
        with tempfile.TemporaryDirectory(prefix="cryopal-doctor-") as tmpdir:
            Path(tmpdir, "write-check").write_text("ok", encoding="utf-8")
        checks.append(("Temporary files", True, tempfile.gettempdir()))
    except OSError as exc:
        checks.append(("Temporary files", False, str(exc)))

    for executable in ("sbatch", "squeue", "sacct", "scancel", "WarpTools", "MTools"):
        resolved = shutil.which(executable)
        checks.append((executable, True, resolved or "not found (optional)"))

    for label, passed, detail in checks:
        print(f"{'PASS' if passed else 'FAIL'}  {label}: {detail}")
    return 1 if any(not passed for _label, passed, _detail in checks) else 0


def doctor_main() -> None:
    raise SystemExit(run_doctor())
