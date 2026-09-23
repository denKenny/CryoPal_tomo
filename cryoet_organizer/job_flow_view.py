"""Read-only, asynchronously laid out and viewport-rendered history graph."""
from __future__ import annotations

import bisect
import hashlib
import queue
import threading
import time
import tkinter as tk
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk

from cryoet_organizer.job_flow import (
    FlowCancelled, build_flow, check_cancel, filter_flow, flow_signature, snapshot_records,
)
from cryoet_organizer.job_flow_layout import layout_flow


class JobFlowView(ttk.Frame):
    PALETTE = ("#ececec", "#dbeeff", "#dff4d8", "#fff0c9", "#eadff8",
               "#f8dddd", "#dff2f4", "#f0eadf", "#e3edf7", "#edf1d6")
    STATES = {"scheduled": "#ececec", "waiting": "#dbeeff", "running": "#dff4d8",
              "succeeded": "#dde8ff", "completed": "#dde8ff", "failed": "#f8d7da",
              "cancelled": "#fff3cd", "aborted": "#fff3cd"}

    def __init__(self, parent, *, details, color_by):
        super().__init__(parent)
        self.details = details
        self.color_by = color_by
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cryopal-flow")
        self.results = queue.Queue()
        self.generation = 0
        self.cancel = threading.Event()
        self.future = None
        self.graph = self.layout = None
        self._last_request = None
        self.layout_mode = tk.StringVar(value="Wide")
        self.selected_node = None
        self.neighbors = {}
        self.active_nodes = set()
        self._style_generation = 0
        self._node_styles = {}
        self._edge_styles = {}
        self.cache = OrderedDict()
        self.graph_cache = None
        self.scale = 1.0
        self.visible_nodes = {}
        self.visible_edges = {}
        self.node_index = []
        self.edge_index = []
        self.edge_prefix = []
        self.dead = False
        self._paint_id = self._snapshot_id = None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        self.status = tk.StringVar(value="Select Flow view to build the processing graph.")
        ttk.Label(toolbar, textvariable=self.status, wraplength=420).pack(side="left", fill="x", expand=True)
        for text, command in (("-", lambda: self.zoom(0.8)), ("+", lambda: self.zoom(1.25)), ("Fit to view", self.fit)):
            ttk.Button(toolbar, text=text, command=command, width=11 if text == "Fit to view" else 3).pack(side="right", padx=3)
        self.progress = ttk.Progressbar(toolbar, mode="indeterminate", length=100)
        layout_controls = ttk.Frame(toolbar)
        layout_controls.pack(side="right", padx=8)
        ttk.Label(layout_controls, text="Layout").pack(side="left", padx=(0, 6))
        layout_combo = ttk.Combobox(layout_controls, textvariable=self.layout_mode, values=("Wide", "Compact"),
                                    state="readonly", width=9)
        layout_combo.pack(side="left")
        layout_combo.bind("<<ComboboxSelected>>", self._layout_changed)
        self.canvas = tk.Canvas(self, background="#fafafa", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.yscroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.yscroll.grid(row=1, column=1, sticky="ns")
        self.xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.xscroll.grid(row=2, column=0, sticky="ew")
        self.canvas.configure(yscrollcommand=self._yscroll, xscrollcommand=self._xscroll)
        ttk.Label(self, text="Solid: recorded/artifact dependency   Dashed: inferred/time order   Dotted: membership\n"
                            "Branches are not a time axis. Missing dependencies do not establish independence.",
                  wraplength=950).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.canvas.bind("<Configure>", lambda _event: self._schedule_paint())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(3, "units"))
        self.canvas.bind("<Double-1>", self._open_details)
        self.canvas.bind("<Button-1>", self._select_node)
        self.canvas.bind("<Escape>", lambda _event: self._set_selection(None))
        self.bind("<Destroy>", self._destroy, add="+")
        self._poll_id = self.after(60, self._poll)

    def _yscroll(self, first, last):
        self.yscroll.set(first, last)
        self._schedule_paint()

    def _xscroll(self, first, last):
        self.xscroll.set(first, last)
        self._schedule_paint()

    def _wheel(self, event):
        amount = -1 if event.delta > 0 else 1
        if event.state & 1:
            self.canvas.xview_scroll(amount * 3, "units")
        else:
            self.canvas.yview_scroll(amount * 3, "units")
        return "break"

    def suspend(self):
        self.generation += 1
        self.cancel.set()
        if self.future:
            self.future.cancel()
        if self._snapshot_id:
            self.after_cancel(self._snapshot_id)
            self._snapshot_id = None
        if self._paint_id:
            self.after_cancel(self._paint_id)
            self._paint_id = None
        if not self.dead:
            self.progress.stop()
            self.progress.pack_forget()

    def request(self, project, dataset="All", tomogram="All", *, force=False, on_ready=None):
        self.suspend()
        self._last_request = (project, dataset, tomogram, on_ready)
        mode = self.layout_mode.get()
        generation = self.generation
        cancel = self.cancel = threading.Event()
        self.status.set("Building processing flow...")
        self.progress.pack(side="right", padx=8)
        self.progress.start(12)
        name, datasets = project.name, tuple(item.dataset_name for item in project.datasets)
        records, iterator = [], iter(snapshot_records(project))

        def snapshot_chunk():
            self._snapshot_id = None
            if cancel.is_set() or self.dead:
                return
            deadline = time.monotonic() + 0.008
            try:
                for _ in range(100):
                    records.append(next(iterator))
                    if time.monotonic() >= deadline:
                        break
            except StopIteration:
                self.future = self.executor.submit(work)
                return
            except Exception as exc:
                self.results.put((generation, None, str(exc), on_ready))
                return
            self._snapshot_id = self.after(1, snapshot_chunk)

        def work():
            try:
                check_cancel(cancel)
                signature = flow_signature(records, name, datasets)
                if force:
                    self.cache.clear()
                    self.graph_cache = None
                if self.graph_cache and self.graph_cache[0] == signature:
                    graph = self.graph_cache[1]
                else:
                    graph = build_flow(records, name, datasets, cancel=cancel)
                    self.graph_cache = (signature, graph)
                check_cancel(cancel)
                key = (signature, dataset, tomogram, mode)
                cached = self.cache.get(key)
                if cached is None:
                    filtered = filter_flow(graph, dataset, tomogram)
                    layout = layout_flow(filtered, cancel, mode=mode)
                    cached = (filtered, layout)
                    self.cache[key] = cached
                    while len(self.cache) > 4:
                        self.cache.popitem(last=False)
                else:
                    self.cache.move_to_end(key)
                check_cancel(cancel)
                self.results.put((generation, (graph, *cached), "", on_ready))
            except FlowCancelled:
                pass
            except Exception as exc:
                self.results.put((generation, None, str(exc), on_ready))

        snapshot_chunk()

    def _layout_changed(self, _event=None):
        if self._last_request is not None:
            project, dataset, tomogram, callback = self._last_request
            self.request(project, dataset, tomogram, on_ready=callback)

    def _poll(self):
        if self.dead:
            return
        try:
            while True:
                generation, result, error, callback = self.results.get_nowait()
                if generation != self.generation:
                    continue
                self.progress.stop()
                self.progress.pack_forget()
                if error:
                    self.status.set("Could not build flow: " + error)
                    continue
                full_graph, self.graph, self.layout = result
                self._reset_items()
                self.neighbors = {}
                for a, b in self.graph.edges:
                    self.neighbors.setdefault(a, set()).add(b)
                    self.neighbors.setdefault(b, set()).add(a)
                self._set_selection(self.selected_node if self.selected_node in self.graph.nodes else None)
                self.node_index = sorted((box[1], box[3], key) for key, box in self.layout.boxes.items())
                self.edge_index = sorted((min(points[1::2]), max(points[1::2]), pair)
                                         for pair, points in self.layout.lines.items())
                highest = -float("inf")
                self.edge_prefix = []
                for _, bottom, _ in self.edge_index:
                    highest = max(highest, bottom)
                    self.edge_prefix.append(highest)
                count = sum(node.kind == "job" for node in self.graph.nodes.values())
                warning = " | " + " ".join(self.graph.warnings) if self.graph.warnings else ""
                self.status.set(f"{count} processing steps | {self.layout.engine}" + warning)
                self._set_scrollregion()
                self._schedule_paint()
                if callback:
                    callback(full_graph)
        except queue.Empty:
            pass
        self._poll_id = self.after(60, self._poll)

    def _reset_items(self):
        self.canvas.delete("all")
        self.visible_nodes.clear()
        self.visible_edges.clear()
        self._node_styles.clear()
        self._edge_styles.clear()

    def _set_scrollregion(self):
        if self.layout:
            self.canvas.configure(scrollregion=(0, 0, self.layout.width * self.scale, self.layout.height * self.scale))

    def zoom(self, factor):
        if not self.layout:
            return
        x, y = self.canvas.xview()[0], self.canvas.yview()[0]
        self.scale = max(0.08, min(3.0, self.scale * factor))
        self._reset_items()
        self._set_scrollregion()
        self.canvas.xview_moveto(x)
        self.canvas.yview_moveto(y)
        self._schedule_paint()

    def fit(self):
        if self.layout:
            factor = min(max(100, self.canvas.winfo_width()) / self.layout.width,
                         max(100, self.canvas.winfo_height()) / self.layout.height)
            self.scale = max(0.01, min(1.0, factor))
            self._reset_items()
            self._set_scrollregion()
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
            self._schedule_paint()

    def recolor(self):
        self._style_generation += 1
        self._schedule_paint()

    def _set_selection(self, key):
        self.selected_node = key
        self.active_nodes = {key, *self.neighbors.get(key, ())} if key else set()
        self.recolor()

    def _node_at(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for item in reversed(self.canvas.find_overlapping(x, y, x, y)):
            for tag in self.canvas.gettags(item):
                if tag.startswith("node:") and self.graph and tag[5:] in self.graph.nodes:
                    return tag[5:]
        return None

    def _select_node(self, event):
        self.canvas.focus_set()
        self._set_selection(self._node_at(event))

    def _style_node(self, key):
        rectangle, text = self.visible_nodes[key]
        muted = self.selected_node is not None and key not in self.active_nodes
        fill = self._color(self.graph.nodes[key])
        if muted:
            fill = "#" + "".join(f"{round(int(fill[i:i + 2], 16) * .25 + 250 * .75):02x}" for i in (1, 3, 5))
        self.canvas.itemconfigure(rectangle, fill=fill, outline="#abb2b6" if muted else "#263d4a" if key == self.selected_node else "#69747d",
                                  width=3 if key == self.selected_node else 1)
        self.canvas.itemconfigure(text, fill="#8a9196" if muted else "#15191c")
        self._node_styles[key] = self._style_generation

    def _style_edge(self, pair):
        active = self.selected_node is not None and self.selected_node in pair
        muted = self.selected_node is not None and not active
        self.canvas.itemconfigure(self.visible_edges[pair], fill="#d5dadd" if muted else "#263d4a" if active else "#69747d",
                                  width=max(1, (2.5 if active else 1.3) * self.scale))
        self._edge_styles[pair] = self._style_generation

    def _color(self, node):
        if node.kind != "job":
            return "#e2e6e8"
        mode = self.color_by()
        value = node.colors.get(mode, "unknown")
        if mode == "Action" and value in self.STATES:
            return self.STATES[value]
        digest = hashlib.blake2s(f"{mode}:{value}".encode(), digest_size=2).hexdigest()
        return self.PALETTE[int(digest, 16) % len(self.PALETTE)]

    def _schedule_paint(self):
        if not self.dead and self._paint_id is None:
            self._paint_id = self.after(15, self._paint)

    def _paint(self):
        self._paint_id = None
        if self.layout is None or self.graph is None or self.dead:
            return
        scale = self.scale
        left = self.canvas.canvasx(0) / scale - 40
        top = self.canvas.canvasy(0) / scale - 40
        right = left + self.canvas.winfo_width() / scale + 80
        bottom = top + self.canvas.winfo_height() / scale + 80
        start = bisect.bisect_left(self.node_index, (top - 100,))
        stop = bisect.bisect_right(self.node_index, (bottom, float("inf"), "\uffff"))
        wanted = {key for _, _, key in self.node_index[start:stop]
                  if self.layout.boxes[key][2] >= left and self.layout.boxes[key][0] <= right}
        for key in set(self.visible_nodes) - wanted:
            for item in self.visible_nodes.pop(key):
                self.canvas.delete(item)
            self._node_styles.pop(key, None)
        deadline = time.monotonic() + .008
        for key in wanted - self.visible_nodes.keys():
            node, box = self.graph.nodes[key], self.layout.boxes[key]
            coords = tuple(value * scale for value in box)
            tag = "node:" + key
            rectangle = self.canvas.create_rectangle(*coords, fill=self._color(node), outline="#69747d", tags=(tag,))
            label = "\n".join(line if len(line) <= 42 else line[:39] + "..." for line in node.label.splitlines())
            text = self.canvas.create_text((coords[0] + coords[2]) / 2, (coords[1] + coords[3]) / 2,
                                          text=label if scale >= .3 else "", width=max(1, coords[2] - coords[0] - 10),
                                          font=("TkDefaultFont", max(6, round(10 * scale))), tags=(tag,))
            self.visible_nodes[key] = rectangle, text
            self._style_node(key)
            if time.monotonic() > deadline:
                self._schedule_paint()
                return
        for key in wanted:
            if self._node_styles.get(key) != self._style_generation:
                self._style_node(key)
                if time.monotonic() > deadline:
                    self._schedule_paint()
                    return
        first = bisect.bisect_left(self.edge_prefix, top)
        last = bisect.bisect_right(self.edge_index, (bottom, float("inf"), ("\uffff", "\uffff")))
        edges = {pair for _, edge_bottom, pair in self.edge_index[first:last] if edge_bottom >= top
                 and max(self.layout.lines[pair][0::2]) >= left and min(self.layout.lines[pair][0::2]) <= right}
        for pair in set(self.visible_edges) - edges:
            self.canvas.delete(self.visible_edges.pop(pair))
            self._edge_styles.pop(pair, None)
        for pair in edges - self.visible_edges.keys():
            kind = self.graph.edges[pair]
            dash = (2, 4) if kind == "membership" else (6, 4) if kind in {"inferred", "sequence"} else ()
            item = self.canvas.create_line(*(value * scale for value in self.layout.lines[pair]),
                                           arrow="last", fill="#69747d", dash=dash,
                                           width=max(1, 1.3 * scale), smooth=self.layout.engine == "Graphviz")
            self.canvas.tag_lower(item)
            self.visible_edges[pair] = item
            self._style_edge(pair)
            if time.monotonic() > deadline:
                self._schedule_paint()
                return
        for pair in edges:
            if self._edge_styles.get(pair) != self._style_generation:
                self._style_edge(pair)
                if time.monotonic() > deadline:
                    self._schedule_paint()
                    return

    def _open_details(self, event):
        key = self._node_at(event)
        if key is not None and self.graph.nodes[key].entries:
            self.details(self.graph.nodes[key].entries)

    def _destroy(self, event):
        if event.widget != self:
            return
        self.dead = True
        self.suspend()
        for after_id in (self._poll_id, self._paint_id):
            if after_id:
                self.after_cancel(after_id)
        self.executor.shutdown(wait=False, cancel_futures=True)
