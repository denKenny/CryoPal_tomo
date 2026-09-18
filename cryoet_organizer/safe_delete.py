from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


class DeletionCancelled(RuntimeError):
    pass


def identifier_matches_name(name: str, identifiers: Iterable[str]) -> bool:
    """Match identifiers at non-alphanumeric boundaries (TS_1 must not match TS_10)."""
    folded = str(name).casefold()
    for identifier in identifiers:
        candidate = str(identifier).strip().casefold()
        if not candidate:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", folded):
            return True
    return False


def matching_files(root: str | Path, identifiers: Iterable[str]) -> list[Path]:
    folder = Path(root)
    if not folder.is_dir():
        return []
    matches: list[Path] = []
    for path in folder.rglob("*"):
        if ".cryopal_trash" in path.parts or not path.is_file():
            continue
        if identifier_matches_name(path.name, identifiers):
            matches.append(path)
    return sorted(matches, key=lambda item: str(item).casefold())


def exact_stem_files(root: str | Path, stems: Iterable[str]) -> list[Path]:
    folder = Path(root)
    wanted = {str(stem).strip().casefold() for stem in stems if str(stem).strip()}
    if not folder.is_dir() or not wanted:
        return []
    return sorted(
        (
            path
            for path in folder.rglob("*")
            if path.is_file()
            and ".cryopal_trash" not in path.parts
            and path.stem.casefold() in wanted
        ),
        key=lambda item: str(item).casefold(),
    )


def processed_items_rewrite(path: str | Path, identifiers: Iterable[str]) -> tuple[str, int] | None:
    candidate = Path(path)
    if not candidate.is_file():
        return None
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Unexpected processed_items format in {candidate}")
    updated: list[object] = []
    removed = 0
    for item in payload:
        current_path = str(item.get("Path", "")) if isinstance(item, dict) else ""
        current_name = Path(current_path).name
        current_stem = Path(current_path).stem
        if identifier_matches_name(current_name, identifiers) or identifier_matches_name(current_stem, identifiers):
            removed += 1
        else:
            updated.append(item)
    return json.dumps(updated, indent=2, ensure_ascii=False) + "\n", removed


@dataclass(frozen=True)
class _Move:
    source: Path
    root: Path


class QuarantineTransaction:
    """Move deletions to recoverable per-root trash and roll back incomplete work."""

    def __init__(self, *, cancel_event=None, log: Callable[[str], None] | None = None) -> None:
        self.transaction_id = uuid.uuid4().hex
        self.cancel_event = cancel_event
        self.log = log or (lambda _message: None)
        self._moves: list[_Move] = []
        self._rewrites: dict[Path, tuple[Path, str]] = {}
        self._completed: list[tuple[Path, Path]] = []
        self._written: list[Path] = []

    def add_file(self, path: str | Path, *, root: str | Path) -> None:
        source = Path(os.path.abspath(Path(path).expanduser()))
        root_path = Path(os.path.abspath(Path(root).expanduser()))
        try:
            relative = source.relative_to(root_path)
        except ValueError as exc:
            raise ValueError(f"Refusing to remove path outside its declared root: {source}") from exc
        current = root_path
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError(f"Refusing to follow a symlinked directory during deletion: {current}")
        if (source.exists() or source.is_symlink()) and all(item.source != source for item in self._moves):
            self._moves.append(_Move(source=source, root=root_path))

    def add_rewrite(self, path: str | Path, text: str, *, root: str | Path) -> None:
        source = Path(os.path.abspath(Path(path).expanduser()))
        self.add_file(source, root=root)
        self._rewrites[source] = (Path(os.path.abspath(Path(root).expanduser())), text)

    def execute(self) -> int:
        try:
            for item in self._moves:
                self._check_cancel()
                relative = item.source.relative_to(item.root)
                destination = item.root / ".cryopal_trash" / self.transaction_id / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item.source), str(destination))
                self._completed.append((item.source, destination))
                self.log(f"Moved to recoverable trash: {item.source}")

                rewrite = self._rewrites.get(item.source)
                if rewrite is not None:
                    _root, text = rewrite
                    self._atomic_write(item.source, text)
                    self._written.append(item.source)
                    self.log(f"Updated: {item.source}")
            self._check_cancel()
        except Exception:
            self.rollback()
            raise
        return len(self._completed) - len(self._written)

    def rollback(self) -> None:
        for path in reversed(self._written):
            path.unlink(missing_ok=True)
        for source, destination in reversed(self._completed):
            if destination.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        self._written.clear()
        self._completed.clear()
        self.log("Deletion transaction rolled back.")

    def _check_cancel(self) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise DeletionCancelled("Deletion cancelled; all moved files were restored.")

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
