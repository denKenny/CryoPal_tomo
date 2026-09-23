"""Shared assignment editor for the builder and Settings."""
import tkinter as tk
from tkinter import ttk

from cryoet_organizer.custom_jobs import CUSTOM_JOB_TARGETS, custom_job_groups, custom_job_assignment_error


class CustomJobAssignment(ttk.Frame):
    def __init__(self, parent):
        # Make room immediately below Job name in either editor.
        for child in parent.grid_slaves():
            row = int(child.grid_info()["row"])
            if row >= 1:
                child.grid_configure(row=row + 1)
        ttk.Label(parent, text="Additional Processing tab").grid(row=1, column=0, sticky="w")
        super().__init__(parent)
        self.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        self.tab_var = tk.StringVar(value="None")
        self.group_var = tk.StringVar()
        self.tab_combo = ttk.Combobox(self, textvariable=self.tab_var, state="readonly",
                                      values=tuple(CUSTOM_JOB_TARGETS.values()), width=27)
        self.tab_combo.grid(row=0, column=0, sticky="w")
        self.group_combo = ttk.Combobox(self, textvariable=self.group_var, state="readonly", width=20)
        self.group_combo.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.message = ttk.Label(self, style="Error.TLabel", wraplength=650)
        self.message.grid(row=1, column=0, columnspan=2, sticky="w")
        self.tab_combo.bind("<<ComboboxSelected>>", self._changed)
        self.group_combo.bind("<<ComboboxSelected>>", lambda _event: self._validate())
        self._changed()

    def values(self):
        tab = next((key for key, label in CUSTOM_JOB_TARGETS.items() if label == self.tab_var.get()), self.tab_var.get())
        group = next((key for key, label in custom_job_groups(tab).items() if label == self.group_var.get()), self.group_var.get())
        return tab, group

    def error(self):
        return custom_job_assignment_error(*self.values())

    def load(self, tab="", group=""):
        self.tab_var.set(CUSTOM_JOB_TARGETS.get(tab, tab))
        self._changed()
        self.group_var.set(custom_job_groups(tab).get(group, group))
        self._validate()

    def _changed(self, _event=None):
        tab, _ = self.values()
        groups = custom_job_groups(tab)
        self.group_var.set("")
        self.group_combo.configure(values=tuple(groups.values()))
        if groups:
            self.group_combo.grid()
        else:
            self.group_combo.grid_remove()
        self._validate()

    def _validate(self):
        self.message.configure(text=self.error())
