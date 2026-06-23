from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cryoet_organizer.project import ProjectData


_GLOBAL_VIEWER_DEFAULTS_PATH = Path.home() / ".cryopal_tomo_viewers.json"


def normalize_extension(value: str) -> str:
    cleaned = str(value).strip().casefold()
    if not cleaned:
        return ""
    if cleaned.startswith("*."):
        cleaned = cleaned[1:]
    elif cleaned.startswith("*"):
        cleaned = cleaned[1:]
    if not cleaned.startswith("."):
        cleaned = f".{cleaned}"
    return cleaned


def parse_extensions_text(value: str) -> list[str]:
    text = str(value).replace(";", ",").replace("\n", ",")
    tokens = [token.strip() for token in text.split(",")]
    if len(tokens) == 1 and " " in tokens[0]:
        tokens = [token.strip() for token in tokens[0].split()]
    extensions: list[str] = []
    for token in tokens:
        normalized = normalize_extension(token)
        if normalized and normalized not in extensions:
            extensions.append(normalized)
    return extensions


def format_extensions(extensions: list[str]) -> str:
    return ", ".join(f"*{extension}" for extension in extensions if extension)


@dataclass(frozen=True)
class ViewerException:
    command: str
    extensions: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict | None) -> "ViewerException":
        if not isinstance(payload, dict):
            return cls(command="", extensions=[])
        extensions_payload = payload.get("extensions", [])
        extensions = []
        if isinstance(extensions_payload, list):
            for item in extensions_payload:
                normalized = normalize_extension(str(item))
                if normalized and normalized not in extensions:
                    extensions.append(normalized)
        return cls(
            command=str(payload.get("command", "")).strip(),
            extensions=extensions,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ViewerDefaultsConfig:
    exceptions: list[ViewerException] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: dict | None) -> "ViewerDefaultsConfig":
        if not isinstance(payload, dict):
            return default_viewer_defaults()
        exceptions_payload = payload.get("exceptions", [])
        exceptions = []
        if isinstance(exceptions_payload, list):
            for item in exceptions_payload:
                if not isinstance(item, dict):
                    continue
                parsed = ViewerException.from_dict(item)
                if parsed.command and parsed.extensions:
                    exceptions.append(parsed)
        return cls(exceptions=exceptions or default_viewer_defaults().exceptions)

    def to_dict(self) -> dict:
        return {"exceptions": [item.to_dict() for item in self.exceptions if item.command and item.extensions]}


def default_viewer_defaults() -> ViewerDefaultsConfig:
    return ViewerDefaultsConfig(
        exceptions=[
            ViewerException(command="3dmod", extensions=[".mrc", ".mrcs"]),
        ]
    )


def load_global_viewer_defaults() -> ViewerDefaultsConfig:
    try:
        payload = json.loads(_GLOBAL_VIEWER_DEFAULTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return default_viewer_defaults()
    return ViewerDefaultsConfig.from_dict(payload)


def save_global_viewer_defaults(config: ViewerDefaultsConfig) -> None:
    try:
        _GLOBAL_VIEWER_DEFAULTS_PATH.write_text(
            json.dumps(config.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def get_project_viewer_defaults(project: ProjectData) -> ViewerDefaultsConfig | None:
    payload = project.state.viewer_defaults
    if not isinstance(payload, dict):
        return None
    return ViewerDefaultsConfig.from_dict(payload)


def get_effective_viewer_defaults(project: ProjectData) -> ViewerDefaultsConfig:
    return get_project_viewer_defaults(project) or load_global_viewer_defaults()


def set_project_viewer_defaults(project: ProjectData, config: ViewerDefaultsConfig | None) -> None:
    project.state.viewer_defaults = None if config is None else config.to_dict()


def resolve_viewer_command(project: ProjectData, path: str | Path) -> str:
    suffix = Path(path).suffix.casefold()
    if not suffix:
        return ""
    config = get_effective_viewer_defaults(project)
    for item in config.exceptions:
        if suffix in item.extensions:
            return item.command
    return ""
