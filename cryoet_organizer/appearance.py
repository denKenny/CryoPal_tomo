from __future__ import annotations

from dataclasses import asdict, dataclass

from cryoet_organizer.project import ProjectData


APPEARANCE_METADATA_KEY = "appearance"


@dataclass(frozen=True)
class AppearanceConfig:
    sidebar_background: str = "#E9EEF2"
    sidebar_button_background: str = "#E9EEF2"
    sidebar_button_foreground: str = "#000000"
    main_background: str = "#F0F0F0"
    main_foreground: str = "#000000"

    @classmethod
    def from_dict(cls, payload: dict | None) -> "AppearanceConfig":
        if not isinstance(payload, dict):
            return cls()
        return cls(
            sidebar_background=str(payload.get("sidebar_background", cls.sidebar_background)),
            sidebar_button_background=str(payload.get("sidebar_button_background", cls.sidebar_button_background)),
            sidebar_button_foreground=str(payload.get("sidebar_button_foreground", cls.sidebar_button_foreground)),
            main_background=str(payload.get("main_background", cls.main_background)),
            main_foreground=str(payload.get("main_foreground", cls.main_foreground)),
        )

    def to_dict(self) -> dict:
        return asdict(self)


def get_project_appearance(project: ProjectData) -> AppearanceConfig:
    return AppearanceConfig.from_dict(project.state.appearance)


def set_project_appearance(project: ProjectData, config: AppearanceConfig) -> None:
    project.state.appearance = config.to_dict()
