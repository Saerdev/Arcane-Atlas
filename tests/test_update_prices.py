import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_prices as market


class FakeMarketClient:
    request_count = 3

    def get(self, route):
        if route == "/items":
            return [
                {"slug": "molt_augmented", "tags": ["arcane_enhancement"], "maxRank": 5,
                 "i18n": {"en": {"name": "Molt Augmented", "thumb": "items/images/molt.png"}}},
                {"slug": "arcane_avenger", "tags": ["arcane_enhancement"], "maxRank": 5,
                 "i18n": {"en": {"name": "Arcane Avenger"}}},
                {"slug": "mod", "tags": ["arcane_enhancement", "mod"]},
                {"slug": "unrelated", "tags": ["prime"]},
            ]
        if route.endswith("molt_augmented"):
            return [
                {"type": "sell", "platinum": 30, "rank": 0, "user": {"status": "online"}},
                {"type": "sell", "platinum": 25, "rank": 0, "user": {"status": "offline"}},
                {"type": "buy", "platinum": 22, "rank": 0, "user": {"status": "ingame"}},
                {"type": "sell", "platinum": 240, "rank": 5, "user": {"status": "online"}},
                {"type": "buy", "platinum": 200, "rank": 5, "user": {"status": "online"}},
            ]
        if route.endswith("arcane_avenger"):
            return []
        raise AssertionError(route)


class FakeBlockedClient(FakeMarketClient):
    def get(self, route):
        if route == "/items":
            return [{"slug": f"test_{i}", "tags": ["arcane_enhancement"]} for i in range(9)]
        raise market.MarketError("Warframe.market HTTP 403", 403)


class TestArcanePriceUpdater(unittest.TestCase):
    def test_catalog_filtered_and_safe_icon(self):
        result = market.filter_arcanes(FakeMarketClient().get("/items"))
        self.assertEqual([entry["slug"] for entry in result], ["arcane_avenger", "molt_augmented"])
        self.assertEqual(result[1]["icon"], "https://warframe.market/static/assets/items/images/molt.png")
        bad = market.filter_arcanes([{"slug": "test", "tags": ["arcane_enhancement"], "i18n": {"en": {"icon": "items/images/../../other.png"}}}])
        self.assertIsNone(bad[0]["icon"])

    def test_rank_and_online_status(self):
        quotes = market.summarize_orders(FakeMarketClient().get("/orders/item/molt_augmented"), 5)
        self.assertEqual(quotes["rank0"]["online"]["ask"], 30)
        self.assertEqual(quotes["rank0"]["recent"]["ask"], 25)
        self.assertEqual(quotes["rank0"]["online"]["bid"], 22)
        self.assertEqual(quotes["rankMax"]["online"]["ask"], 240)
        self.assertEqual(quotes["rankMax"]["online"]["bid"], 200)
        self.assertEqual(quotes["rank0"]["recent"]["sellers"], 2)

    def test_bulk_normalization_and_invalid_orders(self):
        prices = market.summarize_orders([
            {"type": "sell", "platinum": 60, "perTrade": 3, "rank": 0, "user": {"status": "online"}},
            {"type": "sell", "platinum": 25, "rank": 0, "user": {"status": "online"}},
            {"type": "sell", "platinum": -1, "rank": 0},
            {"type": "buy", "platinum": 10, "perTrade": 0, "rank": 0},
            {"type": "sell", "platinum": 1, "rank": 0, "visible": False},
            {"type": "buy", "platinum": 12, "rank": 0, "user": {"status": "ingame"}},
        ], 5)
        self.assertEqual(prices["rank0"]["online"]["ask"], 20)
        self.assertEqual(prices["rank0"]["online"]["medianAsk"], 22.5)
        self.assertEqual(prices["rank0"]["online"]["bid"], 12)
        self.assertEqual(prices["rankMax"]["recent"]["ask"], None)

    def test_acquisition_index_and_wiki_links(self):
        index = market.build_acquisition_index([
            {"name": "Arcane Avenger", "wikiaUrl": "https://wiki.warframe.com/w/Arcane_Avenger", "drops": [
                {"location": "Eidolon Hydrolyst", "chance": 11.7},
                {"location": "Eidolon Hydrolyst (Capture)", "chance": 11.24},
                {"location": "Eidolon Hydrolyst", "chance": 11.7},
                {"location": "", "chance": 50},
                {"location": "Unreliable source", "chance": -1},
            ]},
            {"name": "Molt Augmented", "wikiaUrl": "javascript:alert(1)", "drops": []},
        ])
        data = market.collect(FakeMarketClient(), acquisition_index=index,
                              clock=lambda: 1792600000.0, log=lambda *a, **k: None)
        avenger = data["items"][0]
        self.assertEqual(avenger["wikiUrl"], "https://wiki.warframe.com/w/Arcane_Avenger")
        self.assertEqual(avenger["acquisitionStatus"], "documented")
        self.assertEqual(len(avenger["acquisitionSources"]), 3)
        self.assertEqual(avenger["acquisitionSources"][0]["location"], "Eidolon Hydrolyst")
        self.assertEqual(avenger["acquisitionSources"][0]["chance"], 11.7)
        self.assertIsNone(avenger["acquisitionSources"][-1]["chance"])
        molt = data["items"][1]
        self.assertEqual(molt["wikiUrl"], "https://wiki.warframe.com/w/Molt_Augmented")
        self.assertEqual(molt["acquisitionSources"], [])

    def test_effects_include_rank_zero_and_max_rank(self):
        index = market.build_acquisition_index([{
            "name": "Arcane Avenger",
            "levelStats": [
                {"stats": ["On Damaged:\n21% chance for +7.5% Critical Chance for 12s"]},
                {"stats": ["On Damaged:\n21% chance for +45% Critical Chance for 12s\n+1 Arcane Revive", "+1 Arcane Revive"]},
            ],
            "drops": [],
        }])
        entries = market.filter_arcanes(FakeMarketClient().get("/items"))
        market.attach_acquisition(entries, index)
        effect = entries[0]["effects"]
        self.assertEqual(effect["rank0"], "On Damaged:\n21% chance for +7.5% Critical Chance for 12s")
        self.assertEqual(effect["rankMax"], "On Damaged:\n21% chance for +45% Critical Chance for 12s\n+1 Arcane Revive")
        self.assertEqual(effect["maxRank"], 1)
        self.assertEqual(effect["rankMax"].count("+1 Arcane Revive"), 1)

    def test_effect_fallback_and_invalid_stats(self):
        self.assertEqual(market.arcane_effects({"levelStats": [None, {"stats": [0, False, None]}]}),
                         {"rank0": None, "rankMax": None, "maxRank": None})
        self.assertEqual(market.arcane_effects({"description": "  Restores shields.  "}),
                         {"rank0": "Restores shields.", "rankMax": "Restores shields.", "maxRank": None})
        self.assertEqual(market.arcane_effects({"levelStats": [
            {"stats": ["Shield: +10%"]}, {"stats": ["Shield: +20%"]}]}),
            {"rank0": "Shield: +10%", "rankMax": "Shield: +20%", "maxRank": 1})

    def test_unknown_arcane_has_no_made_up_sources(self):
        entries = market.filter_arcanes(FakeMarketClient().get("/items"))
        market.attach_acquisition(entries, {})
        self.assertTrue(all(entry["acquisitionStatus"] == "unavailable" for entry in entries))
        self.assertTrue(all(entry["acquisitionSources"] == [] for entry in entries))
        self.assertTrue(all(entry["wikiUrl"].startswith("https://wiki.warframe.com/w/") for entry in entries))
        self.assertTrue(all(entry["effects"]["rankMax"] is None for entry in entries))

    def test_normalized_acquisition_names(self):
        self.assertEqual(market.normalized_name("  Molt--Augmented "), market.normalized_name("MOLT AUGMENTED"))
        index = market.build_acquisition_index([{"name": "Molt-AUgmented", "drops": [{"location": "Zariman", "chance": 10}]}])
        entries = market.filter_arcanes(FakeMarketClient().get("/items"))
        market.attach_acquisition(entries, index)
        self.assertEqual(entries[1]["acquisitionSources"][0]["location"], "Zariman")

    def test_snapshot_and_atomic_writer(self):
        data = market.collect(FakeMarketClient(), clock=lambda: 1792600000.0, log=lambda *a, **k: None)
        self.assertEqual(data["status"], "complete")
        self.assertEqual(data["processed"], 2)
        self.assertEqual(data["failed"], 0)
        self.assertEqual(data["market"], {"platform": "pc", "crossplay": True})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "site" / "data" / "prices.json"
            market.write_snapshot(data, output)
            saved = json.loads(output.read_text())
            self.assertEqual(saved["items"][1]["name"], "Molt Augmented")
            self.assertEqual(saved["prices"][1]["quotes"]["rankMax"]["recent"]["ask"], 240)

    def test_broken_api_fails_instead_of_faking_prices(self):
        with self.assertRaises(market.MarketError):
            market.collect(FakeBlockedClient(), log=lambda *a, **k: None)


if __name__ == "__main__":
    unittest.main()
