"""Portable, versioned job provenance. Never opens files or executes commands."""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shlex


VERSION = 1
FILE_SUFFIXES = {".star", ".mrc", ".mrcs", ".tomostar", ".population", ".xml", ".json",
                 ".h5", ".hdf", ".txt", ".tlt", ".xf", ".st", ".ali", ".rec", ".npy", ".npz", ".cs"}
INPUT_KEYS = {"i", "input", "input_file", "input_star", "input_stars", "input_data", "settings",
              "population", "tomogram", "tomogram_file", "reference", "mask", "half1", "half2"}
OUTPUT_KEYS = {"o", "output", "output_file", "output_star", "output_mask", "output_mrc"}


def normalize_artifact(value: str, cwd: str = "") -> str:
    value = str(value).strip().strip("\"'")
    if not value or re.search(r"[\n\r*$?{}<>|]", value) or re.fullmatch(r"\d+ values", value):
        return ""
    # Paths are from the execution host, not necessarily the computer displaying the project.
    if not value.startswith("/"):
        if not cwd.startswith("/"):
            return ""
        value = posixpath.join(cwd, value)
    return posixpath.normpath(value)


def inferred_io(record: dict) -> tuple[list[str], list[str]]:
    parameters = record.get("parameters", {})
    artifacts = record.get("artifacts", {})
    custom = bool(artifacts.get("custom_job_id") or artifacts.get("workflow_catalog_namespace") == "Custom"
                  or record.get("processing_tab") == "Processing: Custom jobs")
    roles = {}
    if custom:
        for parameter in artifacts.get("custom_job_definition", {}).get("parameters", []):
            role = parameter.get("extra", {}).get("io_role")
            if role in {"input", "output"}:
                roles[str(parameter.get("key", ""))] = role
                roles[str(parameter.get("flag", "")).lstrip("-")] = role
    inputs, outputs = set(), set()
    cwd = record.get("working_directory") or record.get("cwd", "")

    def accept(key, raw):
        key = str(key).lstrip("-").lower().replace(" ", "_")
        role = roles.get(key) if custom else (
            "input" if key in INPUT_KEYS or key.startswith("input_") else
            "output" if key in OUTPUT_KEYS or key.startswith("output_") else "")
        if not role or any(part in key for part in ("directory", "folder", "pattern", "angpix", "pixel")):
            return
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            path = normalize_artifact(str(value), cwd)
            if path and posixpath.splitext(path)[1].lower() in FILE_SUFFIXES:
                (inputs if role == "input" else outputs).add(path)

    for key, value in parameters.items():
        accept(key, value)
    # Only recognized flags are examined. Shell fragments are never evaluated.
    for line in str(record.get("command", "")).splitlines():
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        for index, token in enumerate(tokens):
            if token.startswith("-"):
                key, separator, value = token.partition("=")
                if not separator and index + 1 < len(tokens) and not tokens[index + 1].startswith("-"):
                    value = tokens[index + 1]
                accept(key, value)
    return sorted(inputs), sorted(outputs)


def provenance_signature(record: dict) -> str:
    data = (record.get("command"), record.get("parameters"), record.get("working_directory") or record.get("cwd"),
            record.get("processing_tab"), record.get("artifacts", {}).get("custom_job_definition"),
            record.get("artifacts", {}).get("custom_job_id"),
            record.get("artifacts", {}).get("workflow_catalog_namespace"))
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def provenance_for_record(record: dict) -> dict:
    previous = record.get("artifacts", {}).get("provenance", {})
    if not isinstance(previous, dict):
        previous = {}
    signature = provenance_signature(record)
    if previous.get("version") == VERSION and previous.get("signature") == signature:
        return previous
    inputs, outputs = inferred_io(record)
    return {**previous, "version": VERSION, "signature": signature,
            "run_id": previous.get("run_id", record.get("entry_id", "")),
            "inferred_inputs": inputs, "inferred_outputs": outputs}


def capture_job_provenance(entry, *, run_id: str = "") -> None:
    record = {key: getattr(entry, key) for key in
              ("entry_id", "command", "parameters", "working_directory", "processing_tab", "artifacts")}
    provenance = dict(provenance_for_record(record))
    if run_id:
        provenance["run_id"] = run_id
    entry.artifacts["provenance"] = provenance
