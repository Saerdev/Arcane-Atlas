#!/usr/bin/env python3
"""Build a static, rate-limited Warframe.market arcane price snapshot.

Standard library only. Intended for GitHub Actions, not for visitor browsers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

API_ROOT = "https://api.warframe.market/v2"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "site" / "data" / "prices.json"
USER_AGENT = os.getenv("WFM_USER_AGENT", "ArcaneAtlas/2.0 (community arcane price dashboard)")


class MarketError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class MarketClient:
    def __init__(self, api_base: str = API_ROOT, min_interval: float = 0.55):
        if min_interval < 0.35:
            raise ValueError("Interval must be at least 0.35 seconds to respect the public API rate limit")
        self.api_base = api_base.rstrip("/")
        self.min_interval = min_interval
        self.next_call = 0.0
        self.request_count = 0

    def get(self, route: str):
        url = self.api_base + route
        for attempt in range(4):
            wait = self.next_call - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.next_call = time.monotonic() + self.min_interval
            self.request_count += 1
            request = urllib.request.Request(url, headers={
                "Accept": "application/json", "Language": "en",
                "Platform": "pc", "Crossplay": "true", "User-Agent": USER_AGENT,
            })
            try:
                with urllib.request.urlopen(request, timeout=18) as response:
                    payload = json.load(response)
                if not isinstance(payload, dict) or payload.get("error"):
                    raise MarketError(f"Malformed or failed marketplace response at {route}")
                if not isinstance(payload.get("data"), list):
                    raise MarketError(f"No item/order list in marketplace response at {route}")
                return payload["data"]
            except urllib.error.HTTPError as error:
                code = error.code
                if code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise MarketError(f"Warframe.market HTTP {code} at {route}", code) from error
                retry_after = error.headers.get("Retry-After", "")
                try:
                    pause = min(45, max(0, float(retry_after)))
                except ValueError:
                    pause = 0
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 3:
                    raise MarketError(f"Market connection failed at {route}: {error}") from error
                pause = 0
            time.sleep(max(pause, 1.5 * 2**attempt))
        raise MarketError(f"API retries exhausted at {route}")


def filter_arcanes(items: list[dict]) -> list[dict]:
    """Only tradable catalog items tagged as arcanes; ignore mod entries."""
    found = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str):
            continue
        tags = item.get("tags") or []
        if "arcane_enhancement" not in tags or "mod" in tags:
            continue
        icon = (item.get("i18n") or {}).get("en") or {}
        thumbnail = icon.get("thumb") or icon.get("icon")
        safe_icon = None
        if isinstance(thumbnail, str) and thumbnail.startswith("items/images/"):
            path = thumbnail.split("/")
            if all(part and part not in (".", "..") and all(c.isalnum() or c in "_.-" for c in part) for part in path):
                safe_icon = "https://warframe.market/static/assets/" + thumbnail
        raw_max = item.get("maxRank")
        max_rank = raw_max if isinstance(raw_max, int) and not isinstance(raw_max, bool) and raw_max >= 0 else 0
        found.append({
            "slug": item["slug"],
            "name": icon.get("name") or item["slug"].replace("_", " ").title(),
            "maxRank": max_rank,
            "rarity": item.get("rarity"),
            "icon": safe_icon,
        })
    return sorted(found, key=lambda a: a["name"].casefold())


def _side(orders: list[dict]) -> dict:
    sells = [o["price"] for o in orders if o["type"] == "sell"]
    buys = [o["price"] for o in orders if o["type"] == "buy"]
    return {
        "ask": min(sells) if sells else None,
        "bid": max(buys) if buys else None,
        "medianAsk": round(statistics.median(sells), 2) if sells else None,
        "sellers": len(sells),
        "buyers": len(buys),
    }


def summarize_orders(orders: list[dict], max_rank: int = 0) -> dict:
    normalized = []
    for order in orders:
        if not isinstance(order, dict) or order.get("type") not in ("sell", "buy") or order.get("visible") is False:
            continue
        try:
            platinum = float(order["platinum"])
            units = float(order.get("perTrade") if order.get("perTrade") is not None else 1)
            rank = float(order.get("rank") if order.get("rank") is not None else 0)
            if not all(math.isfinite(v) for v in (platinum, units, rank)) or platinum <= 0 or units <= 0 or not rank.is_integer():
                continue
        except (ValueError, TypeError, KeyError):
            continue
        normalized.append({
            "type": order["type"], "rank": int(rank), "price": round(platinum / units, 4),
            "online": (order.get("user") or {}).get("status") in ("online", "ingame"),
        })

    def rank_quotes(rank: int) -> dict:
        rank_orders = [order for order in normalized if order["rank"] == rank]
        return {
            "online": _side([order for order in rank_orders if order["online"]]),
            "recent": _side(rank_orders),
        }

    return {"rank0": rank_quotes(0), "rankMax": rank_quotes(max_rank) if max_rank > 0 else None}


def collect(client: MarketClient, *, clock=time.time, log=print) -> dict:
    started = int(clock() * 1000)
    items = filter_arcanes(client.get("/items"))
    if not items:
        raise MarketError("No arcane items found; refusing to publish an empty board.")
    prices = []
    errors = 0
    consecutive = 0
    log(f"Scanning {len(items)} arcanes in the PC crossplay market.", flush=True)
    for index, item in enumerate(items, 1):
        try:
            slug = urllib.parse.quote(item["slug"], safe="")
            orders = client.get(f"/orders/item/{slug}")
            prices.append({
                "slug": item["slug"], "quotes": summarize_orders(orders, item["maxRank"]),
                "fetchedAt": int(clock() * 1000), "error": None,
            })
            consecutive = 0
        except MarketError as error:
            errors += 1
            consecutive += 1
            prices.append({"slug": item["slug"], "quotes": None, "fetchedAt": None, "error": str(error)})
            log(f"  Could not read {item['slug']}: {error}", flush=True)
            if error.status in (401, 403) or consecutive >= 6:
                raise MarketError("Marketplace requests are consistently failing. Keeping the last published snapshot.") from error
        if index % 50 == 0 or index == len(items):
            log(f"  {index}/{len(items)} checked; {errors} failed", flush=True)
    if errors / len(items) > 0.10:
        raise MarketError(f"{errors}/{len(items)} arcane requests failed (>10%). Refusing to replace the previous snapshot.")
    completed = int(clock() * 1000)
    return {
        "schemaVersion": 1,
        "source": "Warframe.market public API v2",
        "market": {"platform": "pc", "crossplay": True},
        "generatedAt": dt.datetime.fromtimestamp(completed / 1000, tz=dt.timezone.utc).isoformat(),
        "startedAt": started, "completedAt": completed,
        "items": items, "prices": prices, "processed": len(items), "failed": errors,
        "status": "complete" if errors == 0 else "partial",
    }


def write_snapshot(snapshot: dict, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json", prefix=".snapshot-", dir=output.parent, delete=False) as handle:
        temp_name = handle.name
        try:
            json.dump(snapshot, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            handle.write("\n")
        except BaseException:
            os.unlink(temp_name)
            raise
    os.replace(temp_name, output)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Publish one rate-limited arcane market snapshot")
    parser.add_argument("--api-base", default=os.getenv("WFM_API_BASE", API_ROOT))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        client = MarketClient(args.api_base)
        snapshot = collect(client)
        write_snapshot(snapshot, args.output)
        print(f"Wrote {len(snapshot['prices'])} quotes to {args.output} ({client.request_count} API requests)", flush=True)
        return 0
    except (MarketError, OSError, ValueError) as error:
        print(f"Snapshot not published: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
