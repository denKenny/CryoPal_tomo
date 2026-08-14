from __future__ import annotations

from dataclasses import asdict, dataclass

from cryoet_organizer.project import ProjectData


RELION_DEFAULT_LABEL = "<default>"


@dataclass
class RelionProjectDefinition:
    name: str
    root_directory: str = ""
    startup_command: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> "RelionProjectDefinition":
        return cls(
            name=str(payload.get("name", "")).strip(),
            root_directory=str(payload.get("root_directory", "")).strip(),
            startup_command=str(payload.get("startup_command", "")),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def normalize_relion_projects(projects: list[RelionProjectDefinition]) -> list[RelionProjectDefinition]:
    cleaned: list[RelionProjectDefinition] = []
    seen: set[str] = set()
    for item in projects:
        name = item.name.strip()
        if not name or name == RELION_DEFAULT_LABEL:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(
            RelionProjectDefinition(
                name=name,
                root_directory=item.root_directory.strip(),
                startup_command=item.startup_command,
            )
        )
    return cleaned


def get_project_relion_projects(project: ProjectData) -> list[RelionProjectDefinition]:
    return normalize_relion_projects([
        RelionProjectDefinition.from_dict(item)
        for item in project.state.relion_projects
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ])


def set_project_relion_projects(project: ProjectData, projects: list[RelionProjectDefinition]) -> None:
    project.state.relion_projects = [item.to_dict() for item in normalize_relion_projects(projects)]


def get_project_relion_default(project: ProjectData) -> RelionProjectDefinition:
    default = RelionProjectDefinition.from_dict(project.state.relion_project_default)
    return RelionProjectDefinition(
        name=default.name.strip(),
        root_directory=default.root_directory.strip(),
        startup_command=default.startup_command,
    )


def set_project_relion_default(project: ProjectData, default: RelionProjectDefinition) -> None:
    project.state.relion_project_default = {
        "name": default.name.strip(),
        "root_directory": default.root_directory.strip(),
        "startup_command": default.startup_command,
    }


def relion_default_is_empty(project: ProjectData) -> bool:
    default = get_project_relion_default(project)
    return not (default.name.strip() or default.root_directory.strip() or default.startup_command.strip())
