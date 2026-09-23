from copy import deepcopy
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
import json
import subprocess

import pytest

from cryoet_organizer.job_execution import create_history_entry
from cryoet_organizer.job_flow import (
    FlowCancelled, build_flow, filter_flow, flow_signature, snapshot_records, topological_order,
)
from cryoet_organizer.job_flow_layout import fallback_layout, graphviz_source, layout_flow
from cryoet_organizer.job_provenance import capture_job_provenance, inferred_io, provenance_for_record
from cryoet_organizer.project import JobHistoryEntry, ProjectData


def record(key, minute=0, dataset="A", **overrides):
    result = dict(entry_id=key, job_name="Job", owner_kind="dataset", owner_name=dataset,
                  processing_tab="Processing: Particle jobs", execution_mode="local", group="",
                  timestamp=f"2026-09-01T12:{minute:02d}:00Z", started_at="", submitted_at="",
                  finished_at=f"2026-09-01T12:{minute:02d}:30Z", status="succeeded", action="ran",
                  working_directory="/data/" + dataset, command="", parameters={}, artifacts={})
    result.update(overrides)
    return result


def test_legacy_history_derives_artifact_edges_without_mutation():
    records = [record("producer", parameters={"output_star": "particles.star"}),
               record("consumer", 1, parameters={"input_star": "particles.star"})]
    original = deepcopy(records)
    graph = build_flow(records, "Project", ["A"])
    assert graph.edges["job:producer", "job:consumer"] == "data"
    assert records == original
    assert len(topological_order(graph)) == len(graph.nodes)


def test_relative_paths_never_match_across_datasets_or_by_basename():
    graph = build_flow([record("a", parameters={"output_star": "particles.star"}),
                        record("b", 1, "B", parameters={"input_star": "particles.star"})], "P", ["A", "B"])
    assert ("job:a", "job:b") not in graph.edges


def test_shared_data_keeps_other_dataset_ancestors_when_filtered():
    graph = build_flow([record("a", parameters={"output_star": "/shared/particles.star"}),
                        record("b", 1, "B", parameters={"input_star": "/shared/particles.star"})], "P", ["A", "B"])
    selected = filter_flow(graph, "B")
    assert selected.edges["job:a", "job:b"] == "data"
    assert "dataset:A" in selected.nodes


def test_unknown_completion_is_not_presented_as_verified_dependency():
    graph = build_flow([record("a", status="unknown", finished_at="", parameters={"o": "a.star"}),
                        record("b", 1, parameters={"i": "a.star"})], "P", ["A"])
    assert graph.edges["job:a", "job:b"] == "inferred"


@pytest.mark.parametrize("status", ["failed", "running", "cancelled"])
def test_failed_or_running_writer_blocks_old_artifact_dependency(status):
    graph = build_flow([record("a", parameters={"o": "a.star"}),
                        record("b", 1, status=status, parameters={"o": "a.star"}),
                        record("c", 2, parameters={"i": "a.star"})], "P", ["A"])
    assert ("job:a", "job:c") not in graph.edges
    assert graph.edges["job:b", "job:c"] == "sequence"


def test_most_recent_successful_writer_wins():
    graph = build_flow([record("a", parameters={"o": "a.star"}),
                        record("b", 1, parameters={"o": "a.star"}),
                        record("c", 2, parameters={"i": "a.star"})], "P", ["A"])
    assert graph.edges["job:b", "job:c"] == "data"
    assert ("job:a", "job:c") not in graph.edges


def test_equal_finish_and_start_timestamp_can_establish_dependency():
    graph = build_flow([record("a", finished_at="2026-09-01T12:01:00Z", parameters={"o": "a.star"}),
                        record("b", 1, parameters={"i": "a.star"})], "P", ["A"])
    assert graph.edges["job:a", "job:b"] == "data"


def test_consumer_during_successful_overwrite_is_not_linked_to_old_data():
    graph = build_flow([record("a", parameters={"o": "a.star"}),
                        record("b", 1, finished_at="2026-09-01T12:03:00Z", parameters={"o": "a.star"}),
                        record("c", 2, parameters={"i": "a.star"})], "P", ["A"])
    assert ("job:a", "job:c") not in graph.edges
    assert graph.edges["job:b", "job:c"] == "sequence"


def test_scheduled_writer_does_not_invalidate_existing_data():
    graph = build_flow([record("a", parameters={"o": "a.star"}),
                        record("b", 1, status="scheduled", action="scheduled", parameters={"o": "a.star"}),
                        record("c", 2, parameters={"i": "a.star"})], "P", ["A"])
    assert graph.edges["job:a", "job:c"] == "data"


def test_in_place_update_uses_previous_producer_not_itself():
    graph = build_flow([record("a", parameters={"o": "a.star"}),
                        record("b", 1, parameters={"i": "a.star", "o": "a.star"})], "P", ["A"])
    assert graph.edges["job:a", "job:b"] == "data"


def test_equally_recent_producers_are_ambiguous():
    graph = build_flow([record("a", parameters={"o": "a.star"}),
                        record("b", parameters={"o": "a.star"}),
                        record("c", 2, parameters={"i": "a.star"})], "P", ["A"])
    assert not any(kind == "data" for kind in graph.edges.values())
    assert graph.warnings


def test_empty_missing_dates_and_conflicting_parents_remain_acyclic():
    assert len(build_flow([], "P", []).nodes) == 1
    graph = build_flow([record("a", timestamp="invalid", finished_at="", artifacts={"provenance": {"parents": ["b"]}}),
                        record("b", timestamp="", finished_at="", artifacts={"provenance": {"parents": ["a"]}})], "P", ["A"])
    assert graph.warnings
    assert len(topological_order(graph)) == 4


def test_grouped_history_deduplicates_owners_and_uses_exact_ts_names():
    a = record("group", artifacts={"processed_ts": [
        {"dataset_name": "A", "ts_name": "Position_18"},
        {"dataset_name": "B", "ts_name": "Position_18_2"}]})
    b = {**deepcopy(a), "owner_name": "B"}
    graph = build_flow([a, b, record("other", 1, parameters={"ts_name": "Position_18_2"})], "P", ["A", "B"])
    assert graph.nodes["job:group"].datasets == ("A", "B")
    assert "2 TS" in graph.nodes["job:group"].label
    selected = filter_flow(graph, "A", "Position_18")
    assert "1 of 2 TS" in selected.nodes["job:group"].label
    assert "job:other" not in selected.nodes


def test_only_shared_run_ids_aggregate_not_repeated_job_names():
    records = [record("a", parameters={"ts_name": "TS1"}), record("b", 1, parameters={"ts_name": "TS2"})]
    assert len([n for n in build_flow(records, "P", ["A"]).nodes.values() if n.kind == "job"]) == 2
    for item in records:
        item["artifacts"]["provenance"] = {"run_id": "same-run"}
    graph = build_flow(records, "P", ["A"])
    assert graph.nodes["job:a"].entries == ("a", "b")
    assert "2 TS" in graph.nodes["job:a"].label


def test_internal_dependency_and_interleaved_runs_are_not_contracted():
    records = [record("a", parameters={"o": "a.star"}), record("b", 1, parameters={"i": "a.star"})]
    for item in records:
        item["artifacts"]["provenance"] = {"run_id": "same-run"}
    graph = build_flow(records, "P", ["A"])
    assert graph.edges["job:a", "job:b"] == "data"
    records[1]["parameters"] = {}
    records.insert(1, record("other", timestamp="2026-09-01T12:00:40Z"))
    assert len([n for n in build_flow(records, "P", ["A"]).nodes.values() if n.kind == "job"]) == 3


def test_population_history_is_not_lost():
    graph = build_flow([record("m", owner_kind="population", owner_name="Population")], "P", [])
    assert graph.nodes["job:m"].lane == "population:Population"
    assert graph.edges["project", "population:Population"] == "membership"


def test_legacy_deferred_selection_is_recovered_without_processed_ts():
    item = record("a", parameters={"ts_name": "2 TS selected", "selected_entries": json.dumps([
        {"dataset_name": "A", "ts_name": "TS1"}, {"dataset_name": "B", "ts_name": "TS2"}])})
    graph = build_flow([item], "P", ["A", "B"])
    assert graph.nodes["job:a"].ts == {("A", "TS1"), ("B", "TS2")}


def test_custom_single_ts_command_does_not_inherit_whole_selection():
    item = record("a", parameters={"ts_name": "TS1"}, artifacts={"custom_job_id": "custom", "processed_ts": [
        {"dataset_name": "A", "ts_name": "TS1"}, {"dataset_name": "B", "ts_name": "TS2"}]})
    graph = build_flow([item], "P", ["A", "B"])
    assert graph.nodes["job:a"].ts == {("A", "TS1")}
    assert graph.nodes["job:a"].datasets == ("A",)


def test_custom_artifact_roles_are_opt_in():
    item = record("c", command="tool --i a.star --o b.star", artifacts={"custom_job_id": "custom"})
    assert inferred_io(item) == ([], [])
    item["artifacts"]["custom_job_definition"] = {"parameters": [
        {"key": "source", "flag": "--i", "extra": {"io_role": "input"}},
        {"key": "destination", "flag": "--o", "extra": {"io_role": "output"}}]}
    assert inferred_io(item) == (["/data/A/a.star"], ["/data/A/b.star"])


def test_directory_wildcard_and_shell_expressions_not_artifact_evidence():
    item = record("a", parameters={"output_directory": "/data/output.star", "i": "*.star", "mask": "$MASK.mrc"})
    assert inferred_io(item) == ([], [])


def test_provenance_invalidates_on_command_changes_and_survives_roundtrip():
    entry = create_history_entry(action="ran", job_name="Job", group="", command="tool --o /tmp/old.star")
    capture_job_provenance(entry, run_id="run")
    entry.command = "tool --o /tmp/new.star"
    capture_job_provenance(entry)
    loaded = JobHistoryEntry.from_dict(entry.to_dict())
    assert loaded.artifacts["provenance"]["run_id"] == "run"
    assert loaded.artifacts["provenance"]["inferred_outputs"] == ["/tmp/new.star"]


def test_snapshots_do_not_copy_plot_payloads_and_are_independent():
    entry = create_history_entry(action="ran", job_name="Job", group="", command="true")
    entry.artifacts["large_plot"] = object()
    dataset = SimpleNamespace(dataset_name="A", processing_folder="/A", job_history=[entry])
    project = ProjectData(datasets=[dataset])
    records = list(snapshot_records(project))
    assert "large_plot" not in records[0]["artifacts"]
    records[0]["artifacts"]["provenance"]["run_id"] = "changed"
    assert entry.artifacts["provenance"]["run_id"] != "changed"
    before = flow_signature(records, "P", ["A"])
    records[0]["status"] = "succeeded"
    assert flow_signature(records, "P", ["A"]) != before


def test_dependency_free_layout_contains_entire_tree_and_downward_edges(monkeypatch):
    monkeypatch.setattr("cryoet_organizer.job_flow_layout.shutil.which", lambda _: None)
    graph = build_flow([record(str(i), i, "A" if i % 2 else "B") for i in range(30)], "P", ["A", "B"])
    layout = layout_flow(graph)
    assert layout.engine == "Dataset lanes"
    assert set(layout.boxes) == set(graph.nodes)
    for x1, y1, x2, y2 in layout.boxes.values():
        assert 0 <= x1 < x2 < layout.width
        assert 0 <= y1 < y2 < layout.height
    for (a, b), kind in graph.edges.items():
        if kind != "sequence":
            assert layout.boxes[a][3] < layout.boxes[b][1]


def test_graphviz_uses_safe_identifiers_not_history_text():
    graph = build_flow([record('x";evil', job_name='bad";')], 'project"', ["A"])
    source, mapping = graphviz_source(graph)
    assert "evil" not in source and "bad" not in source
    assert set(mapping.values()) == set(graph.nodes)


def test_cancellation_before_graph_or_layout():
    cancel = Event()
    cancel.set()
    with pytest.raises(FlowCancelled):
        build_flow([record("a")], "P", ["A"], cancel=cancel)
    with pytest.raises(FlowCancelled):
        fallback_layout(build_flow([], "P", []), cancel)


def test_broken_graphviz_falls_back(monkeypatch):
    monkeypatch.setattr("cryoet_organizer.job_flow_layout.shutil.which", lambda _: "/dot")
    process = Mock(returncode=0)
    process.communicate.return_value = ("not json", "")
    process.poll.return_value = 0
    monkeypatch.setattr("cryoet_organizer.job_flow_layout.subprocess.Popen", lambda *a, **k: process)
    assert layout_flow(build_flow([], "P", [])).engine == "Dataset lanes"


def test_graphviz_timeout_kills_process_and_uses_fallback(monkeypatch):
    monkeypatch.setattr("cryoet_organizer.job_flow_layout.shutil.which", lambda _: "/dot")
    process = Mock()
    process.communicate.side_effect = [subprocess.TimeoutExpired("dot", .1), ("", "")]
    process.poll.return_value = -9
    monkeypatch.setattr("cryoet_organizer.job_flow_layout.subprocess.Popen", lambda *a, **k: process)
    monkeypatch.setattr("cryoet_organizer.job_flow_layout.time.monotonic", Mock(side_effect=[0, 9]))
    assert layout_flow(build_flow([], "P", [])).engine == "Dataset lanes"
    process.kill.assert_called_once()


def test_graphviz_result_is_parsed_in_canvas_coordinates(monkeypatch):
    monkeypatch.setattr("cryoet_organizer.job_flow_layout.shutil.which", lambda _: "/dot")
    graph = build_flow([], "P", ["A"])
    _, mapping = graphviz_source(graph)
    payload = {"bb": "0,0,300,300", "objects": [
        {"name": name, "_gvid": i, "pos": f"150,{250 - i * 150}", "width": "3", "height": "1"}
        for i, name in enumerate(mapping)], "edges": [{"tail": 0, "head": 1, "pos": "e,150,140 150,214 150,175 150,150"}]}
    process = Mock(returncode=0)
    process.communicate.return_value = (json.dumps(payload), "")
    process.poll.return_value = 0
    monkeypatch.setattr("cryoet_organizer.job_flow_layout.subprocess.Popen", lambda *a, **k: process)
    layout = layout_flow(graph)
    assert layout.engine == "Graphviz"
    assert set(layout.boxes) == set(graph.nodes)
    assert set(layout.lines) == set(graph.edges)
    assert layout.boxes["project"][1] < layout.boxes["dataset:A"][1]
    assert layout.width == layout.height == 360
