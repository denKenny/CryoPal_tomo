from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from cryoet_organizer.app import CryoETOrganizerApp
from cryoet_organizer.appearance import AppearanceConfig, set_project_appearance
from cryoet_organizer.dialogs import fit_outer_canvas_to_viewport
from cryoet_organizer.project import ProjectData
from cryoet_organizer.resizable_sections import ResizableSectionStack, VerticalSplitPane


class ProjectRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = CryoETOrganizerApp.__new__(CryoETOrganizerApp)
        self.app.root = Mock()
        self.app.project = ProjectData()
        self.app._current_appearance = AppearanceConfig()
        self.app.tabs = {name: Mock() for name in ("visible", "hidden")}
        self.app.active_tab_id = "visible"
        self.app._refresh_domain_map = {}
        self.app._queued_refresh_targets = set()
        self.app._pending_tab_refreshes = set()
        self.app._queued_refresh_after_id = None

        def apply(config):
            self.app._current_appearance = config

        self.app.apply_appearance_config = Mock(side_effect=apply)

    def flush(self) -> None:
        self.app.root.after.call_args.args[1]()

    def test_repeated_data_changes_do_not_restyle_or_refresh_hidden_tabs(self) -> None:
        self.app._queue_project_refresh(("visible",))
        self.app._queue_project_refresh(("hidden",))
        self.app.root.after.assert_called_once()
        self.flush()

        self.app.apply_appearance_config.assert_not_called()
        self.app.tabs["visible"].on_project_loaded.assert_called_once_with(self.app.project)
        self.app.tabs["hidden"].on_project_loaded.assert_not_called()
        self.assertEqual(self.app._pending_tab_refreshes, {"hidden"})
        self.assertIsNone(self.app._queued_refresh_after_id)

    def test_appearance_changes_are_coalesced_and_latest_value_is_applied(self) -> None:
        set_project_appearance(self.app.project, AppearanceConfig(main_background="#112233"))
        self.app._queue_project_refresh()
        latest = AppearanceConfig(main_background="#445566")
        set_project_appearance(self.app.project, latest)
        self.app._queue_project_refresh()
        self.app.apply_appearance_config.assert_not_called()
        self.flush()

        self.app.apply_appearance_config.assert_called_once_with(latest)
        self.app._queue_project_refresh()
        self.flush()
        self.app.apply_appearance_config.assert_called_once_with(latest)

    def test_appearance_change_reverted_before_refresh_needs_no_restyle(self) -> None:
        set_project_appearance(self.app.project, AppearanceConfig(main_background="#112233"))
        self.app._queue_project_refresh()
        set_project_appearance(self.app.project, AppearanceConfig())
        self.flush()
        self.app.apply_appearance_config.assert_not_called()


class LayoutRefreshTests(unittest.TestCase):
    def test_canvas_fit_preserves_horizontal_overflow_without_running_idle_callbacks(self) -> None:
        for viewport, requested, horizontal, expected in (
            (600, 900, True, 900),
            (1000, 900, True, 1000),
            (600, 900, False, 600),
        ):
            with self.subTest(viewport=viewport, horizontal=horizontal):
                inner = Mock()
                inner.winfo_reqwidth.return_value = requested
                inner.update_idletasks.side_effect = AssertionError("Nested idle processing")
                canvas = Mock()
                canvas.bbox.return_value = (0, 0, expected, 1200)
                fit_outer_canvas_to_viewport(
                    canvas, 1, inner, SimpleNamespace(width=viewport), allow_horizontal=horizontal
                )
                canvas.itemconfigure.assert_called_once_with(1, width=expected)
                canvas.configure.assert_called_once_with(scrollregion=(0, 0, expected, 1200))

    def test_section_notifications_do_not_reenter_the_idle_loop(self) -> None:
        for cls in (ResizableSectionStack, VerticalSplitPane):
            with self.subTest(cls=cls.__name__):
                pane = SimpleNamespace(
                    _layout_notify_after_id="pending",
                    on_layout_changed=Mock(),
                    update_idletasks=Mock(side_effect=AssertionError("Nested idle processing")),
                )
                cls._run_layout_changed_notification(pane)
                self.assertIsNone(pane._layout_notify_after_id)
                pane.on_layout_changed.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
