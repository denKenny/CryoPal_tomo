from __future__ import annotations

import hashlib
import shlex
import threading
from tkinter import messagebox, ttk
import tkinter as tk

from cryoet_organizer.dialogs import show_detail_dialog
from cryoet_organizer.job_execution import (
    history_timestamp_now,
    is_scheduled_history_entry,
    slurm_override_payload,
)
from cryoet_organizer.job_queue import (
    ScheduledJobRef,
    assign_queue_order,
    display_ref_timestamp,
    iter_job_history_refs,
    iter_scheduled_job_refs,
    ref_cwd,
    ref_dataset_name,
    remove_job_ref_from_project,
    reverse_timestamp_sort_key,
)
from cryoet_organizer.log_window import BatchCommandOutputWindow
from cryoet_organizer.performance import TkDebouncer, chunked_treeview_replace
from cryoet_organizer.project import JobHistoryEntry, ProjectData
from cryoet_organizer.scheduled_slurm_dialog import CollectiveSlurmSubmissionDialog, ask_scheduled_slurm_mode
from cryoet_organizer.slurm import SlurmSubmissionResult, find_slurm_profile, render_sbatch_script, wait_for_slurm_job
from cryoet_organizer.tabs.base import SidebarTab
from cryoet_organizer.workflow_editor import WorkflowEditorDialog


class JobListTab(SidebarTab):
    tab_id = "job_list"
    title = "Global Job List"
    refresh_domains = ("processing", "processing_m", "tomograms", "particles", "custom", "job_queue", "datasets")
    _COLOR_PALETTE = (
        "#ececec",
        "#dbeeff",
        "#dff4d8",
        "#fff0c9",
        "#eadff8",
        "#f8dddd",
        "#dff2f4",
        "#f0eadf",
        "#e3edf7",
        "#edf1d6",
    )

    def build(self) -> None:
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)
        self.processing_filter_var = tk.StringVar(value="All")
        self.action_filter_var = tk.StringVar(value="All")
        self.dataset_filter_var = tk.StringVar(value="All")
        self.search_var = tk.StringVar()
        self.color_by_var = tk.StringVar(value="Action")
        self.sort_column = "queue_order"
        self.sort_descending = False
        self.refs_by_id: dict[str, ScheduledJobRef] = {}
        self._table_generation = 0
        self._refresh_table_debouncer = TkDebouncer(self.frame, self._refresh_table, delay_ms=120)

        filters = ttk.LabelFrame(self.frame, text="Global job filters", padding=12)
        filters.grid(row=0, column=0, sticky="ew")
        for column in (1, 3, 5, 7):
            filters.columnconfigure(column, weight=1)
        ttk.Label(filters, text="Processing tab").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.processing_filter = ttk.Combobox(filters, textvariable=self.processing_filter_var, state="readonly", width=20)
        self.processing_filter.grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ttk.Label(filters, text="Action").grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.action_filter = ttk.Combobox(filters, textvariable=self.action_filter_var, state="readonly", width=14)
        self.action_filter.grid(row=0, column=3, sticky="ew", padx=(0, 12))
        ttk.Label(filters, text="Dataset").grid(row=0, column=4, sticky="w", padx=(0, 6))
        self.dataset_filter = ttk.Combobox(filters, textvariable=self.dataset_filter_var, state="readonly", width=20)
        self.dataset_filter.grid(row=0, column=5, sticky="ew", padx=(0, 12))
        ttk.Label(filters, text="Search").grid(row=0, column=6, sticky="w", padx=(0, 6))
        ttk.Entry(filters, textvariable=self.search_var).grid(row=0, column=7, sticky="ew")
        ttk.Label(filters, text="Color jobs by").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(8, 0))
        self.color_by_combo = ttk.Combobox(
            filters,
            textvariable=self.color_by_var,
            state="readonly",
            values=("Action", "Processing tab", "Dataset", "Mode"),
            width=20,
        )
        self.color_by_combo.grid(row=1, column=1, sticky="ew", padx=(0, 12), pady=(8, 0))
        ttk.Button(filters, text="Refresh", command=self.refresh_queue).grid(row=1, column=7, sticky="e", pady=(8, 0))
        for variable in (
            self.processing_filter_var,
            self.action_filter_var,
            self.dataset_filter_var,
            self.search_var,
            self.color_by_var,
        ):
            variable.trace_add("write", lambda *_args: self._refresh_table_debouncer.schedule())

        box = ttk.LabelFrame(self.frame, text="Global jobs", padding=12)
        box.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        columns = ("processing_tab", "action", "dataset", "job_name", "timestamp", "mode")
        self.table = ttk.Treeview(box, columns=columns, show="headings", height=16, style="Technical.Treeview")
        headings = {
            "processing_tab": "Processing tab",
            "action": "Action",
            "dataset": "Dataset",
            "job_name": "Job",
            "timestamp": "Timestamp",
            "mode": "Mode",
        }
        widths = {
            "processing_tab": 170,
            "action": 100,
            "dataset": 180,
            "job_name": 240,
            "timestamp": 190,
            "mode": 90,
        }
        for column in columns:
            self.table.heading(column, text=headings[column], command=lambda current=column: self._sort_by(current))
            self.table.column(column, width=widths[column], anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        self.table.tag_configure("scheduled", background="#ececec")
        self.table.tag_configure("waiting", background="#dbeeff")
        self.table.tag_configure("running", background="#dff4d8")
        self.table.tag_configure("completed", background="#dde8ff")
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(box)
        actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(4, weight=1)
        ttk.Button(actions, text="Move up", command=lambda: self._move_selected(-1)).grid(row=0, column=0, sticky="w")
        ttk.Button(actions, text="Move down", command=lambda: self._move_selected(1)).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(actions, text="Remove selected", command=self._remove_selected).grid(row=0, column=2, sticky="w", padx=(8, 0))
        workflow_button = ttk.Menubutton(actions, text="Workflows")
        workflow_menu = tk.Menu(workflow_button, tearoff=False)
        workflow_menu.add_command(label="Add selected jobs as workflow", command=self._add_selected_jobs_as_workflow)
        workflow_menu.add_command(label="Edit and Run existing workflows", command=self._edit_and_run_workflows)
        workflow_button.configure(menu=workflow_menu)
        workflow_button.grid(row=0, column=3, sticky="w", padx=(16, 0))
        ttk.Button(actions, text="Run scheduled jobs", command=lambda: self._execute_queue(force_slurm=False)).grid(
            row=0,
            column=5,
            sticky="e",
            padx=(8, 0),
        )
        ttk.Button(actions, text="Submit scheduled jobs to Slurm", command=lambda: self._execute_queue(force_slurm=True)).grid(
            row=0,
            column=6,
            sticky="e",
            padx=(8, 0),
        )
        abort = ttk.Button(actions, text="Abort", command=self.app.abort_running_commands, state="disabled")
        abort.grid(row=0, column=7, sticky="e", padx=(8, 0))
        self.app.attach_abort_button(abort)
        self.table.bind("<Double-1>", self._show_selected_details)

    def on_project_loaded(self, project: ProjectData) -> None:
        self.refresh_queue()

    def on_tab_shown(self) -> None:
        self.refresh_queue()

    def refresh_queue(self) -> None:
        scheduled_refs = iter_scheduled_job_refs(self.app.project)
        if any("queue_order" not in ref.entry.artifacts for ref in scheduled_refs):
            assign_queue_order(scheduled_refs)
        refs = iter_job_history_refs(self.app.project)
        self._refresh_filter_options(refs)
        self._refresh_table()

    def _refresh_filter_options(self, refs: list[ScheduledJobRef]) -> None:
        processing_values = ["All"] + sorted({ref.entry.processing_tab or "-" for ref in refs})
        action_values = ["All"] + sorted({ref.entry.action or "-" for ref in refs})
        dataset_values = ["All"] + sorted({ref_dataset_name(ref) or "-" for ref in refs})
        self.processing_filter.configure(values=processing_values)
        self.action_filter.configure(values=action_values)
        self.dataset_filter.configure(values=dataset_values)
        for variable, values in (
            (self.processing_filter_var, processing_values),
            (self.action_filter_var, action_values),
            (self.dataset_filter_var, dataset_values),
        ):
            if variable.get() not in values:
                variable.set("All")

    def _filtered_refs(self) -> list[ScheduledJobRef]:
        refs = iter_job_history_refs(self.app.project)
        text = self.search_var.get().strip().casefold()
        filtered: list[ScheduledJobRef] = []
        for ref in refs:
            processing_tab = ref.entry.processing_tab or "-"
            action = ref.entry.action or "-"
            dataset = ref_dataset_name(ref) or "-"
            haystack = " ".join(
                [
                    processing_tab,
                    action,
                    ref.owner_name,
                    dataset,
                    ref.entry.job_name,
                    display_ref_timestamp(ref),
                    ref.entry.command,
                ]
            ).casefold()
            if self.processing_filter_var.get() != "All" and processing_tab != self.processing_filter_var.get():
                continue
            if self.action_filter_var.get() != "All" and action != self.action_filter_var.get():
                continue
            if self.dataset_filter_var.get() != "All" and dataset != self.dataset_filter_var.get():
                continue
            if text and text not in haystack:
                continue
            filtered.append(ref)
        return sorted(filtered, key=self._sort_value, reverse=self.sort_descending)

    def _sort_value(self, ref: ScheduledJobRef):
        if self.sort_column == "processing_tab":
            return ref.entry.processing_tab or ""
        if self.sort_column == "action":
            return ref.entry.action or ""
        if self.sort_column == "dataset":
            return ref_dataset_name(ref)
        if self.sort_column == "job_name":
            return ref.entry.job_name
        if self.sort_column == "timestamp":
            return ref.entry.timestamp
        if self.sort_column == "mode":
            return ref.entry.execution_mode
        try:
            queue_order = int(ref.entry.artifacts.get("queue_order", 0))
        except Exception:
            queue_order = 10_000_000
        return (0, queue_order) if is_scheduled_history_entry(ref.entry) else (1, reverse_timestamp_sort_key(ref.entry.timestamp))

    def _sort_by(self, column: str) -> None:
        if self.sort_column == column:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_column = column
            self.sort_descending = False
        self._refresh_table()

    def _refresh_table(self) -> None:
        self._table_generation += 1
        generation = self._table_generation
        self.refs_by_id = {}
        rows: list[tuple[str, tuple[object, ...], tuple[str, ...]]] = []
        for ref in self._filtered_refs():
            self.refs_by_id[ref.entry_id] = ref
            rows.append(
                (
                    ref.entry_id,
                    (
                        ref.entry.processing_tab or "-",
                        ref.entry.action or "-",
                        ref_dataset_name(ref) or "-",
                        ref.entry.job_name or "-",
                        display_ref_timestamp(ref),
                        ref.entry.execution_mode or "local",
                    ),
                    (self._color_tag_for_ref(ref),),
                ),
            )
        chunked_treeview_replace(
            self.table,
            rows,
            widget=self.frame,
            generation=generation,
            is_current=lambda current: current == self._table_generation,
            chunk_size=180,
        )

    def _color_tag_for_ref(self, ref: ScheduledJobRef) -> str:
        color_by = self.color_by_var.get()
        if color_by == "Action":
            return self.app.history_entry_state_tag(ref.entry)
        if color_by == "Processing tab":
            value = ref.entry.processing_tab or "-"
        elif color_by == "Dataset":
            value = ref_dataset_name(ref) or "-"
        elif color_by == "Mode":
            value = ref.entry.execution_mode or "local"
        else:
            value = ref.entry.action or "-"
        digest = hashlib.blake2s(f"{color_by}:{value}".encode("utf-8"), digest_size=2).hexdigest()
        color = self._COLOR_PALETTE[int(digest, 16) % len(self._COLOR_PALETTE)]
        tag = f"global_job_color_{color_by.replace(' ', '_').lower()}_{digest}"
        self.table.tag_configure(tag, background=color)
        return tag

    def _selected_ref(self) -> ScheduledJobRef | None:
        selection = self.table.selection()
        if not selection:
            return None
        return self.refs_by_id.get(selection[0])

    def _selected_refs(self) -> list[ScheduledJobRef]:
        refs: list[ScheduledJobRef] = []
        for item_id in self.table.selection():
            ref = self.refs_by_id.get(item_id)
            if ref is not None:
                refs.append(ref)
        order = {ref.entry_id: index for index, ref in enumerate(self._filtered_refs())}
        return sorted(refs, key=lambda ref: order.get(ref.entry_id, 10_000_000))

    def _add_selected_jobs_as_workflow(self) -> None:
        refs = self._selected_refs()
        if not refs:
            messagebox.showinfo("Add workflow", "Please select one or more jobs in the Global Job List first.")
            return
        WorkflowEditorDialog(
            self.app,
            self.frame,
            initial_refs=refs,
            selected_refs_provider=self._selected_refs,
        ).show()
        self.refresh_queue()

    def _edit_and_run_workflows(self) -> None:
        WorkflowEditorDialog(
            self.app,
            self.frame,
            selected_refs_provider=self._selected_refs,
        ).show()
        self.refresh_queue()

    def _show_selected_details(self, _event=None) -> None:
        ref = self._selected_ref()
        if ref is None:
            messagebox.showinfo("Job details", "Please select a job first.")
            return
        if self._show_origin_history_details(ref):
            return
        sections = [
            (
                "Overview",
                [
                    ("Job", ref.entry.job_name),
                    ("Dataset", ref_dataset_name(ref) or "-"),
                    ("Group", ref.entry.group or "-"),
                    ("Action", ref.entry.action or "-"),
                    ("Timestamp", display_ref_timestamp(ref)),
                    ("Execution mode", ref.entry.execution_mode or "local"),
                    ("Slurm profile", ref.entry.slurm_profile or "-"),
                    ("Slurm job ID", ref.entry.slurm_job_id or "-"),
                ],
            ),
            (
                "Parameters",
                [(key, value) for key, value in ref.entry.parameters.items()] or [("Parameters", "-")],
            ),
        ]
        show_detail_dialog(self.frame, "Job details", sections, command=ref.entry.command or "-")

    def _show_origin_history_details(self, ref: ScheduledJobRef) -> bool:
        tab_by_processing = {
            "Processing: WARP": "processing",
            "Processing: M": "processing_m",
            "Processing: TS jobs": "tomograms",
            "Processing: Particle jobs": "particles",
        }
        tab_id = tab_by_processing.get(ref.entry.processing_tab)
        tab = self.app.tabs.get(tab_id) if tab_id else None
        show_details = getattr(tab, "show_history_entry_details", None)
        if not callable(show_details):
            return False
        show_details(ref.entry, parent=self.frame)
        return True

    def _move_selected(self, direction: int) -> None:
        selected = self._selected_ref()
        if selected is None:
            messagebox.showinfo("Move scheduled job", "Please select a scheduled job first.")
            return
        if not is_scheduled_history_entry(selected.entry):
            messagebox.showinfo("Move scheduled job", "Only scheduled jobs can be reordered.")
            return
        refs = iter_scheduled_job_refs(self.app.project)
        ids = [ref.entry_id for ref in refs]
        try:
            index = ids.index(selected.entry_id)
        except ValueError:
            return
        new_index = index + direction
        if not (0 <= new_index < len(refs)):
            return
        refs[index], refs[new_index] = refs[new_index], refs[index]
        assign_queue_order(refs)
        self.sort_column = "queue_order"
        self.sort_descending = False
        self.app.on_project_changed("job_queue")
        self._refresh_table()
        self.table.selection_set(selected.entry_id)

    def _remove_selected(self) -> None:
        ref = self._selected_ref()
        if ref is None:
            messagebox.showinfo("Remove job", "Please select a job first.")
            return
        remove_job_ref_from_project(self.app.project, ref)
        assign_queue_order(iter_scheduled_job_refs(self.app.project))
        self.app.on_project_changed("job_queue", "processing", "processing_m", "tomograms", "particles", "custom")
        self.refresh_queue()

    def _execute_queue(self, *, force_slurm: bool) -> None:
        refs = iter_scheduled_job_refs(self.app.project)
        if not refs:
            messagebox.showinfo("Run scheduled jobs", "No scheduled jobs found.")
            return
        profile = ""
        slurm_overrides: dict[str, str] = {}
        if force_slurm:
            slurm_mode = ask_scheduled_slurm_mode(self.frame)
            if slurm_mode is None:
                return
            initial_profile = self._initial_slurm_profile(refs)
            preview_builder = (
                (lambda profile_name, overrides: self._collective_scheduled_script(refs, profile_name, overrides))
                if slurm_mode == "collective"
                else (lambda profile_name, overrides: self._separate_scheduled_script_preview(refs, profile_name, overrides))
            )
            dialog = CollectiveSlurmSubmissionDialog(
                self.app,
                self.frame,
                initial_profile=initial_profile,
                initial_overrides={},
                script_builder=preview_builder,
                window_title="Submit scheduled jobs to Slurm",
                intro_text=(
                    "Which Slurm profile and header overrides should be used for this global submission?"
                    if slurm_mode == "collective"
                    else "Which Slurm profile and header overrides should be used for each scheduled submission?"
                ),
                submit_label="Submit jobs",
            )
            dialog_result = dialog.show()
            if dialog_result is None:
                return
            profile, slurm_overrides = dialog_result
            if slurm_mode == "collective":
                try:
                    combined_command = self._collective_command_text(refs)
                    command_text_by_id = {ref.entry_id: self._command_text_for_ref(ref) for ref in refs}
                    result = self.app.submit_slurm_command(
                        combined_command,
                        profile_name=profile,
                        cwd=None,
                        dataset_name="global_job_list",
                        job_name="scheduled_global_batch",
                        overrides=slurm_override_payload(slurm_overrides),
                    )
                except Exception as exc:
                    messagebox.showerror("Slurm submission failed", str(exc))
                    return
                submitted_at = history_timestamp_now()
                for ref in refs:
                    self._mark_entry_submitted(ref.entry, submitted_at, result, profile, command_text_by_id.get(ref.entry_id))
                self.app.status_var.set(f"Submitted {len(refs)} scheduled job(s) collectively")
                return
            if not profile and not self.app.is_debug_mode_enabled():
                messagebox.showerror("Slurm profile missing", "Please select a Slurm profile first.")
                return
        running_entry_ids = [ref.entry_id for ref in refs]

        def start_batch(on_queue_finished=None) -> None:
            self.app.mark_history_entries_running(running_entry_ids)
            self._execute_refs_sequentially(
                refs,
                force_slurm=force_slurm,
                forced_profile=profile,
                forced_overrides=slurm_overrides,
                on_queue_finished=on_queue_finished,
            )
            self.app.status_var.set(
                ("Submitting sequentially" if force_slurm else "Running") + f" {len(refs)} scheduled job(s)"
            )

        self.app.request_scheduled_batch_start(
            self.frame,
            queue_key="global-scheduled-slurm" if force_slurm else "global-scheduled-local",
            title="Queued global scheduled jobs",
            entry_ids=running_entry_ids,
            start_batch=start_batch,
        )

    def _execute_refs_sequentially(
        self,
        refs: list[ScheduledJobRef],
        *,
        force_slurm: bool,
        forced_profile: str,
        forced_overrides: dict[str, str],
        on_queue_finished,
    ) -> None:
        running_ids = [ref.entry_id for ref in refs]
        override_payload = slurm_override_payload(forced_overrides)
        has_local_commands = not force_slurm and any(ref.entry.execution_mode != "slurm" for ref in refs)
        output_window = (
            BatchCommandOutputWindow(self.app.root, title="Scheduled job output")
            if has_local_commands
            else None
        )

        def worker() -> None:
            failures: list[str] = []
            resolved_refs: list[tuple[ScheduledJobRef, list[tuple[str | None, str, str]]]] = []
            total_commands = 0
            output_job_ids: dict[tuple[str, int], str] = {}
            try:
                for ref in refs:
                    items = self._resolved_command_items_for_ref(ref)
                    resolved_refs.append((ref, items))
                    total_commands += len(items)
                if output_window is not None:
                    output_counter = 1
                    for ref, command_items in resolved_refs:
                        if ref.entry.execution_mode == "slurm":
                            continue
                        for command_index, (_cwd, dataset_name, command) in enumerate(command_items, start=1):
                            job_id = f"scheduled-{output_counter}"
                            output_job_ids[(ref.entry_id, command_index)] = job_id
                            label_bits = [
                                bit
                                for bit in (
                                    dataset_name or ref_dataset_name(ref),
                                    ref.entry.parameters.get("ts_name", ""),
                                    ref.entry.job_name,
                                )
                                if bit
                            ]
                            label = " / ".join(label_bits) if label_bits else ref.entry.job_name
                            if len(command_items) > 1:
                                label = f"{label} ({command_index}/{len(command_items)})"
                            output_window.queue_job(job_id, label, command)
                            output_counter += 1
            except Exception as exc:
                failures.append(str(exc))
            log_counter = 1
            for ref, command_items in resolved_refs:
                if failures:
                    break
                entry = ref.entry
                started_at = history_timestamp_now()
                command_text = self._command_text_for_items(command_items)
                try:
                    if force_slurm or entry.execution_mode == "slurm":
                        profile_name = forced_profile or entry.slurm_profile
                        if not profile_name and not self.app.is_debug_mode_enabled():
                            failures.append(f"{entry.job_name}: missing Slurm profile")
                            break
                        command, cwd, dataset_name = self._combined_command_items(command_items)
                        entry_overrides = slurm_override_payload(entry.parameters)
                        entry_overrides.update(override_payload)
                        result = self.app.submit_slurm_command(
                            command,
                            profile_name=profile_name,
                            cwd=cwd,
                            dataset_name=dataset_name,
                            job_name=entry.job_name,
                            overrides=entry_overrides,
                        )
                        self.app.root.after(
                            0,
                            lambda current_entry=entry, current_time=started_at, current_result=result, current_profile=profile_name, current_command=command_text: self._mark_entry_submitted(
                                current_entry,
                                current_time,
                                current_result,
                                current_profile,
                                current_command,
                            ),
                        )
                        if force_slurm and not self.app.is_debug_mode_enabled():
                            succeeded, state = wait_for_slurm_job(result.job_id)
                            if not succeeded:
                                failures.append(f"{entry.job_name}: Slurm job ended with state {state}")
                                break
                        continue

                    activation_command = self.app.resolve_environment_activation(entry.environment_title)
                    self.app.root.after(
                        0,
                        lambda current_entry=entry, current_time=started_at, current_command=command_text: self._mark_entry_started(
                            current_entry,
                            current_time,
                            current_command,
                        ),
                    )
                    for command_index, (cwd, _dataset_name, command) in enumerate(command_items, start=1):
                        batch_job_id = output_job_ids.get((ref.entry_id, command_index), "")
                        if output_window is not None and batch_job_id:
                            output_window.set_job_running(batch_job_id)
                        process = self.app.start_managed_process_for_output(
                            command,
                            cwd=cwd,
                            activation_command=activation_command,
                        )
                        if output_window is not None and batch_job_id:
                            output_window.attach_process(batch_job_id, process)
                        log_counter += 1
                        return_code = self.app.wait_managed_process(process)
                        if output_window is not None and batch_job_id:
                            output_window.set_job_finished(batch_job_id, return_code)
                        if self.app.abort_requested():
                            failures.append(f"{entry.job_name}: aborted")
                            break
                        if return_code != 0:
                            failures.append(f"{entry.job_name}: exit code {return_code}")
                            break
                except Exception as exc:
                    failures.append(f"{entry.job_name}: {exc}")
                    break
                if self.app.abort_requested():
                    failures.append(f"{entry.job_name}: aborted")
                    break

            if output_window is not None:
                output_window.finish_batch(failures)

            def finish() -> None:
                self.app.clear_history_entries_running(running_ids)
                self.app.clear_abort_request()
                assign_queue_order(iter_scheduled_job_refs(self.app.project))
                self.app.on_project_changed("job_queue", "processing", "processing_m", "tomograms", "particles", "custom")
                if failures:
                    self.app.status_var.set("Scheduled jobs stopped: " + "; ".join(failures))
                else:
                    self.app.status_var.set(f"Finished {len(refs)} scheduled job(s)")
                if on_queue_finished is not None:
                    on_queue_finished()

            self.app.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _initial_slurm_profile(self, refs: list[ScheduledJobRef]) -> str:
        for ref in refs:
            if ref.entry.slurm_profile:
                return ref.entry.slurm_profile
        profiles = self.app.slurm_profile_names()
        if profiles:
            return profiles[0]
        return "debug_profile" if self.app.is_debug_mode_enabled() else ""

    def _command_text_for_ref(self, ref: ScheduledJobRef) -> str:
        return self._command_text_for_items(self._resolved_command_items_for_ref(ref))

    def _resolved_command_items_for_ref(self, ref: ScheduledJobRef) -> list[tuple[str | None, str, str]]:
        if ref.entry.processing_tab == "Processing: TS jobs" and ref.owner_kind == "dataset":
            tomogram_tab = self.app.tabs.get("tomograms")
            resolver = getattr(tomogram_tab, "_resolved_scheduled_entry_commands", None)
            if callable(resolver):
                commands, errors = resolver(ref.owner, ref.entry)
                if errors and not self.app.is_debug_mode_enabled():
                    raise ValueError(f"{ref.entry.job_name}: " + "; ".join(errors))
                if commands:
                    items = [
                        (
                            self._tomogram_command_cwd(ref, resolved_dataset, spec),
                            resolved_dataset.dataset_name if resolved_dataset is not None else ref_dataset_name(ref),
                            command,
                        )
                        for resolved_dataset, spec, command in commands
                        if command.strip()
                    ]
                    if items:
                        return items
        command = ref.entry.command.strip()
        if not command or command == "Resolved at runtime":
            raise ValueError("No concrete command is stored for this scheduled job.")
        return [(ref_cwd(ref), ref_dataset_name(ref), command)]

    def _tomogram_command_cwd(self, ref: ScheduledJobRef, resolved_dataset, spec: dict[str, str]) -> str | None:
        cwd = (
            spec.get("destination")
            or spec.get("save_dir")
            or spec.get("tm_output_folder")
            or spec.get("out_folder")
            or spec.get("output_directory")
            or (resolved_dataset.processing_folder if resolved_dataset is not None else "")
        )
        return cwd or ref_cwd(ref)

    def _command_text_for_items(self, items: list[tuple[str | None, str, str]]) -> str:
        return "\n".join(command for _cwd, _dataset_name, command in items if command.strip()).strip()

    def _combined_command_items(self, items: list[tuple[str | None, str, str]]) -> tuple[str, str | None, str]:
        if not items:
            raise ValueError("No concrete command is stored for this scheduled job.")
        if len(items) == 1:
            cwd, dataset_name, command = items[0]
            return command, cwd, dataset_name
        lines: list[str] = []
        dataset_names = {dataset_name for _cwd, dataset_name, _command in items if dataset_name}
        for cwd, _dataset_name, command in items:
            if cwd:
                lines.append(f"cd {shlex.quote(cwd)}")
            lines.append(command)
            lines.append("")
        dataset_name = sorted(dataset_names)[0] if len(dataset_names) == 1 else "multiple_datasets"
        return "\n".join(lines).strip(), None, dataset_name

    def _collective_command_text(self, refs: list[ScheduledJobRef]) -> str:
        lines: list[str] = []
        for ref in refs:
            command_items = self._resolved_command_items_for_ref(ref)
            for cwd, _dataset_name, command in command_items:
                if cwd:
                    lines.append(f"cd {shlex.quote(cwd)}")
                lines.append(command)
                lines.append("")
        return "\n".join(lines).strip()

    def _collective_scheduled_script(
        self,
        refs: list[ScheduledJobRef],
        profile_name: str,
        overrides: dict[str, str],
    ) -> str:
        profile = find_slurm_profile(self.app.project, profile_name)
        if profile is None:
            raise ValueError("Please select a valid Slurm profile.")
        return render_sbatch_script(
            self._collective_command_text(refs),
            profile,
            None,
            "global_job_list",
            "scheduled_global_batch",
            slurm_override_payload(overrides),
        )

    def _separate_scheduled_script_preview(
        self,
        refs: list[ScheduledJobRef],
        profile_name: str,
        overrides: dict[str, str],
    ) -> str:
        profile = find_slurm_profile(self.app.project, profile_name)
        if profile is None:
            raise ValueError("Please select a valid Slurm profile.")
        scripts: list[str] = []
        for index, ref in enumerate(refs, start=1):
            command, cwd, dataset_name = self._combined_command_items(self._resolved_command_items_for_ref(ref))
            scripts.append(f"# Scheduled submission {index}/{len(refs)}: {ref.entry.job_name}")
            scripts.append(
                render_sbatch_script(
                    command,
                    profile,
                    cwd,
                    dataset_name,
                    ref.entry.job_name,
                    {**slurm_override_payload(ref.entry.parameters), **slurm_override_payload(overrides)},
                )
            )
        return "\n\n".join(scripts)

    def _mark_entry_started(self, entry: JobHistoryEntry, started_at: str, command_text: str | None = None) -> None:
        entry.timestamp = started_at
        entry.action = "ran"
        if command_text:
            entry.command = command_text
        if isinstance(entry.artifacts, dict):
            entry.artifacts.pop("queue_order", None)
        self.refresh_queue()
        self.app.on_project_changed("job_queue", "processing", "processing_m", "tomograms", "particles", "custom")

    def _mark_entry_submitted(
        self,
        entry: JobHistoryEntry,
        submitted_at: str,
        result: SlurmSubmissionResult,
        profile_name: str,
        command_text: str | None = None,
    ) -> None:
        entry.timestamp = submitted_at or history_timestamp_now()
        entry.action = "submitted"
        if command_text:
            entry.command = command_text
        entry.execution_mode = "slurm"
        entry.slurm_profile = profile_name
        entry.slurm_job_id = result.job_id
        entry.slurm_script_path = result.script_path
        if isinstance(entry.artifacts, dict):
            entry.artifacts.pop("queue_order", None)
        self.refresh_queue()
        self.app.on_project_changed("job_queue", "processing", "processing_m", "tomograms", "particles", "custom")
