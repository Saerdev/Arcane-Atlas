import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_prices as market


class FakeClient:
    request_count = 3

    def get(self, route):
        if route != "/items":
            raise AssertionError("Live order endpoint must never be called: " + route)
        return [
            {"slug": "arcane_avenger", "tags": ["arcane_enhancement"],
             "maxRank": 5, "i18n": {"en": {"name": "Arcane Avenger"}}},
            {"slug": "molt_augmented", "tags": ["arcane_enhancement"],
             "maxRank": 5, "i18n": {"en": {"name": "Molt Augmented"}}},
            {"slug": "not_arcane", "tags": []},
        ]

    def statistics(self, slug):
        return [
            {"mod_rank": 0, "datetime": "2026-10-21T01:00:00Z",
             "volume": 2, "avg_price": 10, "median": 11},
            {"mod_rank": 0, "datetime": "2026-10-21T02:00:00Z",
             "volume": 6, "avg_price": 20, "median": 22},
            {"mod_rank": 5, "datetime": "2026-10-21T03:00:00Z",
             "volume": 3, "avg_price": 100, "median": 110},
        ]


class TestClosedOrderStatistics(unittest.TestCase):
    def test_volume_weighted_price_and_rank_separation(self):
        result = market.summarize_closed_statistics(FakeClient().statistics("test"), 5)
        self.assertEqual(result["rank0"]["avg48h"], 17.5)
        self.assertEqual(result["rank0"]["latestHourly"], 22)
        self.assertEqual(result["rank0"]["volume48h"], 8)
        self.assertEqual(result["rankMax"]["avg48h"], 100)
        self.assertEqual(result["rankMax"]["volume48h"], 3)
        self.assertEqual(result["volume48hAll"], 11)

    def test_zero_volume_no_fabricated_price(self):
        result = market.summarize_closed_statistics([
            {"mod_rank": 0, "datetime": "2026-10-21T01:00:00Z",
             "volume": 1, "avg_price": 40, "median": 41},
            {"mod_rank": 0, "datetime": "2026-10-21T02:00:00Z",
             "volume": 0, "avg_price": 1, "median": 1},
        ], 5)
        self.assertEqual(result["rank0"]["latestHourly"], 41)
        self.assertEqual(result["rank0"]["latestHourAt"], "2026-10-21T01:00:00Z")
        self.assertEqual(result["rank0"]["avg48h"], 40)
        self.assertIsNone(result["rankMax"]["avg48h"])

    def test_invalid_stats_and_missing_prices(self):
        result = market.summarize_closed_statistics([
            {"mod_rank": 0, "volume": 2, "avg_price": -1, "median": -4},
            {"mod_rank": 0, "volume": -3, "avg_price": 999},
            {"mod_rank": True, "volume": 3, "avg_price": 999},
        ], 0)
        self.assertIsNone(result["rank0"]["avg48h"])
        self.assertEqual(result["rank0"]["volume48h"], 2)
        self.assertIsNone(result["rankMax"])

    def test_complete_snapshot_uses_only_statistics_not_order_routes(self):
        snapshot = market.collect(FakeClient(), clock=lambda: 1792600000.0, log=lambda *a, **k: None)
        self.assertEqual(snapshot["schemaVersion"], 2)
        self.assertEqual(snapshot["processed"], 2)
        self.assertEqual(snapshot["failed"], 0)
        self.assertEqual(snapshot["prices"][0]["stats"]["rankMax"]["latestHourly"], 110)
        self.assertNotIn("quotes", snapshot["prices"][0])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prices.json"
            market.write_snapshot(snapshot, path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["prices"][1]["stats"]["volume48hAll"], 11)

    def test_statistics_failure_prevents_publishing(self):
        class Blocked(FakeClient):
            def statistics(self, slug):
                raise market.MarketError("Forbidden", 403)
        with self.assertRaises(market.MarketError):
            market.collect(Blocked(), log=lambda *a, **k: None)

    def test_catalog_metadata_and_acquisition_remain(self):
        items = market.filter_arcanes(FakeClient().get("/items"))
        self.assertEqual(len(items), 2)
        market.attach_acquisition(items, {})
        self.assertTrue(all(item["wikiUrl"].startswith("https://wiki.warframe.com/w/") for item in items))


if __name__ == "__main__":
    unittest.main()
