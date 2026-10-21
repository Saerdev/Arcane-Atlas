#!/usr/bin/env python3
"""Generate a published arcane pricing and acquisition snapshot."""

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
import unicodedata
import re
import urllib.error
import urllib.parse
import urllib.request

from vendor_offers import vendor_sources

API_ROOT = "https://api.warframe.market/v2"
STATISTICS_ROOT = "https://api.warframe.market/v1"
VOLUME_CACHE = Path(__file__).resolve().parents[1] / ".cache" / "volume.json"
VOLUME_MAX_AGE = 25 * 60 * 60
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "site" / "data" / "prices.json"
USER_AGENT = os.getenv("WFM_USER_AGENT", "ArcaneAtlas/2.1 (community arcane price dashboard)")
ACQUISITION_URL = "https://raw.githubusercontent.com/WFCD/warframe-items/master/data/json/Arcanes.json"
WIKI_BASE = "https://wiki.warframe.com/w/"



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

    def _request(self, url: str) -> dict:
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
                    try:
                        payload = json.load(response)
                    except (json.JSONDecodeError, UnicodeDecodeError) as error:
                        raise MarketError(f"Invalid JSON from marketplace at {url}") from error
                if not isinstance(payload, dict) or payload.get("error"):
                    raise MarketError(f"Invalid marketplace response at {url}")
                return payload
            except urllib.error.HTTPError as error:
                code = error.code
                if code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise MarketError(f"Warframe.market HTTP {code} at {url}", code) from error
                retry_after = error.headers.get("Retry-After", "")
                try:
                    pause = min(45, max(0, float(retry_after)))
                except ValueError:
                    pause = 0
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt == 3:
                    raise MarketError(f"Market connection failed at {url}: {error}") from error
                pause = 0
            time.sleep(max(pause, 1.5 * 2**attempt))
        raise MarketError(f"API retries exhausted at {url}")

    def get(self, route: str):
        payload = self._request(self.api_base + route)
        if not isinstance(payload.get("data"), list):
            raise MarketError(f"No item/order list in marketplace response at {route}")
        return payload["data"]

    def statistics(self, slug: str):
        base = STATISTICS_ROOT if self.api_base == API_ROOT else self.api_base.removesuffix("/v2") + "/v1"
        route = f"/items/{urllib.parse.quote(slug, safe='')}/statistics"
        payload = self._request(base + route)
        closed = (payload.get("payload") or {}).get("statistics_closed")
        hours = closed.get("48hours") if isinstance(closed, dict) else None
        if not isinstance(hours, list):
            raise MarketError(f"No 48-hour closed-order statistics for {slug}")
        return hours


def filter_arcanes(items: list[dict]) -> list[dict]:
    """Select tradable arcane listings from the item catalog."""
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



def normalized_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).casefold()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def wiki_link(name: str, candidate: str | None = None) -> str:
    if isinstance(candidate, str):
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.scheme == "https" and parsed.netloc == "wiki.warframe.com" and parsed.path.startswith("/w/"):
            return candidate
    return WIKI_BASE + urllib.parse.quote(name.replace(" ", "_"), safe="()")


def arcane_effects(record: dict) -> dict:
    ranks = record.get("levelStats")
    descriptions = []
    if isinstance(ranks, list):
        for rank in ranks:
            lines = []
            seen = set()
            stats = rank.get("stats") if isinstance(rank, dict) else None
            if isinstance(stats, str):
                stats = [stats]
            if isinstance(stats, list):
                for stat in stats:
                    if not isinstance(stat, str):
                        continue
                    for line in stat.replace("\r", "\n").split("\n"):
                        line = re.sub(r"\s+", " ", line).strip()
                        if line and line not in seen:
                            seen.add(line)
                            lines.append(line)
            descriptions.append("\n".join(lines)[:2500] if lines else None)
    if descriptions and any(descriptions):
        return {
            "rank0": descriptions[0],
            "rankMax": descriptions[-1],
            "maxRank": len(descriptions) - 1,
        }
    fallback = record.get("description")
    fallback = fallback.strip()[:2500] if isinstance(fallback, str) and fallback.strip() else None
    return {"rank0": fallback, "rankMax": fallback, "maxRank": None}


def build_acquisition_index(records: list[dict]) -> dict[str, dict]:
    index = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("name"), str):
            continue
        sources = []
        seen = set()
        for drop in record.get("drops") or []:
            if not isinstance(drop, dict):
                continue
            location = drop.get("location")
            if not isinstance(location, str) or not location.strip():
                continue
            location = location.strip()[:180]
            # WFCD includes Plague Star vendor purchases as 100% "drops".
            # Those are limited-time shop offers, not combat rewards.
            if location.casefold().startswith("operational supply"):
                continue
            chance = drop.get("chance")
            if isinstance(chance, bool) or not isinstance(chance, (int, float)) or not math.isfinite(chance) or not 0 <= chance <= 100:
                chance = None
            else:
                chance = round(float(chance), 3)
            key = (location, chance)
            if key not in seen:
                sources.append({"location": location, "chance": chance})
                seen.add(key)
        index[normalized_name(record["name"])] = {
            "sources": sorted(sources, key=lambda source: (source["chance"] is None, -(source["chance"] or 0), source["location"]))[:24],
            "wikiUrl": wiki_link(record["name"], record.get("wikiaUrl")),
            "effects": arcane_effects(record),
        }
    return index


def fetch_acquisition_index(url: str = ACQUISITION_URL) -> dict[str, dict]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=25) as response:
        records = json.load(response)
    if not isinstance(records, list) or not records:
        raise ValueError("Arcane acquisition catalog is empty or invalid")
    index = build_acquisition_index(records)
    if not index:
        raise ValueError("Arcane acquisition catalog has no usable records")
    return index


def attach_acquisition(items: list[dict], index: dict[str, dict]) -> None:
    for item in items:
        details = index.get(normalized_name(item["name"]))
        item["wikiUrl"] = details["wikiUrl"] if details else wiki_link(item["name"])
        item["acquisitionSources"] = details["sources"] if details else []
        item["vendorSources"] = vendor_sources(item["name"])
        item["acquisitionStatus"] = "documented" if details or item["vendorSources"] else "unavailable"
        item["effects"] = details["effects"] if details else {"rank0": None, "rankMax": None, "maxRank": None}


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


def summarize_sales_volume(hours: list[dict], max_rank: int) -> dict | None:
    if not hours:
        return None
    ranks = {}
    for hour in hours:
        if not isinstance(hour, dict):
            continue
        amount = hour.get("volume")
        rank = hour.get("mod_rank")
        if rank is None and max_rank == 0:
            rank = 0
        if (isinstance(amount, bool) or not isinstance(amount, (int, float)) or
                not math.isfinite(amount) or amount < 0 or not float(amount).is_integer() or
                isinstance(rank, bool) or not isinstance(rank, int) or rank < 0 or rank > max_rank):
            continue
        ranks[rank] = ranks.get(rank, 0) + int(amount)
    if not ranks:
        return None
    return {
        "allRanks": sum(ranks.values()),
        "rank0": ranks.get(0, 0),
        "rankMax": ranks.get(max_rank, 0) if max_rank > 0 else None,
    }


def load_volume_cache(path: Path, slugs: list[str], *, clock=time.time) -> dict | None:
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        fetched = cached["fetchedAt"]
        values = cached["values"]
        if (cached.get("schemaVersion") == 1 and isinstance(fetched, (int, float)) and
                0 <= clock() - fetched < VOLUME_MAX_AGE and
                dt.datetime.fromtimestamp(fetched, dt.timezone.utc).date() ==
                dt.datetime.fromtimestamp(clock(), dt.timezone.utc).date() and
                isinstance(values, dict) and
                all(slug in values for slug in slugs)):
            return cached
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def collect_sales_volumes(client: MarketClient, items: list[dict], cache_path: Path,
                          *, clock=time.time, log=print) -> dict:
    slugs = [item["slug"] for item in items]
    cached = load_volume_cache(cache_path, slugs, clock=clock)
    if cached:
        log(f"Reusing 48h trade-volume snapshot from {dt.datetime.fromtimestamp(cached['fetchedAt'], dt.timezone.utc).isoformat()}.", flush=True)
        return cached

    values = {}
    failures = 0
    consecutive = 0
    for item in items:
        if consecutive >= 5:
            values[item["slug"]] = None
            continue
        try:
            hours = client.statistics(item["slug"])
            values[item["slug"]] = summarize_sales_volume(hours, item["maxRank"])
            consecutive = 0
        except MarketError as error:
            values[item["slug"]] = None
            failures += 1
            consecutive += 1
            if error.status in (401, 403, 404):
                consecutive = 5
            if consecutive == 5:
                log(f"Legacy volume statistics unavailable ({error}); remaining values will be blank.", flush=True)
    saved = {"schemaVersion": 1, "fetchedAt": clock(), "values": values}
    try:
        write_snapshot(saved, cache_path)
    except OSError as error:
        log(f"Could not save volume cache: {error}", flush=True)
    log(f"Volume statistics: {sum(v is not None for v in values.values())}/{len(items)} available; {failures} API errors.", flush=True)
    return saved


def attach_sales_volumes(snapshot: dict, cached: dict | None) -> None:
    values = cached["values"] if cached else {}
    snapshot["volumeUpdatedAt"] = int(cached["fetchedAt"] * 1000) if cached else None
    for price in snapshot["prices"]:
        price["sales48h"] = values.get(price["slug"])


def collect(client: MarketClient, *, acquisition_index=None, clock=time.time, log=print) -> dict:
    started = int(clock() * 1000)
    items = filter_arcanes(client.get("/items"))
    if not items:
        raise MarketError("No arcane items found; refusing to publish an empty board.")
    attach_acquisition(items, acquisition_index or {})
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
        "source": "Warframe.market API v2 orders; legacy v1 closed-order statistics",
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
    parser.add_argument("--volume-cache", type=Path, default=VOLUME_CACHE)
    parser.add_argument("--no-volume", action="store_true")
    args = parser.parse_args(argv)
    try:
        client = MarketClient(args.api_base)
        try:
            acquisition_index = fetch_acquisition_index()
            print(f"Loaded acquisition data for {len(acquisition_index)} arcanes", flush=True)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"Acquisition metadata unavailable; Wiki links remain accessible: {error}", file=sys.stderr, flush=True)
            acquisition_index = {}
        snapshot = collect(client, acquisition_index=acquisition_index)
        volume_data = None if args.no_volume else collect_sales_volumes(client, snapshot["items"], args.volume_cache)
        attach_sales_volumes(snapshot, volume_data)
        write_snapshot(snapshot, args.output)
        print(f"Wrote {len(snapshot['prices'])} quotes to {args.output} ({client.request_count} API requests)", flush=True)
        return 0
    except (MarketError, OSError, ValueError) as error:
        print(f"Snapshot not published: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
