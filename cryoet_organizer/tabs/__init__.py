from cryoet_organizer.tabs.base import SidebarTab
from cryoet_organizer.tabs.custom import CustomTab
from cryoet_organizer.tabs.file_registry import FileRegistryTab
from cryoet_organizer.tabs.gallery import GalleryTab
from cryoet_organizer.tabs.particles import ParticlesTab
from cryoet_organizer.tabs.processing import ProcessingTab
from cryoet_organizer.tabs.processing_m import ProcessingMTab
from cryoet_organizer.tabs.project import ProjectOverviewTab
from cryoet_organizer.tabs.shortcuts import ShortcutsTab
from cryoet_organizer.tabs.tomograms import TomogramsTab
from cryoet_organizer.tabs.registry import get_tab_classes

__all__ = [
    "SidebarTab",
    "CustomTab",
    "FileRegistryTab",
    "ProjectOverviewTab",
    "ProcessingTab",
    "ProcessingMTab",
    "ParticlesTab",
    "GalleryTab",
    "ShortcutsTab",
    "TomogramsTab",
    "get_tab_classes",
]
