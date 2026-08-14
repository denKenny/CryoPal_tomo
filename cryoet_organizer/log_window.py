from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import tkinter as tk
from tkinter import ttk

from cryoet_organizer.dialogs import technical_font_name


class ProcessLogWindow:
    """Floating window that streams stdout/stderr from a subprocess in real time."""

    def __init__(self, parent: tk.Misc, title: str = "Process output") -> None:
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("860x520")
        self.window.minsize(600, 320)
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)

        self._queue: queue.Queue[str | None] = queue.Queue()
        self._process: subprocess.Popen | None = None
        self._terminate_process_on_close = False

        self._build()
        self._poll()

    def _build(self) -> None:
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        text_frame = ttk.Frame(self.window, padding=(8, 8, 8, 0))
        text_frame.grid(row=0, column=0, sticky="nsew")
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.text = tk.Text(
            text_frame,
            wrap="word",
            state="disabled",
            font=technical_font_name(),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        self.text.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)

        bottom = ttk.Frame(self.window, padding=(8, 6, 8, 8))
        bottom.grid(row=1, column=0, sticky="ew")
        bottom.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Running…")
        ttk.Label(bottom, textvariable=self.status_var, anchor="w").grid(
            row=0, column=0, sticky="ew"
        )
        self.close_button = ttk.Button(
            bottom,
            text="Close",
            command=self.window.destroy,
            state="disabled",
        )
        self.close_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

    def attach_process(
        self,
        process: subprocess.Popen,
        *,
        terminate_process_on_close: bool = False,
    ) -> None:
        self._process = process
        self._terminate_process_on_close = terminate_process_on_close
        threading.Thread(target=self._read_output, daemon=True).start()

    def append_message(self, text: str) -> None:
        self._append_text(text)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def finish(self, status: str = "Finished.") -> None:
        self.status_var.set(status)
        if self.window.winfo_exists():
            try:
                self.close_button.configure(state="normal")
            except tk.TclError:
                pass

    def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._queue.put(None)
            return
        try:
            for line in iter(process.stdout.readline, ""):
                self._queue.put(line)
        except Exception:
            pass
        finally:
            self._queue.put(None)

    def _poll(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    self._on_finished()
                    return
                self._append_text(item)
        except queue.Empty:
            pass
        if self.window.winfo_exists():
            try:
                self.window.after(50, self._poll)
            except tk.TclError:
                pass

    def _append_text(self, text: str) -> None:
        if not self.window.winfo_exists():
            return
        self.text.configure(state="normal")
        self.text.insert("end", text)
        self.text.see("end")
        self.text.configure(state="disabled")

    def _handle_close(self) -> None:
        process = self._process
        if (
            self._terminate_process_on_close
            and process is not None
            and process.poll() is None
        ):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                try:
                    process.terminate()
                except Exception:
                    pass
        self.window.destroy()

    def _on_finished(self) -> None:
        rc = None
        if self._process is not None:
            rc = self._process.returncode
            if rc is None:
                rc = self._process.poll()
        if rc == 0:
            status = "Finished successfully."
        elif rc is None:
            status = "Finished."
        else:
            status = f"Finished with exit code {rc}."
        self.status_var.set(status)
        if self.window.winfo_exists():
            try:
                self.close_button.configure(state="normal")
            except tk.TclError:
                pass


class BatchCommandOutputWindow:
    """Single floating window that tracks output for a sequence of commands."""

    def __init__(self, parent: tk.Misc, title: str = "Command output") -> None:
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("1120x680")
        self.window.minsize(820, 460)
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)

        self._queue: queue.Queue[tuple[str, str, object]] = queue.Queue()
        self._jobs: dict[str, dict[str, object]] = {}
        self._selected_job_id = ""
        self._running = 0
        self._completed = 0
        self._failed = 0
        self._aborted = 0
        self._finished = False

        self._build()
        self._poll()

    def _build(self) -> None:
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(1, weight=1)

        header = ttk.Frame(self.window, padding=(10, 8, 10, 4))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        self.summary_var = tk.StringVar(value="Preparing commands...")
        ttk.Label(header, textvariable=self.summary_var, anchor="w").grid(row=0, column=0, sticky="ew")

        paned = ttk.Panedwindow(self.window, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew", padx=10, pady=(4, 6))

        list_frame = ttk.Frame(paned)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.job_table = ttk.Treeview(
            list_frame,
            columns=("status", "label"),
            show="headings",
            height=16,
            style="Technical.Treeview",
        )
        self.job_table.heading("status", text="Status")
        self.job_table.heading("label", text="Command")
        self.job_table.column("status", width=105, anchor="w", stretch=False)
        self.job_table.column("label", width=290, anchor="w")
        self.job_table.grid(row=0, column=0, sticky="nsew")
        job_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.job_table.yview)
        job_scroll.grid(row=0, column=1, sticky="ns")
        self.job_table.configure(yscrollcommand=job_scroll.set)
        self.job_table.bind("<<TreeviewSelect>>", self._on_job_selected)
        self.job_table.tag_configure("pending", background="#f1f1f1")
        self.job_table.tag_configure("running", background="#dff4d8")
        self.job_table.tag_configure("completed", background="#dde8ff")
        self.job_table.tag_configure("failed", background="#ffe0df")
        self.job_table.tag_configure("aborted", background="#ffe8cc")
        paned.add(list_frame, weight=1)

        log_frame = ttk.Frame(paned)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(log_frame)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        selected_frame = ttk.Frame(self.notebook)
        selected_frame.columnconfigure(0, weight=1)
        selected_frame.rowconfigure(0, weight=1)
        all_frame = ttk.Frame(self.notebook)
        all_frame.columnconfigure(0, weight=1)
        all_frame.rowconfigure(0, weight=1)
        self.selected_text = self._create_log_text(selected_frame)
        self.all_text = self._create_log_text(all_frame)
        self.notebook.add(selected_frame, text="Selected output")
        self.notebook.add(all_frame, text="All output")
        paned.add(log_frame, weight=3)

        footer = ttk.Frame(self.window, padding=(10, 4, 10, 10))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Running...")
        ttk.Label(footer, textvariable=self.status_var, anchor="w").grid(row=0, column=0, sticky="ew")
        self.close_button = ttk.Button(
            footer,
            text="Close",
            command=self.window.destroy,
            state="disabled",
        )
        self.close_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

    def _create_log_text(self, parent: ttk.Frame) -> tk.Text:
        text = tk.Text(
            parent,
            wrap="word",
            state="disabled",
            font=technical_font_name(),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        text.configure(yscrollcommand=scrollbar.set)
        return text

    def add_job(self, job_id: str, label: str, command: str) -> None:
        self._add_job(job_id, label, command)

    def queue_job(self, job_id: str, label: str, command: str) -> None:
        self._queue.put(("add_job", job_id, (label, command)))

    def _add_job(self, job_id: str, label: str, command: str) -> None:
        if job_id in self._jobs:
            return
        self._jobs[job_id] = {
            "label": label,
            "command": command,
            "status": "pending",
            "exit_code": "",
            "output": [],
        }
        self.job_table.insert("", "end", iid=job_id, values=("Pending", label), tags=("pending",))
        if not self._selected_job_id:
            self._selected_job_id = job_id
            self.job_table.selection_set(job_id)
            self._render_selected_job()
        self._update_summary()

    def attach_process(self, job_id: str, process: subprocess.Popen) -> None:
        threading.Thread(target=self._read_output, args=(job_id, process), daemon=True).start()

    def set_job_running(self, job_id: str) -> None:
        self._queue.put(("status", job_id, "running"))

    def set_job_finished(self, job_id: str, return_code: int) -> None:
        if return_code == 0:
            status = "completed"
        elif return_code < 0:
            status = "aborted"
        else:
            status = "failed"
        self._queue.put(("status", job_id, (status, return_code)))

    def finish_batch(self, failures: list[str] | None = None) -> None:
        self._queue.put(("batch_finished", "", list(failures or [])))

    def _read_output(self, job_id: str, process: subprocess.Popen) -> None:
        if process is None or process.stdout is None:
            self._queue.put(("output", job_id, "(No command output stream was available.)\n"))
            return
        try:
            for line in iter(process.stdout.readline, ""):
                self._queue.put(("output", job_id, line))
        except Exception as exc:
            self._queue.put(("output", job_id, f"\nCould not read command output: {exc}\n"))

    def _poll(self) -> None:
        try:
            while True:
                event, job_id, payload = self._queue.get_nowait()
                if event == "add_job":
                    label, command = payload if isinstance(payload, tuple) else ("Command", "")
                    self._add_job(job_id, str(label), str(command))
                elif event == "output":
                    self._handle_output(job_id, str(payload))
                elif event == "status":
                    self._handle_status(job_id, payload)
                elif event == "batch_finished":
                    self._handle_batch_finished(payload if isinstance(payload, list) else [])
        except queue.Empty:
            pass
        if self.window.winfo_exists():
            try:
                self.window.after(50, self._poll)
            except tk.TclError:
                pass

    def _handle_output(self, job_id: str, text: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        output = job.get("output")
        if isinstance(output, list):
            output.append(text)
        label = str(job.get("label", job_id))
        self._append_text(self.all_text, f"[{label}] {text}" if text.endswith("\n") else f"[{label}] {text}\n")
        if job_id == self._selected_job_id:
            self._append_text(self.selected_text, text)

    def _handle_status(self, job_id: str, payload: object) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        old_status = str(job.get("status", "pending"))
        new_status = str(payload)
        exit_code = ""
        if isinstance(payload, tuple):
            new_status = str(payload[0])
            exit_code = str(payload[1])
        if old_status != new_status:
            self._decrement_status(old_status)
            self._increment_status(new_status)
        job["status"] = new_status
        job["exit_code"] = exit_code
        display = self._status_label(new_status, exit_code)
        try:
            self.job_table.item(job_id, values=(display, job.get("label", job_id)), tags=(new_status,))
        except tk.TclError:
            pass
        if job_id == self._selected_job_id:
            self._render_selected_job()
        self._update_summary()

    def _handle_batch_finished(self, failures: list[str]) -> None:
        self._finished = True
        if failures:
            self.status_var.set("Batch stopped: " + "; ".join(str(item) for item in failures))
        else:
            self.status_var.set("Batch finished.")
        try:
            self.close_button.configure(state="normal")
        except tk.TclError:
            pass
        self._update_summary()

    def _decrement_status(self, status: str) -> None:
        if status == "running":
            self._running = max(0, self._running - 1)
        elif status == "completed":
            self._completed = max(0, self._completed - 1)
        elif status == "failed":
            self._failed = max(0, self._failed - 1)
        elif status == "aborted":
            self._aborted = max(0, self._aborted - 1)

    def _increment_status(self, status: str) -> None:
        if status == "running":
            self._running += 1
        elif status == "completed":
            self._completed += 1
        elif status == "failed":
            self._failed += 1
        elif status == "aborted":
            self._aborted += 1

    def _status_label(self, status: str, exit_code: str = "") -> str:
        if status == "pending":
            return "Pending"
        if status == "running":
            return "Running"
        if status == "completed":
            return "Done"
        if status == "aborted":
            return "Aborted"
        if status == "failed":
            return f"Failed ({exit_code})" if exit_code else "Failed"
        return status.title()

    def _update_summary(self) -> None:
        total = len(self._jobs)
        pending = sum(1 for job in self._jobs.values() if job.get("status") == "pending")
        self.summary_var.set(
            f"Total {total} | Pending {pending} | Running {self._running} | "
            f"Completed {self._completed} | Failed {self._failed} | Aborted {self._aborted}"
        )

    def _on_job_selected(self, _event=None) -> None:
        selection = self.job_table.selection()
        if not selection:
            return
        self._selected_job_id = str(selection[0])
        self._render_selected_job()

    def _render_selected_job(self) -> None:
        job = self._jobs.get(self._selected_job_id)
        self._set_text(self.selected_text, "")
        if job is None:
            return
        header = [
            f"Command: {job.get('label', self._selected_job_id)}",
            f"Status: {self._status_label(str(job.get('status', 'pending')), str(job.get('exit_code', '')))}",
            "",
            str(job.get("command", "")).strip(),
            "",
            "Output:",
            "",
        ]
        output = job.get("output")
        text = "\n".join(header)
        if isinstance(output, list):
            text += "".join(str(item) for item in output)
        self._set_text(self.selected_text, text)

    def _append_text(self, widget: tk.Text, text: str) -> None:
        if not self.window.winfo_exists():
            return
        widget.configure(state="normal")
        widget.insert("end", text)
        widget.see("end")
        widget.configure(state="disabled")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        if not self.window.winfo_exists():
            return
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.see("end")
        widget.configure(state="disabled")

    def _handle_close(self) -> None:
        if self._finished:
            self.window.destroy()
            return
        self.status_var.set("Commands are still running. Use CryoPal's Abort button to stop them.")


class ShortcutLaunchWindow:
    """Small progress window for launching GUI-oriented shortcuts."""

    def __init__(self, parent: tk.Misc, title: str = "Launching shortcut") -> None:
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("460x170")
        self.window.minsize(420, 150)
        self.window.transient(parent.winfo_toplevel())
        self.window.protocol("WM_DELETE_WINDOW", self._handle_close)
        self.window.columnconfigure(0, weight=1)
        self.window.rowconfigure(0, weight=1)

        self._queue: queue.Queue[str | None] = queue.Queue()
        self._process: subprocess.Popen | None = None
        self._output_lines: list[str] = []
        self._finished = False

        body = ttk.Frame(self.window, padding=16)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self.message_var = tk.StringVar(
            value="CryoPal is preparing and launching the selected shortcut."
        )
        ttk.Label(
            body,
            textvariable=self.message_var,
            wraplength=400,
            justify="left",
        ).grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(body, orient="horizontal", mode="indeterminate", length=320)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.progress.start(10)

        self.status_var = tk.StringVar(value="Launching…")
        ttk.Label(body, textvariable=self.status_var).grid(row=2, column=0, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, sticky="e", pady=(12, 0))
        self.details_button = ttk.Button(
            buttons,
            text="Show details",
            command=self._show_details,
            state="disabled",
        )
        self.details_button.grid(row=0, column=0, padx=(0, 8))
        self.close_button = ttk.Button(
            buttons,
            text="Close",
            command=self._handle_close,
        )
        self.close_button.grid(row=0, column=1)

        self.window.after(50, self._poll)

    def attach_process(self, process: subprocess.Popen) -> None:
        self._process = process
        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            self._queue.put(None)
            return
        try:
            for line in iter(process.stdout.readline, ""):
                self._queue.put(line)
        except Exception:
            pass
        finally:
            self._queue.put(None)

    def _poll(self) -> None:
        try:
            while True:
                item = self._queue.get_nowait()
                if item is None:
                    self._on_finished()
                    return
                self._output_lines.append(item)
        except queue.Empty:
            pass
        if self.window.winfo_exists():
            try:
                self.window.after(50, self._poll)
            except tk.TclError:
                pass

    def _on_finished(self) -> None:
        self._finished = True
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        process = self._process
        rc = process.returncode if process is not None else 0
        if rc is None and process is not None:
            rc = process.poll()
        if rc == 0:
            self.status_var.set("Shortcut launched.")
            if self.window.winfo_exists():
                self.window.after(700, self._destroy_if_exists)
        else:
            self.status_var.set(f"Shortcut failed with exit code {rc}.")
            self.message_var.set(
                "The shortcut could not be launched successfully. "
                "Use 'Show details' to inspect the shell output."
            )
            try:
                self.details_button.configure(state="normal")
            except tk.TclError:
                pass

    def _show_details(self) -> None:
        details = ProcessLogWindow(self.window, title="Shortcut details")
        details.append_message("".join(self._output_lines) or "No output was captured.\n")
        process = self._process
        rc = process.returncode if process is not None else None
        if rc is None and process is not None:
            rc = process.poll()
        if rc is None:
            details.finish("Shortcut output")
        elif rc == 0:
            details.finish("Finished successfully.")
        else:
            details.finish(f"Finished with exit code {rc}.")

    def _terminate_process(self) -> None:
        process = self._process
        if process is None or process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass

    def _destroy_if_exists(self) -> None:
        if self.window.winfo_exists():
            self.window.destroy()

    def _handle_close(self) -> None:
        if not self._finished:
            self._terminate_process()
        self._destroy_if_exists()
