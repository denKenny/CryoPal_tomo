from __future__ import annotations

import unittest
from pathlib import Path

from cryoet_organizer.tabs.tomograms import TomogramsTab


ROOT = Path(__file__).resolve().parents[1]


class CommandParameterFormattingTests(unittest.TestCase):
    def test_tomogram_job_parameter_values_are_not_shell_quoted(self) -> None:
        tab = TomogramsTab.__new__(TomogramsTab)

        self.assertEqual(tab._command_value("0 1"), "0 1")
        self.assertEqual(tab._command_value("*.mrc"), "*.mrc")

    def test_job_parameter_builders_do_not_use_automatic_shell_quoting(self) -> None:
        job_builder_files = [
            ROOT / "cryoet_organizer" / "tabs" / "processing.py",
            ROOT / "cryoet_organizer" / "tabs" / "particles.py",
            ROOT / "cryoet_organizer" / "tabs" / "processing_m.py",
            ROOT / "cryoet_organizer" / "tabs" / "custom.py",
        ]

        for path in job_builder_files:
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("shlex.quote", source)
                self.assertNotIn("shlex.join", source)


if __name__ == "__main__":
    unittest.main()
