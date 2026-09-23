from copy import deepcopy
from threading import Event

import pytest

from cryoet_organizer.job_flow import FlowCancelled, FlowGraph, FlowNode
from cryoet_organizer.job_flow_layout import fallback_layout, graphviz_source, layout_rows


def graph_with_jobs(count):
    nodes = {"project": FlowNode("project", "Project", kind="project", order=(0, 0)),
             "dataset:A": FlowNode("dataset:A", "A", kind="dataset", lane="dataset:A", order=(1, 0))}
    for i in range(count):
        key = f"job:{i}"
        nodes[key] = FlowNode(key, key, lane="dataset:A", order=(2, i))
    return FlowGraph(nodes, {("project", "dataset:A"): "membership", ("dataset:A", "job:0"): "membership"})


def assert_layout_valid(graph, layout):
    assert set(layout.boxes) == set(graph.nodes)
    assert set(layout.lines) == set(graph.edges)
    rows = {}
    for box in layout.boxes.values():
        x1, y1, x2, y2 = box
        assert 0 <= x1 < x2 < layout.width
        assert 0 <= y1 < y2 < layout.height
        rows.setdefault(y1, []).append((x1, x2))
    for row in rows.values():
        ordered = sorted(row)
        assert all(a[1] < b[0] for a, b in zip(ordered, ordered[1:]))
    for (a, b), kind in graph.edges.items():
        if kind != "sequence":
            assert layout.boxes[a][3] < layout.boxes[b][1]
    for points in layout.lines.values():
        assert all(0 <= x < layout.width for x in points[0::2])
        assert all(0 <= y < layout.height for y in points[1::2])


def test_chronological_chain_fans_out_without_changing_graph():
    graph = graph_with_jobs(16)
    graph.edges.update({(f"job:{i}", f"job:{i+1}"): "sequence" for i in range(15)})
    original = deepcopy(graph)
    wide = fallback_layout(graph)
    compact = fallback_layout(graph, mode="Compact")
    assert wide.width > compact.width
    assert wide.height < compact.height
    assert wide.boxes["job:0"][1] == wide.boxes["job:3"][1]
    assert wide.boxes["job:4"][1] > wide.boxes["job:3"][1]
    assert_layout_valid(graph, wide)
    assert_layout_valid(graph, compact)
    assert graph == original


def test_true_linear_chain_remains_downward():
    graph = graph_with_jobs(16)
    graph.edges.update({(f"job:{i}", f"job:{i+1}"): "data" for i in range(15)})
    layout = fallback_layout(graph)
    assert len({box[0] for key, box in layout.boxes.items() if key.startswith("job:")}) == 1
    assert_layout_valid(graph, layout)


@pytest.mark.parametrize("mode", ["Wide", "Compact"])
def test_fork_and_merge_stay_close_and_acyclic(mode):
    graph = graph_with_jobs(4)
    graph.edges.update({("job:0", "job:1"): "data", ("job:0", "job:2"): "inferred",
                        ("job:1", "job:3"): "data", ("job:2", "job:3"): "explicit"})
    layout = fallback_layout(graph, mode=mode)
    assert layout.boxes["job:1"][1] == layout.boxes["job:2"][1]
    center = lambda key: (layout.boxes[key][0] + layout.boxes[key][2]) / 2
    assert center("job:3") == (center("job:1") + center("job:2")) / 2
    assert_layout_valid(graph, layout)


def test_multiple_dataset_lanes_and_cross_dataset_dependencies():
    graph = graph_with_jobs(16)
    graph.nodes["dataset:B"] = FlowNode("dataset:B", "B", kind="dataset", lane="dataset:B", order=(1, 1))
    graph.edges["project", "dataset:B"] = "membership"
    for i in range(8, 16):
        graph.nodes[f"job:{i}"].lane = "dataset:B"
        graph.edges["job:0", f"job:{i}"] = "data"
    graph.edges["job:12", "job:15"] = "explicit"
    layout = fallback_layout(graph)
    assert_layout_valid(graph, layout)
    assert max(layout.boxes[f"job:{i}"][2] for i in range(8)) < min(layout.boxes[f"job:{i}"][0] for i in range(8, 16))


def test_soft_edge_can_point_up_without_constraining_real_dependencies():
    graph = graph_with_jobs(6)
    graph.edges.update({(f"job:{i}", f"job:{i+1}"): "data" for i in range(4)})
    graph.edges["job:4", "job:5"] = "sequence"
    layout = fallback_layout(graph)
    assert layout.boxes["job:5"][1] < layout.boxes["job:4"][1]
    assert_layout_valid(graph, layout)


def test_graphviz_chronology_edges_do_not_constrain_ranks():
    graph = graph_with_jobs(8)
    graph.edges.update({(f"job:{i}", f"job:{i+1}"): "sequence" for i in range(7)})
    source, mapping = graphviz_source(graph)
    identifiers = {key: identifier for identifier, key in mapping.items()}
    for a, b in graph.edges:
        if graph.edges[a, b] == "sequence":
            assert f'{identifiers[a]} -> {identifiers[b]} [constraint=false, weight=0];' in source
            assert f'{identifiers[a]} -> {identifiers[b]} [style=invis' not in source
    wide, _, _ = layout_rows(graph)
    compact, _, _ = layout_rows(graph, "Compact")
    assert max(wide.values()) < max(compact.values())


def test_layout_is_stable_when_dictionary_insertion_order_changes():
    graph = graph_with_jobs(12)
    other = FlowGraph(dict(reversed(list(graph.nodes.items()))), dict(reversed(list(graph.edges.items()))))
    assert fallback_layout(graph).boxes == fallback_layout(other).boxes


def test_large_sparse_history_uses_bounded_row_capacity_and_cancels():
    graph = graph_with_jobs(10000)
    levels, rows, _ = layout_rows(graph)
    assert max(map(len, rows.values())) == 4
    assert max(levels.values()) == 2501
    cancel = Event()
    cancel.set()
    with pytest.raises(FlowCancelled):
        graphviz_source(graph, cancel=cancel)
