from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable

from cryoet_organizer.preferences import project_preference_int
from cryoet_organizer.project import ProjectData


LAYOUT_PREFERENCE_PREFIX = "layout."


def layout_preference_key(namespace: str, item: str, dimension: str = "height") -> str:
    return f"{LAYOUT_PREFERENCE_PREFIX}{namespace}.{item}.{dimension}"


def clear_layout_preferences(project: ProjectData, namespace: str | None = None) -> None:
    prefix = LAYOUT_PREFERENCE_PREFIX if namespace is None else f"{LAYOUT_PREFERENCE_PREFIX}{namespace}."
    stale_keys = [key for key in project.state.preferences if key.startswith(prefix)]
    for key in stale_keys:
        project.state.preferences.pop(key, None)


def load_layout_value(
    project: ProjectData,
    namespace: str,
    item: str,
    *,
    dimension: str = "height",
    default: int,
    minimum: int,
    maximum: int = 4000,
) -> int:
    return project_preference_int(
        project,
        layout_preference_key(namespace, item, dimension),
        default=default,
        minimum=minimum,
        maximum=maximum,
    )


def save_layout_value(
    project: ProjectData,
    namespace: str,
    item: str,
    value: int,
    *,
    dimension: str = "height",
) -> None:
    project.state.preferences[layout_preference_key(namespace, item, dimension)] = str(max(1, int(value)))


@dataclass
class _Section:
    key: str
    wrapper: ttk.Frame
    content: ttk.Frame
    handle: tk.Frame
    default_height: int
    min_height: int
    current_height: int
    visible: bool = True


class ResizableSectionStack(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        app,
        preference_namespace: str,
        bottom_spacing: int = 96,
        show_last_handle: bool = True,
        on_layout_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.preference_namespace = preference_namespace
        self.bottom_spacing = max(24, int(bottom_spacing))
        self.show_last_handle = show_last_handle
        self.on_layout_changed = on_layout_changed
        self.columnconfigure(0, weight=1)
        self._sections: list[_Section] = []
        self._section_map: dict[str, _Section] = {}
        self._dragging_key: str | None = None
        self._drag_start_y = 0
        self._drag_start_height = 0
        self._spacer = ttk.Frame(self, height=self.bottom_spacing)
        self._spacer.grid_propagate(False)

    def add_section(self, key: str, *, default_height: int, min_height: int) -> ttk.Frame:
        handle_height = max(8, int(self.app._scale_pixels(8)))
        wrapper = ttk.Frame(self, height=default_height)
        wrapper.columnconfigure(0, weight=1)
        wrapper.rowconfigure(0, weight=1)
        wrapper.grid_propagate(False)

        content = ttk.Frame(wrapper)
        content.grid(row=0, column=0, sticky="nsew")
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        wrapper.rowconfigure(1, weight=0)

        handle = tk.Frame(
            wrapper,
            height=handle_height,
            cursor="sb_v_double_arrow",
            bd=0,
            highlightthickness=0,
        )
        handle.grid(row=1, column=0, sticky="ew")
        separator = ttk.Separator(handle, orient="horizontal")
        separator.place(relx=0.0, rely=0.5, relwidth=1.0, anchor="w")
        for widget in (handle, separator):
            widget.bind("<ButtonPress-1>", lambda event, current=key: self._start_drag(current, event))
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<ButtonRelease-1>", self._finish_drag)

        section = _Section(
            key=key,
            wrapper=wrapper,
            content=content,
            handle=handle,
            default_height=max(min_height, default_height),
            min_height=max(40, min_height),
            current_height=max(min_height, default_height),
        )
        self._sections.append(section)
        self._section_map[key] = section
        self._regrid_sections()
        return content

    def set_section_visible(self, key: str, visible: bool) -> None:
        section = self._section_map.get(key)
        if section is None or section.visible == visible:
            return
        section.visible = visible
        self._regrid_sections()
        self._notify_layout_changed()

    def section_frame(self, key: str) -> ttk.Frame:
        return self._section_map[key].content

    def reset_to_defaults(self) -> None:
        for section in self._sections:
            section.current_height = section.default_height
            section.wrapper.configure(height=section.current_height)
        self._notify_layout_changed()

    def restore_from_project(self, project: ProjectData) -> None:
        for section in self._sections:
            section.current_height = load_layout_value(
                project,
                self.preference_namespace,
                section.key,
                default=section.default_height,
                minimum=section.min_height,
            )
            section.wrapper.configure(height=section.current_height)
        self._notify_layout_changed()

    def write_to_project(self, project: ProjectData) -> None:
        for section in self._sections:
            save_layout_value(project, self.preference_namespace, section.key, section.current_height)

    def resize_section(self, key: str, delta: int) -> int:
        section = self._section_map.get(key)
        if section is None or not section.visible:
            return 0
        requested = int(delta)
        if requested == 0:
            return 0
        previous = section.current_height
        section.current_height = max(section.min_height, previous + requested)
        applied = section.current_height - previous
        if applied == 0:
            return 0
        section.wrapper.configure(height=section.current_height)
        self._notify_layout_changed()
        return applied

    def _visible_sections(self) -> list[_Section]:
        return [section for section in self._sections if section.visible]

    def _regrid_sections(self) -> None:
        visible_sections = self._visible_sections()
        for row_index in range(len(self._sections) + 1):
            self.rowconfigure(row_index, weight=0)
        for section in self._sections:
            section.wrapper.grid_remove()
        for row_index, section in enumerate(visible_sections):
            section.wrapper.configure(height=max(section.min_height, section.current_height))
            section.wrapper.grid(row=row_index, column=0, sticky="ew")
            if self.show_last_handle or row_index < len(visible_sections) - 1:
                section.handle.grid(row=1, column=0, sticky="ew")
            else:
                section.handle.grid_remove()
        spacer_row = len(visible_sections)
        self._spacer.configure(height=self.bottom_spacing)
        self._spacer.grid(row=spacer_row, column=0, sticky="nsew")
        self.rowconfigure(spacer_row, weight=1)

    def _start_drag(self, key: str, event) -> None:
        section = self._section_map.get(key)
        if section is None or not section.visible:
            return
        self._dragging_key = key
        self._drag_start_y = int(event.y_root)
        self._drag_start_height = section.current_height

    def _drag(self, event) -> None:
        if not self._dragging_key:
            return
        section = self._section_map.get(self._dragging_key)
        if section is None or not section.visible:
            return
        delta = int(event.y_root) - self._drag_start_y
        new_height = max(section.min_height, self._drag_start_height + delta)
        if new_height == section.current_height:
            return
        section.current_height = new_height
        section.wrapper.configure(height=new_height)
        self._notify_layout_changed()

    def _finish_drag(self, _event=None) -> None:
        if not self._dragging_key:
            return
        self._dragging_key = None
        self._notify_layout_changed()

    def _notify_layout_changed(self) -> None:
        self.update_idletasks()
        if callable(self.on_layout_changed):
            self.on_layout_changed()


class VerticalSplitPane(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        *,
        app,
        preference_namespace: str,
        default_top_height: int,
        min_top_height: int,
        min_bottom_height: int,
        resize_parent_by: Callable[[int], int] | None = None,
        on_layout_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.preference_namespace = preference_namespace
        self.default_top_height = max(min_top_height, int(default_top_height))
        self.min_top_height = max(40, int(min_top_height))
        self.min_bottom_height = max(40, int(min_bottom_height))
        self.current_top_height = self.default_top_height
        self.resize_parent_by = resize_parent_by
        self.on_layout_changed = on_layout_changed
        self._drag_start_y = 0
        self._drag_start_height = 0
        self._resize_after_id: str | None = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1, minsize=self.min_bottom_height)

        self._handle_height = max(8, int(self.app._scale_pixels(8)))

        self.top_wrapper = ttk.Frame(self, height=self.current_top_height)
        self.top_wrapper.grid(row=0, column=0, sticky="ew")
        self.top_wrapper.columnconfigure(0, weight=1)
        self.top_wrapper.rowconfigure(0, weight=1)
        self.top_wrapper.grid_propagate(False)

        self.top_content = ttk.Frame(self.top_wrapper)
        self.top_content.grid(row=0, column=0, sticky="nsew")
        self.top_content.columnconfigure(0, weight=1)
        self.top_content.rowconfigure(0, weight=1)

        self.handle = tk.Frame(
            self,
            height=self._handle_height,
            cursor="sb_v_double_arrow",
            bd=0,
            highlightthickness=0,
        )
        self.handle.grid(row=1, column=0, sticky="ew")
        separator = ttk.Separator(self.handle, orient="horizontal")
        separator.place(relx=0.0, rely=0.5, relwidth=1.0, anchor="w")
        for widget in (self.handle, separator):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._drag)
            widget.bind("<ButtonRelease-1>", self._finish_drag)

        self.bottom_wrapper = ttk.Frame(self)
        self.bottom_wrapper.grid(row=2, column=0, sticky="nsew")
        self.bottom_wrapper.columnconfigure(0, weight=1)
        self.bottom_wrapper.rowconfigure(0, weight=1)

        self.bottom_content = ttk.Frame(self.bottom_wrapper)
        self.bottom_content.grid(row=0, column=0, sticky="nsew")
        self.bottom_content.columnconfigure(0, weight=1)
        self.bottom_content.rowconfigure(0, weight=1)

        self.bind("<Configure>", self._schedule_reconcile_height)
        self.after_idle(self._apply_top_height)

    def top_frame(self) -> ttk.Frame:
        return self.top_content

    def bottom_frame(self) -> ttk.Frame:
        return self.bottom_content

    def reset_to_defaults(self) -> None:
        self.current_top_height = self.default_top_height
        self._apply_top_height()
        self._notify_layout_changed()

    def restore_from_project(self, project: ProjectData) -> None:
        self.current_top_height = load_layout_value(
            project,
            self.preference_namespace,
            "top",
            default=self.default_top_height,
            minimum=self.min_top_height,
        )
        self._apply_top_height()
        self._notify_layout_changed()

    def write_to_project(self, project: ProjectData) -> None:
        save_layout_value(project, self.preference_namespace, "top", self.current_top_height)

    def _available_top_height(self) -> int:
        total_height = max(0, int(self.winfo_height()))
        if total_height <= self._handle_height + 1:
            return max(self.min_top_height, int(self.current_top_height))
        available = total_height - self._handle_height - self.min_bottom_height
        return max(self.min_top_height, available)

    def _apply_top_height(self) -> None:
        clamped = max(self.min_top_height, min(int(self.current_top_height), self._available_top_height()))
        self.current_top_height = clamped
        self.rowconfigure(0, minsize=clamped)
        self.top_wrapper.configure(height=clamped)

    def _schedule_reconcile_height(self, _event=None) -> None:
        if self._resize_after_id is not None:
            self.after_cancel(self._resize_after_id)
        self._resize_after_id = self.after_idle(self._reconcile_height)

    def _reconcile_height(self) -> None:
        self._resize_after_id = None
        previous = self.current_top_height
        self._apply_top_height()
        if self.current_top_height != previous:
            self._notify_layout_changed()

    def _start_drag(self, event) -> None:
        self._drag_start_y = int(event.y_root)
        self._drag_start_height = self.current_top_height

    def _drag(self, event) -> None:
        pointer_delta = int(event.y_root) - self._drag_start_y
        requested_height = max(self.min_top_height, self._drag_start_height + pointer_delta)
        if self.resize_parent_by is None:
            requested_height = min(requested_height, self._available_top_height())
        requested_delta = requested_height - self.current_top_height
        if requested_delta == 0:
            return
        applied_delta = requested_delta
        if self.resize_parent_by is not None:
            applied_delta = self.resize_parent_by(requested_delta)
            if applied_delta == 0:
                return
        self.current_top_height = max(self.min_top_height, self.current_top_height + applied_delta)
        self._apply_top_height()
        self._notify_layout_changed()

    def _finish_drag(self, _event=None) -> None:
        self._notify_layout_changed()

    def _notify_layout_changed(self) -> None:
        self.update_idletasks()
        if callable(self.on_layout_changed):
            self.on_layout_changed()
