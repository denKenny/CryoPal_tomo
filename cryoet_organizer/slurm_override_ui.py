from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from cryoet_organizer.slurm import (
    SlurmProfile,
    decode_slurm_overrides,
    encode_slurm_overrides,
    find_slurm_profile,
    profile_header_fields,
    profile_memory_choice,
    profile_memory_field_keys,
)


class SlurmOverrideUI:
    def __init__(self, app, profile_var: tk.StringVar) -> None:
        self.app = app
        self.profile_var = profile_var
        self.frames: list[ttk.Frame] = []
        self.override_vars: dict[str, tk.StringVar] = {}
        self.memory_choice_var = tk.StringVar()
        self._current_profile_name = ""

    def register_frame(self, frame: ttk.Frame) -> None:
        if frame not in self.frames:
            self.frames.append(frame)

    def rebuild(self, parameters: dict[str, str] | None = None, *, preserve_existing: bool = True) -> None:
        profile = self.current_profile()
        existing_values = {key: var.get() for key, var in self.override_vars.items()} if preserve_existing else {}
        existing_memory_choice = self.memory_choice_var.get()

        if parameters is not None:
            values, memory_choice = decode_slurm_overrides(profile, parameters)
        elif self._current_profile_name == self.profile_var.get().strip():
            values = existing_values
            memory_choice = existing_memory_choice
        else:
            values, memory_choice = decode_slurm_overrides(profile, {})

        self.override_vars = {}
        self._current_profile_name = self.profile_var.get().strip()

        for frame in self.frames:
            for child in frame.winfo_children():
                child.destroy()
            for column in range(4):
                frame.columnconfigure(column, weight=1 if column % 2 == 1 else 0)

        if profile is None:
            for frame in self.frames:
                ttk.Label(frame, text="Please select Slurm profile.").grid(row=0, column=0, sticky="w")
            self.memory_choice_var.set("")
            return

        fields = profile_header_fields(profile)
        memory_keys = profile_memory_field_keys(profile)
        if memory_keys:
            chosen = memory_choice or profile_memory_choice(profile)
            self.memory_choice_var.set(chosen)

        for frame in self.frames:
            row = 0
            if len(memory_keys) > 1:
                ttk.Label(frame, text="Memory field").grid(row=row, column=0, sticky="w", padx=(0, 6), pady=(0, 6))
                memory_options = [
                    field.key
                    for field in fields
                    if field.key in memory_keys
                ]
                labels = {
                    field.key: f"{field.flag} ({field.description or field.key})"
                    for field in fields
                    if field.key in memory_keys
                }
                combo = ttk.Combobox(
                    frame,
                    textvariable=self.memory_choice_var,
                    state="readonly",
                    values=memory_options,
                    width=24,
                )
                combo.grid(row=row, column=1, sticky="ew", pady=(0, 6))
                combo.bind("<<ComboboxSelected>>", lambda _e: self.rebuild(preserve_existing=True))
                row += 1

            visible_fields = [
                field
                for field in fields
                if not (field.key in memory_keys and len(memory_keys) > 1 and field.key != self.memory_choice_var.get())
            ]
            for index, field in enumerate(visible_fields):
                pair = index % 2
                if pair == 0 and index > 0:
                    row += 1
                base_column = pair * 2
                ttk.Label(frame, text=field.flag).grid(
                    row=row,
                    column=base_column,
                    sticky="w",
                    padx=(0, 6 if pair == 0 else 4),
                    pady=(0, 6),
                )
                var = self.override_vars.get(field.key)
                if var is None:
                    var = tk.StringVar(value=values.get(field.key, field.value))
                    self.override_vars[field.key] = var
                entry = ttk.Entry(frame, textvariable=var)
                entry.grid(
                    row=row,
                    column=base_column + 1,
                    sticky="ew",
                    padx=(0, 12 if pair == 0 else 0),
                    pady=(0, 6),
                )

    def current_profile(self) -> SlurmProfile | None:
        profile_name = self.profile_var.get().strip()
        if not profile_name:
            return None
        return find_slurm_profile(self.app.project, profile_name)

    def refresh_profile_choices(self, combos: list[ttk.Combobox]) -> None:
        profiles = self.app.slurm_profile_names()
        for combo in combos:
            combo.configure(values=profiles)
        if self.profile_var.get() and self.profile_var.get() not in profiles:
            self.profile_var.set("")
        self.rebuild(preserve_existing=False)

    def metadata(self) -> dict[str, str]:
        return encode_slurm_overrides(
            self.current_profile(),
            {key: var.get() for key, var in self.override_vars.items()},
            memory_choice=self.memory_choice_var.get(),
        )
