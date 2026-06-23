from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


@dataclass
class MPopulationSpecies:
    guid: str = ""
    path: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "guid": self.guid,
            "path": self.path,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "MPopulationSpecies":
        return cls(
            guid=str(payload.get("guid", "")).strip(),
            path=str(payload.get("path", "")).strip(),
            name=str(payload.get("name", "")).strip(),
        )


@dataclass
class MPopulationSource:
    guid: str = ""
    path: str = ""
    name: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "guid": self.guid,
            "path": self.path,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "MPopulationSource":
        return cls(
            guid=str(payload.get("guid", "")).strip(),
            path=str(payload.get("path", "")).strip(),
            name=str(payload.get("name", "")).strip(),
        )


@dataclass
class ParsedMPopulation:
    name: str
    directory: str
    population_file: str
    species: list[MPopulationSpecies] = field(default_factory=list)
    sources: list[MPopulationSource] = field(default_factory=list)


def _display_name_from_path(path_value: str) -> str:
    if not path_value.strip():
        return ""
    return Path(path_value).stem


def parse_population_file(path: str | Path) -> ParsedMPopulation:
    population_path = Path(path).expanduser().resolve()
    tree = ET.parse(population_path)
    root = tree.getroot()
    if root.tag != "Population":
        raise ValueError("Invalid .population file: expected a Population root element.")

    internal_name = ""
    for param in root.findall("./Param"):
        if str(param.attrib.get("Name", "")).strip() == "Name":
            internal_name = str(param.attrib.get("Value", "")).strip()
            break
    population_name = internal_name or population_path.stem

    species: list[MPopulationSpecies] = []
    for item in root.findall("./Species/Species"):
        path_value = str(item.attrib.get("Path", "")).strip()
        species.append(
            MPopulationSpecies(
                guid=str(item.attrib.get("GUID", "")).strip(),
                path=path_value,
                name=_display_name_from_path(path_value),
            )
        )

    sources: list[MPopulationSource] = []
    for item in root.findall("./Sources/Source"):
        path_value = str(item.attrib.get("Path", "")).strip()
        sources.append(
            MPopulationSource(
                guid=str(item.attrib.get("GUID", "")).strip(),
                path=path_value,
                name=_display_name_from_path(path_value),
            )
        )

    return ParsedMPopulation(
        name=population_name,
        directory=str(population_path.parent),
        population_file=population_path.name,
        species=species,
        sources=sources,
    )
