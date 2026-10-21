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
