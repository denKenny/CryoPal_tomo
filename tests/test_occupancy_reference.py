import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryoet_organizer.star_merge import ParticleAbundancePlot
from cryoet_organizer.tabs.particles import ParticlesTab


class OccupancyReferenceTests(unittest.TestCase):
    def test_sort_descends_per_plot_without_mutating_source(self):
        tab = ParticlesTab.__new__(ParticlesTab)
        rows = [{"dataset": "D", "tomogram": str(i), "condition": condition, "count": count}
                for i, (condition, count) in enumerate([("A", 0), ("B", 9), ("A", 5), ("B", 2)])]
        plot = ParticleAbundancePlot(Path("input.star"), "2d", "total", True,
                                     occupancy=rows, sort_by_occupancy=True)
        canvases = [MagicMock() for _ in range(3)]
        with patch("cryoet_organizer.tabs.particles.ttk.Label"), \
                patch("cryoet_organizer.tabs.particles.tk.Canvas", side_effect=canvases):
            tab._draw_occupancy_plots(MagicMock(), plot, True, 800)
        for canvas, expected_count in zip(canvases, [4, 2, 2]):
            tops = [call.args[1] for call in canvas.create_rectangle.call_args_list]
            self.assertEqual(len(tops), expected_count)
            self.assertEqual(tops, sorted(tops))
        self.assertEqual([row["count"] for row in rows], [0, 9, 5, 2])

    def test_reference_uses_each_displayed_group_and_excludes_equal_counts(self):
        tab = ParticlesTab.__new__(ParticlesTab)
        rows = [{"dataset": "D", "tomogram": str(i), "condition": condition, "count": count}
                for i, (condition, count) in enumerate([("A", 0), ("A", 2), ("A", 4), ("B", 10)])]
        for mode, expected in [
            ("Plot mean value", ["mean=4.00 | 1 TS> mean, 2 TS< mean",
                                 "mean=2.00 | 1 TS> mean, 1 TS< mean",
                                 "mean=10.00 | 0 TS> mean, 0 TS< mean"]),
            ("Plot median value", ["median=3.00 | 2 TS> median, 2 TS< median",
                                   "median=2.00 | 1 TS> median, 1 TS< median",
                                   "median=10.00 | 0 TS> median, 0 TS< median"]),
            ("None", []),
        ]:
            plot = ParticleAbundancePlot(Path("input.star"), "2d", "total", True,
                                         occupancy=rows, occupancy_reference=mode)
            canvases = [MagicMock() for _ in range(3)]
            with patch("cryoet_organizer.tabs.particles.ttk.Label") as labels, \
                    patch("cryoet_organizer.tabs.particles.tk.Canvas", side_effect=canvases):
                tab._draw_occupancy_plots(MagicMock(), plot, True, 800)
            headings = [call.kwargs["text"] for call in labels.call_args_list]
            for heading, suffix in zip(headings, expected):
                self.assertTrue(heading.endswith(suffix), heading)
            for canvas in canvases:
                reference_lines = [call for call in canvas.create_line.call_args_list
                                   if call.kwargs.get("dash") == (6, 4)]
                self.assertEqual(len(reference_lines), 0 if mode == "None" else 1)
            if mode == "None":
                self.assertEqual(headings[0], "Tomogram occupancy | All | TS=4")

    def test_history_round_trip_and_older_entries(self):
        tab = ParticlesTab.__new__(ParticlesTab)
        plot = ParticleAbundancePlot(Path("input.star"), "2d", "total", False,
                                     occupancy_reference="Plot median value", sort_by_occupancy=True)
        payload = json.loads(json.dumps(tab._serialize_abundance_plot(plot)))
        self.assertEqual(tab._deserialize_abundance_plot(payload), plot)
        del payload["occupancy_reference"]
        del payload["sort_by_occupancy"]
        self.assertEqual(tab._deserialize_abundance_plot(payload).occupancy_reference, "None")
        self.assertFalse(tab._deserialize_abundance_plot(payload).sort_by_occupancy)
