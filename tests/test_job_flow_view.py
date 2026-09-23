import queue
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import Mock

from cryoet_organizer.job_flow_view import JobFlowView
from cryoet_organizer.tabs.job_list import JobListTab
from cryoet_organizer.project import ProjectData


def test_stale_background_results_are_discarded():
    view = SimpleNamespace(dead=False, generation=2, results=queue.Queue(), after=Mock(),
                           progress=Mock(), status=Mock(), _poll=Mock())
    callback = Mock()
    view.results.put((1, None, "obsolete error", callback))
    JobFlowView._poll(view)
    callback.assert_not_called()
    view.status.set.assert_not_called()
    view.progress.stop.assert_not_called()
    view.after.assert_called_once()


def test_scrollregion_is_full_layout_not_visible_items():
    view = SimpleNamespace(layout=SimpleNamespace(width=4000, height=90000), scale=.5, canvas=Mock())
    JobFlowView._set_scrollregion(view)
    view.canvas.configure.assert_called_once_with(scrollregion=(0, 0, 2000, 45000))


def test_color_change_does_not_rebuild_flow():
    tab = SimpleNamespace(view_mode_var=Mock(get=lambda: "Flow view"), flow_view=Mock(), _refresh_table_debouncer=Mock())
    JobListTab._on_color_changed(tab)
    tab.flow_view.recolor.assert_called_once()
    tab.flow_view.request.assert_not_called()
    tab._refresh_table_debouncer.schedule.assert_not_called()


def test_flow_refresh_does_not_sort_or_mutate_hidden_list(monkeypatch):
    iterate = Mock(side_effect=AssertionError("List traversal in GUI thread"))
    monkeypatch.setattr("cryoet_organizer.tabs.job_list.iter_scheduled_job_refs", iterate)
    tab = SimpleNamespace(view_mode_var=Mock(get=lambda: "Flow view"), _refresh_table=Mock())
    JobListTab.refresh_queue(tab)
    tab._refresh_table.assert_called_once()
    iterate.assert_not_called()


def test_suspending_cancels_snapshot_layout_and_paint():
    view = SimpleNamespace(dead=False, generation=2, cancel=Mock(), future=Mock(), _snapshot_id="snapshot",
                           _paint_id="paint", after_cancel=Mock(), progress=Mock())
    JobFlowView.suspend(view)
    assert view.generation == 3
    view.cancel.set.assert_called_once()
    view.future.cancel.assert_called_once()
    assert view._snapshot_id is view._paint_id is None
    assert view.after_cancel.call_count == 2


def test_destroy_does_not_call_already_destroyed_progress_widget():
    view = SimpleNamespace(dead=True, generation=2, cancel=Mock(), future=Mock(), _snapshot_id=None,
                           _paint_id=None, after_cancel=Mock(), progress=Mock())
    JobFlowView.suspend(view)
    view.progress.stop.assert_not_called()
    view.progress.pack_forget.assert_not_called()


def test_selection_only_marks_direct_neighbors_and_schedules_repaint():
    view = SimpleNamespace(neighbors={"a": {"b", "c"}, "b": {"a", "d"}}, recolor=Mock())
    JobFlowView._set_selection(view, "a")
    assert view.active_nodes == {"a", "b", "c"}
    assert view.selected_node == "a"
    view.recolor.assert_called_once()
    JobFlowView._set_selection(view, None)
    assert not view.active_nodes and view.selected_node is None


def test_highlighting_edges_keeps_dash_style_and_does_not_change_layout():
    view = SimpleNamespace(selected_node="a", scale=1, canvas=Mock(), visible_edges={("a", "b"): 1, ("b", "c"): 2},
                           _edge_styles={}, _style_generation=3)
    JobFlowView._style_edge(view, ("a", "b"))
    JobFlowView._style_edge(view, ("b", "c"))
    calls = view.canvas.itemconfigure.call_args_list
    assert calls[0].kwargs["width"] > calls[1].kwargs["width"]
    assert all("dash" not in call.kwargs for call in calls)
    assert view._edge_styles == {("a", "b"): 3, ("b", "c"): 3}


def test_layout_switch_preserves_filters_without_forcing_graph_rebuild():
    project, callback = object(), Mock()
    view = SimpleNamespace(_last_request=(project, "Dataset", "TS", callback), request=Mock())
    JobFlowView._layout_changed(view)
    view.request.assert_called_once_with(project, "Dataset", "TS", on_ready=callback)


def test_layout_cache_separates_modes_and_reuses_graph(monkeypatch):
    from cryoet_organizer.job_flow import build_flow
    build = Mock(wraps=build_flow)
    layout = Mock(side_effect=lambda graph, cancel, mode: SimpleNamespace(mode=mode))
    monkeypatch.setattr("cryoet_organizer.job_flow_view.build_flow", build)
    monkeypatch.setattr("cryoet_organizer.job_flow_view.layout_flow", layout)
    view = SimpleNamespace(suspend=Mock(), generation=1, layout_mode=Mock(), status=Mock(), progress=Mock(),
                           dead=False, executor=Mock(), results=queue.Queue(), cache=OrderedDict(), graph_cache=None)
    view.executor.submit.side_effect = lambda work: work()
    project = ProjectData()
    for mode in ("Wide", "Compact", "Wide"):
        view.layout_mode.get.return_value = mode
        JobFlowView.request(view, project)
        generation, result, error, callback = view.results.get_nowait()
        assert error == ""
        assert result[2].mode == mode
    assert build.call_count == 1
    assert layout.call_count == 2
