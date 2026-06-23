from __future__ import annotations

from dataclasses import asdict, dataclass

from cryoet_organizer.project import ProjectData


@dataclass
class EnvironmentDefinition:
    title: str
    activation_command: str = ""

    @classmethod
    def from_dict(cls, payload: dict) -> "EnvironmentDefinition":
        return cls(
            title=str(payload.get("title", "")).strip(),
            activation_command=str(payload.get("activation_command", "")).strip(),
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def get_project_environments(project: ProjectData) -> list[EnvironmentDefinition]:
    payload = getattr(project.state, "environments", [])
    environments = [
        EnvironmentDefinition.from_dict(item)
        for item in payload
        if isinstance(item, dict)
    ]
    return [item for item in environments if item.title]


def set_project_environments(project: ProjectData, environments: list[EnvironmentDefinition]) -> None:
    project.state.environments = [item.to_dict() for item in environments if item.title.strip()]


def environment_titles(project: ProjectData, *, include_none: bool = True) -> list[str]:
    titles = [item.title for item in get_project_environments(project)]
    if include_none:
        return ["None", *titles]
    return titles


def find_environment(project: ProjectData, title: str) -> EnvironmentDefinition | None:
    wanted = title.strip()
    if not wanted or wanted.casefold() == "none":
        return None
    for item in get_project_environments(project):
        if item.title.casefold() == wanted.casefold():
            return item
    return None


def resolve_environment_activation(project: ProjectData, title: str) -> str:
    item = find_environment(project, title)
    return item.activation_command if item is not None else ""
