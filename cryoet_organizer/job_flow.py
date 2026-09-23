"""History-to-DAG projection, shared by global, dataset and TS views."""
from __future__ import annotations

import hashlib
import heapq
import json
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from cryoet_organizer.job_provenance import normalize_artifact, provenance_for_record


class FlowCancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise FlowCancelled()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def record_order(record):
    planned = record.get("action") in {"scheduled", "waiting"} or record.get("status") in {"scheduled", "waiting"}
    when = timestamp(record.get("started_at") or record.get("submitted_at") or record.get("timestamp"))
    return (int(planned), when if when is not None else float("inf"), record["entry_id"])


def snapshot_records(project):
    """Yield small snapshots on the UI thread; callers can process these in chunks."""
    snapshots = {}
    for kind, owners in (("dataset", project.datasets), ("population", project.m_populations)):
        for owner in owners:
            name = owner.dataset_name if kind == "dataset" else owner.name
            for entry in owner.job_history:
                cwd = getattr(owner, "processing_folder", "") or getattr(owner, "directory", "")
                if entry.entry_id in snapshots:
                    yield {**snapshots[entry.entry_id], "owner_kind": kind, "owner_name": name, "cwd": cwd}
                    continue
                artifacts = entry.artifacts if isinstance(entry.artifacts, dict) else {}
                kept = {key: artifacts[key] for key in
                        ("provenance", "processed_ts", "custom_job_id", "custom_job_definition", "workflow_catalog_namespace")
                        if key in artifacts}
                # JSON-copy only provenance metadata, not potentially large saved plot arrays.
                kept = json.loads(json.dumps(kept, default=str))
                record = {key: getattr(entry, key, "") for key in
                          ("entry_id", "job_name", "processing_tab", "group", "timestamp", "started_at",
                           "finished_at", "submitted_at", "status", "action", "command", "execution_mode", "working_directory")}
                record.update(parameters=dict(entry.parameters), artifacts=kept, owner_kind=kind, owner_name=name,
                              cwd=cwd)
                snapshots[entry.entry_id] = record
                yield record


@dataclass
class FlowNode:
    key: str
    label: str
    kind: str = "job"
    entries: tuple[str, ...] = ()
    datasets: tuple[str, ...] = ()
    ts: frozenset[tuple[str, str]] = frozenset()
    lane: str = ""
    order: tuple = ()
    colors: dict[str, str] = field(default_factory=dict)


@dataclass
class FlowGraph:
    nodes: dict[str, FlowNode] = field(default_factory=dict)
    edges: dict[tuple[str, str], str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def topological_order(graph: FlowGraph) -> list[str]:
    successors = defaultdict(list)
    indegree = dict.fromkeys(graph.nodes, 0)
    for source, target in graph.edges:
        successors[source].append(target)
        indegree[target] += 1
    ready = [(graph.nodes[key].order, key) for key, count in indegree.items() if not count]
    heapq.heapify(ready)
    result = []
    while ready:
        _, key = heapq.heappop(ready)
        result.append(key)
        for target in successors[key]:
            indegree[target] -= 1
            if not indegree[target]:
                heapq.heappush(ready, (graph.nodes[target].order, target))
    if len(result) != len(graph.nodes):
        raise ValueError("The history graph contains a cycle.")
    return result


def build_flow(records, project_name, dataset_names, *, cancel=None) -> FlowGraph:
    graph = FlowGraph()
    unique, owners = {}, defaultdict(set)
    known_datasets = set(dataset_names)
    for record in records:
        check_cancel(cancel)
        key = record["entry_id"]
        unique.setdefault(key, record)
        owners[key].add((record["owner_kind"], record["owner_name"]))
    sorted_records = sorted(unique.values(), key=record_order)
    order = {record["entry_id"]: index for index, record in enumerate(sorted_records)}
    provenance, scopes, ts_sets, record_edges = {}, {}, {}, {}
    producers = defaultdict(list)
    for record in sorted_records:
        check_cancel(cancel)
        key = record["entry_id"]
        prov = provenance_for_record(record)
        provenance[key] = prov
        ts = set()
        params = record.get("parameters", {})
        processed = record.get("artifacts", {}).get("processed_ts", [])
        if not isinstance(processed, list) or not processed:
            try:
                processed = json.loads(params.get("selected_entries", "[]"))
            except (TypeError, ValueError):
                processed = []
        for item in processed if isinstance(processed, list) else []:
            if isinstance(item, dict) and item.get("dataset_name") and item.get("ts_name"):
                ts.add((str(item["dataset_name"]), str(item["ts_name"])))
        names = {name for kind, name in owners[key] if kind == "dataset"}
        names.update(name for name, _ in ts)
        declared = str(params.get("datasets", ""))
        names.update(name for name in known_datasets if name == declared or name in declared.split(", "))
        ts_name = str(params.get("ts_name", "")).strip()
        concrete_ts = ts_name and not re.match(r"^\d+ TS", ts_name) and ts_name not in {"-", "Deferred selection"} and "," not in ts_name
        if concrete_ts and record.get("artifacts", {}).get("custom_job_id") and record["owner_kind"] == "dataset":
            ts = {(record["owner_name"], ts_name)}
            names = {record["owner_name"]}
        elif len(names) == 1 and concrete_ts:
            # Older Custom entries sometimes stored the full selection alongside a
            # single concrete command. The concrete TS is the scope of that entry.
            if not ts:
                ts = {(next(iter(names)), ts_name)}
        scopes[key], ts_sets[key] = names, ts
        cwd = record.get("working_directory") or record.get("cwd", "")

        def paths(direction):
            result = set(prov.get("inferred_" + direction, []))
            for artifact in prov.get(direction, []):
                if isinstance(artifact, dict):
                    if artifact.get("kind", "file") != "file":
                        continue
                    artifact = artifact.get("path", "")
                path = normalize_artifact(artifact, cwd)
                if path:
                    result.add(path)
            return result

        prov = dict(prov)
        prov["resolved_inputs"], prov["resolved_outputs"] = paths("inputs"), paths("outputs")
        provenance[key] = prov
        for parent in prov.get("parents", []):
            if parent in order and parent != key:
                if order[parent] < order[key]:
                    record_edges[parent, key] = "explicit"
                else:
                    graph.warnings.append("A conflicting or undated explicit dependency was not drawn.")
        # Only finished successful producers establish artifact dependencies. Legacy records
        # with unknown completion remain in the chronological view without invented success.
        finished = timestamp(record.get("finished_at"))
        status = record.get("status", "")
        strong = finished is not None and status in {"completed", "succeeded", "success"}
        attempted = timestamp(record.get("started_at") or record.get("submitted_at") or record.get("timestamp"))
        not_executed = record.get("action") in {"copied", "scheduled", "waiting"} or status in {"copied", "scheduled", "waiting", "created"}
        if not not_executed and attempted is not None:
            evidence = "data" if strong else "inferred" if status in {"", "unknown"} and record.get("action") in {"ran", "submitted", "completed"} else "blocked"
            for path in prov["resolved_outputs"]:
                if strong and finished > attempted:
                    # Consumers that start during an overwrite cannot safely refer
                    # to either the preceding or the not-yet-finished output.
                    producers[path].append((attempted, key, "blocked"))
                producers[path].append((finished if strong else attempted, key, evidence))
    for items in producers.values():
        items.sort()
    for record in sorted_records:
        check_cancel(cancel)
        key = record["entry_id"]
        started = timestamp(record.get("started_at") or record.get("submitted_at") or record.get("timestamp"))
        if started is None or record.get("action") == "copied":
            continue
        for path in provenance[key]["resolved_inputs"]:
            items = producers.get(path, [])
            index = bisect_right(items, (started, "\uffff", "\uffff")) - 1
            while index >= 0 and items[index][1] == key:
                index -= 1
            if index < 0:
                continue
            finished, parent, evidence = items[index]
            if index > 0 and items[index - 1][0] == finished:
                graph.warnings.append("An input has multiple equally recent producers; no dependency was assumed.")
                continue
            if evidence != "blocked" and order[parent] < order[key]:
                record_edges.setdefault((parent, key), evidence)

    groups = defaultdict(list)
    for record in sorted_records:
        key = record["entry_id"]
        # A shared run ID is necessary, but never collapse a run that contains an
        # internal dependency or a different operation/parameter set.
        scalars = {k: v for k, v in record.get("parameters", {}).items()
                   if k not in {"ts_name", "dataset_name", "datasets"} and not any(x in k.lower() for x in ("input", "output", "path", "file", "directory"))}
        token = (provenance[key].get("run_id") or key, record.get("job_name"), record.get("processing_tab"),
                 record.get("execution_mode"), json.dumps(scalars, sort_keys=True, default=str))
        groups[token].append(key)
    record_node = {}
    successors = defaultdict(set)
    for a, b in record_edges:
        successors[a].add(b)
    for members in groups.values():
        check_cancel(cancel)
        member_set = set(members)
        internal = any(successors[a] & member_set for a in members)
        # Interleaved runs could create cycles when contracted, so only contiguous runs aggregate.
        contiguous = max(order[key] for key in members) - min(order[key] for key in members) + 1 == len(members)
        batches = [members] if not internal and contiguous else [[key] for key in members]
        for batch in batches:
            first = unique[batch[0]]
            node_key = "job:" + batch[0]
            names = set().union(*(scopes[key] for key in batch))
            ts = set().union(*(ts_sets[key] for key in batch))
            populations = {name for key in batch for kind, name in owners[key] if kind == "population"}
            lane = "dataset:" + next(iter(names)) if len(names) == 1 else "shared" if names else "population:" + next(iter(populations)) if len(populations) == 1 else "project-wide"
            status = {unique[key].get("status") or unique[key].get("action") or "unknown" for key in batch}
            label = first.get("job_name") or "Job"
            if ts:
                label += f"\n{len(ts)} TS"
            elif len(batch) > 1:
                label += f"\n{len(batch)} jobs"
            label += "\n" + (next(iter(status)) if len(status) == 1 else "mixed status")
            graph.nodes[node_key] = FlowNode(node_key, label, entries=tuple(batch), datasets=tuple(sorted(names)),
                                             ts=frozenset(ts), lane=lane, order=(2, order[batch[0]], node_key),
                                             colors={"Action": next(iter(status)) if len(status) == 1 else "Mixed",
                                                     "Dataset": ", ".join(sorted(names)) or " / ".join(sorted(populations)),
                                                     "Processing tab": first.get("processing_tab", ""),
                                                     "Mode": first.get("execution_mode", "local")})
            for key in batch:
                record_node[key] = node_key
    for (source, target), kind in record_edges.items():
        a, b = record_node[source], record_node[target]
        if a != b:
            graph.edges[a, b] = kind

    graph.nodes["project"] = FlowNode("project", project_name, kind="project", order=(0, 0, ""))
    lanes = set(node.lane for node in graph.nodes.values() if node.kind == "job")
    lanes.update("dataset:" + name for name in dataset_names)
    for index, lane in enumerate(sorted(lanes)):
        label = lane.split(":", 1)[1] if ":" in lane else "Shared processing" if lane == "shared" else "Project-wide jobs"
        graph.nodes[lane] = FlowNode(lane, label, kind="dataset" if lane.startswith("dataset:") else "scope",
                                     datasets=(label,) if lane.startswith("dataset:") else (), lane=lane, order=(1, index, lane))
        graph.edges["project", lane] = "membership"
    incoming = {b for a, b in graph.edges if graph.nodes[a].kind == "job"}
    previous_by_lane = {}
    for node in sorted((n for n in graph.nodes.values() if n.kind == "job"), key=lambda n: n.order):
        previous = previous_by_lane.get(node.lane)
        if node.key not in incoming:
            if previous and (not previous.ts or not node.ts or previous.ts & node.ts):
                graph.edges[previous.key, node.key] = "sequence"
            else:
                graph.edges[node.lane, node.key] = "membership"
        previous_by_lane[node.lane] = node
    topological_order(graph)
    try:
        import networkx as nx
    except ImportError:
        pass
    else:
        dag = nx.DiGraph()
        dag.add_nodes_from(graph.nodes)
        dag.add_edges_from(graph.edges)
        if not nx.is_directed_acyclic_graph(dag):
            raise ValueError("Invalid cyclic processing graph.")
    graph.warnings = list(dict.fromkeys(graph.warnings))
    return graph


def filter_flow(graph, dataset="All", tomogram="All"):
    if dataset == "All":
        return graph
    keep = {"project", "dataset:" + dataset}
    for node in graph.nodes.values():
        if node.kind == "job" and dataset in node.datasets and (tomogram == "All" or not node.ts or (dataset, tomogram) in node.ts):
            keep.add(node.key)
    # Retain genuine upstream context, including shared processing across datasets.
    parents = defaultdict(list)
    for (a, b), kind in graph.edges.items():
        if kind in {"data", "explicit"}:
            parents[b].append(a)
    pending = list(keep)
    while pending:
        for parent in parents[pending.pop()]:
            if parent not in keep:
                keep.add(parent)
                pending.append(parent)
    for key in list(keep):
        if key in graph.nodes and graph.nodes[key].lane:
            keep.add(graph.nodes[key].lane)
    result = FlowGraph(warnings=list(graph.warnings))
    for key in keep:
        if key not in graph.nodes:
            continue
        node = graph.nodes[key]
        if node.kind == "job" and node.ts:
            selected = {(ds, ts) for ds, ts in node.ts if ds == dataset and (tomogram == "All" or ts == tomogram)}
            if selected and len(selected) != len(node.ts):
                from dataclasses import replace
                node = replace(node, label=node.label.replace(f"{len(node.ts)} TS", f"{len(selected)} of {len(node.ts)} TS"))
        result.nodes[key] = node
    result.edges = {(a, b): kind for (a, b), kind in graph.edges.items() if a in result.nodes and b in result.nodes}
    incoming = {b for a, b in result.edges}
    for key, node in result.nodes.items():
        if key != "project" and key not in incoming:
            result.edges[node.lane if node.kind == "job" else "project", key] = "membership"
    return result


def flow_signature(records, name, datasets):
    return hashlib.sha256(json.dumps((records, name, datasets), sort_keys=True, default=str).encode()).hexdigest()
