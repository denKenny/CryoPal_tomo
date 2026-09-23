"""Bounded Graphviz layout with a dependency-free dataset-lane fallback."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass

from cryoet_organizer.job_flow import check_cancel, topological_order


@dataclass
class FlowLayout:
    boxes: dict[str, tuple[float, float, float, float]]
    lines: dict[tuple[str, str], tuple[float, ...]]
    width: float
    height: float
    engine: str


def layout_rows(graph, mode="Wide", cancel=None):
    """Bound row width without turning chronological hints into dependencies."""
    order = topological_order(graph)
    parents = defaultdict(list)
    for (a, b), kind in graph.edges.items():
        if kind != "sequence":
            parents[b].append(a)
    capacity = 2 if mode == "Compact" else 4
    rows, levels, skips = defaultdict(list), {}, defaultdict(dict)

    def available(lane, level):
        visited = []
        while level in skips[lane]:
            visited.append(level)
            level = skips[lane][level]
        for old in visited:
            skips[lane][old] = level
        return level

    for key in order:
        check_cancel(cancel)
        node = graph.nodes[key]
        level = max((levels[parent] + 1 for parent in parents[key]), default=0)
        if node.kind == "job":
            level = available(node.lane, max(2, level))
            rows[node.lane, level].append(key)
            if len(rows[node.lane, level]) == capacity:
                skips[node.lane][level] = available(node.lane, level + 1)
        levels[key] = level
    return levels, rows, parents


def fallback_layout(graph, cancel=None, *, mode="Wide"):
    levels, rows, parents = layout_rows(graph, mode, cancel)
    lanes = sorted({node.lane for node in graph.nodes.values() if node.lane})
    widths = dict.fromkeys(lanes, 280)
    for (lane, _level), members in rows.items():
        widths[lane] = max(widths[lane], len(members) * 330 - 50)
    offsets, x = {}, 30
    for lane in lanes:
        offsets[lane] = x
        x += widths[lane] + 80
    right = x - 80 if lanes else 310
    boxes = {}
    for key, node in graph.nodes.items():
        if node.kind != "job":
            center = (30 + right) / 2 if node.kind == "project" else offsets[node.lane] + widths[node.lane] / 2
            y = 30 + levels[key] * 135
            boxes[key] = (center - 140, y, center + 140, y + 86)
    for (lane, level), members in sorted(rows.items(), key=lambda item: (item[0][1], item[0][0])):
        check_cancel(cancel)
        left, width = offsets[lane], widths[lane]
        desired = {}
        for key in members:
            centers = [(boxes[p][0] + boxes[p][2]) / 2 for p in parents[key] if p in boxes]
            desired[key] = sum(centers) / len(centers) if centers else left + width / 2
        members = sorted(members, key=lambda key: (desired[key], graph.nodes[key].order, key))
        # Spread siblings around their shared parent, then prevent overlaps while
        # keeping merges close to the centre of their inputs.
        ties = defaultdict(list)
        for key in members:
            ties[desired[key]].append(key)
        for center, siblings in ties.items():
            for index, key in enumerate(siblings):
                desired[key] = center + (index - (len(siblings) - 1) / 2) * 330
        centers = []
        for key in members:
            centers.append(max(left + 140, desired[key], centers[-1] + 330 if centers else left + 140))
        centers[-1] = min(centers[-1], left + width - 140)
        for index in range(len(centers) - 2, -1, -1):
            centers[index] = min(centers[index], centers[index + 1] - 330)
        for key, center in zip(members, centers):
            y = 30 + level * 135
            boxes[key] = (center - 140, y, center + 140, y + 86)
    lines = {}
    for a, b in graph.edges:
        check_cancel(cancel)
        source, target = boxes[a], boxes[b]
        sx, sy = (source[0] + source[2]) / 2, source[3]
        tx, ty = (target[0] + target[2]) / 2, target[1]
        if levels[a] == levels[b]:
            gap = max(source[3], target[3]) + 16
            lines[a, b] = (sx, sy, sx, gap, tx, gap, tx, target[3])
        elif levels[b] == levels[a] + 1:
            middle = (sy + ty) / 2
            lines[a, b] = (sx, sy, sx, middle, tx, middle, tx, ty)
        else:
            gutter = min(offsets.get(graph.nodes[a].lane, 30), offsets.get(graph.nodes[b].lane, 30)) - 18
            lines[a, b] = (sx, sy, sx, sy + 16, gutter, sy + 16, gutter, ty - 16, tx, ty - 16, tx, ty)
    return FlowLayout(boxes, lines, max((box[2] for box in boxes.values()), default=300) + 30,
                      max((box[3] for box in boxes.values()), default=200) + 30, "Dataset lanes")


def graphviz_source(graph, mode="Wide", cancel=None):
    levels, _rows, _parents = layout_rows(graph, mode, cancel)
    keys = {key: f"n{index}" for index, key in enumerate(sorted(graph.nodes, key=lambda key: (graph.nodes[key].order, key)))}
    lines = ['digraph flow { graph [rankdir=TB, nodesep=0.4, ranksep=0.6, newrank=true];',
             'node [shape=box, fixedsize=true, width=3.9, height=1.2, label=""];']
    lanes = defaultdict(list)
    for key, node in graph.nodes.items():
        lanes[node.lane].append(key)
    for index, (lane, members) in enumerate(sorted(lanes.items())):
        if lane:
            lines.append(f"subgraph cluster_{index} {{ color=transparent;")
        lines.extend(f"{keys[key]};" for key in sorted(members, key=lambda key: (graph.nodes[key].order, key)))
        if lane:
            lines.append("}")
    roots = [keys[key] for key in keys if graph.nodes[key].kind in {"dataset", "scope"}]
    if roots:
        lines.append("{rank=same; " + "; ".join(roots) + ";}")
    for (source, target), kind in sorted(graph.edges.items()):
        attributes = "constraint=false, weight=0" if kind == "sequence" else "weight=6" if kind in {"data", "explicit"} else "weight=2"
        lines.append(f"{keys[source]} -> {keys[target]} [{attributes}];")
    ranks = defaultdict(list)
    for key, level in levels.items():
        ranks[level].append(keys[key])
    previous = None
    for level, members in sorted(ranks.items()):
        anchor = f"rank_{level}"
        lines.append(f'{anchor} [shape=point, width=0, height=0, style=invis];')
        lines.append("{rank=same; " + "; ".join([anchor, *members]) + ";}")
        if previous is not None:
            lines.append(f"{previous} -> {anchor} [style=invis];")
        previous = anchor
    lines.append("}")
    return "\n".join(lines), {value: key for key, value in keys.items()}


def layout_flow(graph, cancel=None, *, mode="Wide"):
    dot = shutil.which("dot")
    if dot is None or len(graph.nodes) > 3000:
        return fallback_layout(graph, cancel, mode=mode)
    source, mapping = graphviz_source(graph, mode, cancel)
    process = None
    try:
        process = subprocess.Popen([dot, "-Tjson"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 8
        first = True
        while True:
            check_cancel(cancel)
            try:
                output, _ = process.communicate(source if first else None, timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                first = False
                if time.monotonic() >= deadline:
                    process.kill()
                    process.communicate()
                    return fallback_layout(graph, cancel, mode=mode)
        if process.returncode:
            return fallback_layout(graph, cancel, mode=mode)
        payload = json.loads(output)
        bounds = [float(value) for value in payload["bb"].split(",")]
        height = bounds[3]
        boxes, objects = {}, {}
        for obj in payload.get("objects", []):
            key = mapping.get(obj.get("name"))
            if key is None:
                continue
            x, y = [float(value) for value in obj["pos"].split(",")]
            width, box_height = float(obj["width"]) * 72, float(obj["height"]) * 72
            boxes[key] = (x - width / 2 + 30, height - y - box_height / 2 + 30,
                          x + width / 2 + 30, height - y + box_height / 2 + 30)
            objects[obj["_gvid"]] = key
        if set(boxes) != set(graph.nodes):
            return fallback_layout(graph, cancel, mode=mode)
        lines = {}
        for edge in payload.get("edges", []):
            pair = (objects.get(edge["tail"]), objects.get(edge["head"]))
            if pair not in graph.edges:
                continue
            points, endpoint = [], None
            for token in edge.get("pos", "").split():
                values = token.split(",")
                if len(values) == 3:
                    if values[0] == "e":
                        endpoint = (float(values[1]) + 30, height - float(values[2]) + 30)
                elif len(values) == 2:
                    points.extend((float(values[0]) + 30, height - float(values[1]) + 30))
            if endpoint:
                points.extend(endpoint)
            if len(points) >= 4:
                lines[pair] = tuple(points)
        return FlowLayout(boxes, lines, bounds[2] + 60, height + 60, "Graphviz")
    except (OSError, ValueError, KeyError, TypeError):
        return fallback_layout(graph, cancel, mode=mode)
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate()
