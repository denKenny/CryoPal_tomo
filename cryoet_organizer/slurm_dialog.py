from __future__ import annotations

from copy import deepcopy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from cryoet_organizer.dialogs import bind_scrollable_canvas, make_copy_name
from cryoet_organizer.slurm import (
    SlurmHeaderField,
    SlurmProfile,
    export_slurm_profiles,
    get_project_slurm_profiles,
    import_slurm_profiles,
    make_header_field_key,
    set_project_slurm_profiles,
)
from cryoet_organizer.settings_shell import decorate_settings_window


class SlurmProfilesDialog:
    def __init__(self, app, host: tk.Misc | None = None) -> None:
        self.app = app
        self.profiles = deepcopy(get_project_slurm_profiles(app.project))
        self.saved_profiles = deepcopy(self.profiles)
        self.current_index: int | None = None
        self.field_rows: list[dict[str, object]] = []
        self.embedded = host is not None

        self.window = host if host is not None else tk.Toplevel(app.root)
        if not self.embedded:
            self.window.title("Slurm submission")
            self.window.geometry("1200x760")
            self.window.transient(app.root)
            self.window.grab_set()
            self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)
        self.content_row = 0 if self.embedded else 1
        self.footer_row = self.content_row + 1
        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(self.content_row, weight=1)

        self.name_var = tk.StringVar()
        self.conda_activate_var = tk.StringVar()

        self._build()
        self._refresh_profile_list()
        if self.profiles:
            self.profile_list.selection_set(0)
            self._on_profile_selected()
        if not self.embedded:
            decorate_settings_window(self, "slurm_profiles")

    def _build(self) -> None:
        if not self.embedded:
            toolbar = ttk.Frame(self.window, padding=12)
            toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")
            toolbar.columnconfigure(0, weight=1)

        left = ttk.LabelFrame(self.window, text="Profiles", padding=12)
        left.grid(row=self.content_row, column=0, sticky="nsw", padx=(12, 8), pady=(0, 12))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.profile_list = tk.Listbox(left, exportselection=False, height=20)
        self.profile_list.grid(row=0, column=0, sticky="nsew")
        left_scroll = ttk.Scrollbar(left, orient="vertical", command=self.profile_list.yview)
        left_scroll.grid(row=0, column=1, sticky="ns")
        self.profile_list.configure(yscrollcommand=left_scroll.set)
        self.profile_list.bind("<<ListboxSelect>>", self._on_profile_selected)
        actions = ttk.Frame(left)
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(actions, text="Add profile", command=self._add_profile).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Clone entry", command=self._clone_selected).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(actions, text="Remove selected", command=self._remove_selected).grid(row=0, column=2, sticky="w", padx=(8, 0))

        right = ttk.LabelFrame(self.window, text="Profile details", padding=12)
        right.grid(row=self.content_row, column=1, sticky="nsew", padx=(0, 12), pady=(0, 12))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(3, weight=1)

        header = ttk.Frame(right)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Profile name").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(header, textvariable=self.name_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))

        fields_box = ttk.LabelFrame(right, text="Header fields", padding=12)
        fields_box.grid(row=1, column=0, sticky="nsew", pady=(0, 12))
        fields_box.columnconfigure(0, weight=1)
        fields_box.rowconfigure(1, weight=1)

        fields_toolbar = ttk.Frame(fields_box)
        fields_toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        fields_toolbar.columnconfigure(0, weight=1)
        ttk.Button(fields_toolbar, text="Add field", command=self._add_field_row).grid(row=0, column=1, sticky="e")

        self.fields_canvas = tk.Canvas(fields_box, highlightthickness=0)
        self.fields_canvas.grid(row=1, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(fields_box, orient="vertical", command=self.fields_canvas.yview)
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(fields_box, orient="horizontal", command=self.fields_canvas.xview)
        xscroll.grid(row=2, column=0, sticky="ew")
        self.fields_canvas.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.fields_frame = ttk.Frame(self.fields_canvas)
        self.fields_frame.columnconfigure(2, weight=1)
        self.fields_window = self.fields_canvas.create_window((0, 0), window=self.fields_frame, anchor="nw")
        bind_scrollable_canvas(self.fields_canvas, self.fields_window, self.fields_frame, allow_horizontal=True)

        ttk.Label(self.fields_frame, text="Flag").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Label(self.fields_frame, text="Description").grid(row=0, column=1, sticky="w", padx=(0, 8))
        ttk.Label(self.fields_frame, text="Value").grid(row=0, column=2, sticky="w", padx=(0, 8))

        footer_box = ttk.LabelFrame(right, text="Environment", padding=12)
        footer_box.grid(row=2, column=0, sticky="ew")
        footer_box.columnconfigure(1, weight=1)
        ttk.Label(footer_box, text="Conda activate").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Entry(footer_box, textvariable=self.conda_activate_var).grid(row=0, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(footer_box, text="Modules (one per line)").grid(row=1, column=0, sticky="nw", pady=(0, 4))
        self.modules_text = tk.Text(footer_box, height=4, wrap="word")
        self.modules_text.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(footer_box, text="Shell preamble").grid(row=2, column=0, sticky="nw", pady=(0, 4))
        self.preamble_text = tk.Text(footer_box, height=8, wrap="word")
        self.preamble_text.grid(row=2, column=1, sticky="nsew", pady=(0, 8))

        buttons = ttk.Frame(self.window, padding=(12, 0, 12, 12))
        buttons.grid(row=self.footer_row, column=0, columnspan=2, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        cancel_label = "Revert section" if self.embedded else "Cancel"
        save_label = "Save section" if self.embedded else "Save"
        ttk.Button(buttons, text=cancel_label, command=self._cancel).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(buttons, text=save_label, command=self._save).grid(row=0, column=2, padx=(8, 0))

    def _new_field_row(self, field: SlurmHeaderField | None = None) -> dict[str, object]:
        row_index = len(self.field_rows) + 1
        flag_var = tk.StringVar(value=field.flag if field else "")
        description_var = tk.StringVar(value=field.description if field else "")
        value_var = tk.StringVar(value=field.value if field else "")
        key = field.key if field else ""

        frame = ttk.Frame(self.fields_frame)
        frame.grid(row=row_index, column=0, columnspan=4, sticky="ew", pady=4)
        frame.columnconfigure(2, weight=1)
        ttk.Entry(frame, textvariable=flag_var, width=16).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Entry(frame, textvariable=description_var, width=28).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Entry(frame, textvariable=value_var).grid(row=0, column=2, sticky="ew", padx=(0, 8))
        ttk.Button(frame, text="Remove", command=lambda current=frame: self._remove_field_row(current)).grid(
            row=0, column=3, sticky="e"
        )
        row = {
            "frame": frame,
            "key": key,
            "flag_var": flag_var,
            "description_var": description_var,
            "value_var": value_var,
        }
        self.field_rows.append(row)
        return row

    def _clear_field_rows(self) -> None:
        for row in self.field_rows:
            frame = row.get("frame")
            if isinstance(frame, ttk.Frame):
                frame.destroy()
        self.field_rows.clear()

    def _remove_field_row(self, frame: ttk.Frame) -> None:
        self.field_rows = [row for row in self.field_rows if row.get("frame") is not frame]
        frame.destroy()
        self._regrid_field_rows()

    def _regrid_field_rows(self) -> None:
        for index, row in enumerate(self.field_rows, start=1):
            frame = row.get("frame")
            if isinstance(frame, ttk.Frame):
                frame.grid_configure(row=index)

    def _header_fields_from_rows(self) -> list[SlurmHeaderField]:
        fields: list[SlurmHeaderField] = []
        existing_keys: set[str] = set()
        for row in self.field_rows:
            flag = str(row["flag_var"].get()).strip()
            description = str(row["description_var"].get()).strip()
            value = str(row["value_var"].get()).strip()
            if not flag:
                continue
            key = str(row.get("key", "")).strip()
            if not key:
                key = make_header_field_key(flag, description, existing_keys)
                row["key"] = key
            existing_keys.add(key)
            fields.append(SlurmHeaderField(key=key, flag=flag, description=description, value=value))
        return fields

    def _profile_from_form(self) -> SlurmProfile:
        return SlurmProfile(
            name=self.name_var.get().strip(),
            header_fields=self._header_fields_from_rows(),
            modules=self.modules_text.get("1.0", "end").strip(),
            conda_activate=self.conda_activate_var.get().strip(),
            shell_preamble=self.preamble_text.get("1.0", "end").strip(),
        )

    def _write_current_profile(self) -> None:
        if self.current_index is None or not (0 <= self.current_index < len(self.profiles)):
            return
        self.profiles[self.current_index] = self._profile_from_form()
        self._refresh_profile_list(preserve_selection=True)

    def _load_profile(self, profile: SlurmProfile) -> None:
        self.name_var.set(profile.name)
        self.conda_activate_var.set(profile.conda_activate)
        self.modules_text.delete("1.0", "end")
        self.modules_text.insert("1.0", profile.modules)
        self.preamble_text.delete("1.0", "end")
        self.preamble_text.insert("1.0", profile.shell_preamble)
        self._clear_field_rows()
        for field in profile.header_fields:
            self._new_field_row(field)
        if not profile.header_fields:
            self._add_field_row()

    def _clear_form(self) -> None:
        self.name_var.set("")
        self.conda_activate_var.set("")
        self.modules_text.delete("1.0", "end")
        self.preamble_text.delete("1.0", "end")
        self._clear_field_rows()

    def _refresh_profile_list(self, preserve_selection: bool = False) -> None:
        current = self.current_index if preserve_selection else None
        self.profile_list.delete(0, "end")
        for profile in self.profiles:
            self.profile_list.insert("end", profile.name or "(Unnamed profile)")
        if current is not None and 0 <= current < len(self.profiles):
            self.profile_list.selection_clear(0, "end")
            self.profile_list.selection_set(current)

    def _on_profile_selected(self, _event=None) -> None:
        selection = self.profile_list.curselection()
        if not selection:
            self.current_index = None
            self._clear_form()
            return
        self._write_current_profile()
        self.current_index = int(selection[0])
        self._load_profile(self.profiles[self.current_index])

    def _add_profile(self) -> None:
        self._write_current_profile()
        self.profiles.append(SlurmProfile(name="New profile"))
        self._refresh_profile_list()
        self.current_index = len(self.profiles) - 1
        self.profile_list.selection_clear(0, "end")
        self.profile_list.selection_set(self.current_index)
        self._load_profile(self.profiles[self.current_index])

    def _remove_selected(self) -> None:
        selection = self.profile_list.curselection()
        if not selection:
            return
        index = int(selection[0])
        del self.profiles[index]
        self.current_index = None
        self._refresh_profile_list()
        if self.profiles:
            self.profile_list.selection_set(min(index, len(self.profiles) - 1))
            self._on_profile_selected()
        else:
            self._clear_form()

    def _clone_selected(self) -> None:
        selection = self.profile_list.curselection()
        if len(selection) != 1:
            messagebox.showinfo("Clone Slurm profile", "Please select exactly one Slurm profile first.", parent=self.window)
            return
        self._write_current_profile()
        index = int(selection[0])
        if not (0 <= index < len(self.profiles)):
            return
        cloned = deepcopy(self.profiles[index])
        cloned.name = make_copy_name([profile.name for profile in self.profiles], cloned.name)
        self.profiles.append(cloned)
        self.current_index = len(self.profiles) - 1
        self._refresh_profile_list()
        self.profile_list.selection_clear(0, "end")
        self.profile_list.selection_set(self.current_index)
        self._load_profile(self.profiles[self.current_index])

    def _add_field_row(self) -> None:
        self._new_field_row()

    def _import_profiles(self) -> None:
        path = filedialog.askopenfilename(
            title="Import Slurm profiles",
            filetypes=[("CryoPal Slurm profiles", "*.cryopal.slurm.json"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            imported = import_slurm_profiles(path)
        except Exception as exc:
            messagebox.showerror("Import failed", str(exc))
            return
        existing = {profile.name for profile in self.profiles}
        for profile in imported:
            if not profile.name or profile.name in existing:
                continue
            self.profiles.append(profile)
            existing.add(profile.name)
        self._refresh_profile_list()

    def _export_profiles(self) -> None:
        self._write_current_profile()
        path = filedialog.asksaveasfilename(
            title="Export Slurm profiles",
            defaultextension=".cryopal.slurm.json",
            filetypes=[("CryoPal Slurm profiles", "*.cryopal.slurm.json"), ("JSON", "*.json")],
        )
        if not path:
            return
        try:
            export_slurm_profiles(path, self.profiles)
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))
            return
        self.app.status_var.set("Exported Slurm profiles")

    def _save(self) -> None:
        self.save_section(close_window=False)

    def save_section(self, *, close_window: bool = False) -> bool:
        self._write_current_profile()
        clean_profiles = [profile for profile in self.profiles if profile.name]
        set_project_slurm_profiles(self.app.project, clean_profiles)
        self.saved_profiles = deepcopy(clean_profiles)
        self.app._apply_project_to_tabs()
        self.app._update_title()
        self.app.status_var.set("Saved Slurm submission profiles")
        if close_window:
            self.window.destroy()
        return True

    def has_unsaved_changes(self) -> bool:
        self._write_current_profile()
        return self.profiles != self.saved_profiles

    def _cancel(self) -> None:
        self.profiles = deepcopy(self.saved_profiles)
        self.current_index = None
        self._refresh_profile_list()
        if self.profiles:
            self.profile_list.selection_set(0)
            self._on_profile_selected()
        else:
            self._clear_form()
