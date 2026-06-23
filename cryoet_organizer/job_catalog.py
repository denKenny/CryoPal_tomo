from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CatalogField:
    key: str
    label: str
    widget: str = "text"
    default_value: str = ""
    required: bool = False
    advanced: bool = False
    description: str = ""
    options: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class CatalogJob:
    namespace: str
    group: str
    job_key: str
    title: str
    fields: tuple[CatalogField, ...] = field(default_factory=tuple)

