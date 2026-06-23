from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cryoet_organizer.m_population import parse_population_file
from cryoet_organizer.project import MPopulationRecord


class MPopulationTests(unittest.TestCase):
    def test_parse_population_file_collects_species_and_sources(self) -> None:
        xml_text = """<?xml version="1.0" encoding="utf-8"?>
<Population>
  <Param Name="Name" Value="ExamplePopulation" />
  <Species>
    <Species GUID="guid-1" Path="species/ATP_Synthase/ATP_Synthase.species" />
    <Species GUID="guid-2" Path="species/Ribosome/Ribosome.species" />
  </Species>
  <Sources>
    <Source GUID="src-1" Path="../warp_tiltseries/Example.source" />
  </Sources>
</Population>
"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            population_path = Path(tmp_dir) / "ExamplePopulation.population"
            population_path.write_text(xml_text, encoding="utf-8")

            parsed = parse_population_file(population_path)

        self.assertEqual(parsed.name, "ExamplePopulation")
        self.assertEqual(parsed.directory, str(population_path.parent.resolve()))
        self.assertEqual(parsed.population_file, "ExamplePopulation.population")
        self.assertEqual([item.name for item in parsed.species], ["ATP_Synthase", "Ribosome"])
        self.assertEqual([item.name for item in parsed.sources], ["Example"])

    def test_population_record_roundtrip_keeps_imported_metadata(self) -> None:
        record = MPopulationRecord(
            name="ExamplePopulation",
            directory="/tmp/populations",
            population_file="ExamplePopulation.population",
            species=[{"guid": "guid-1", "path": "species/A.species", "name": "A"}],
            sources=[{"guid": "src-1", "path": "../warp_tiltseries/A.source", "name": "A"}],
        )

        restored = MPopulationRecord.from_dict(record.to_dict())

        self.assertEqual(restored.population_file, "ExamplePopulation.population")
        self.assertEqual(restored.species[0]["name"], "A")
        self.assertEqual(restored.sources[0]["path"], "../warp_tiltseries/A.source")


if __name__ == "__main__":
    unittest.main()
