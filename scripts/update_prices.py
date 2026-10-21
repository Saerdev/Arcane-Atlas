#!/usr/bin/env python3
"""Generate a published arcane pricing and acquisition snapshot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
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


def _price(value) -> float | None:
    """Return a usable reported price, never a made-up zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def summarize_closed_statistics(hours: list[dict], max_rank: int) -> dict:
    """Summarize reported closed-order activity, separated by arcane rank.

    The API supplies hourly aggregated statistics, not individual sales.
    Weight each hourly average by its reported closed-order volume rather
    than averaging hourly averages equally. No value is inferred for hours
    with zero volume. The latest price is from the most recent *traded* hour.
    """
    ranks = {0: []}
    if max_rank > 0:
        ranks[max_rank] = []
    total_volume = 0

    for hour in hours:
        if not isinstance(hour, dict):
            continue
        rank = hour.get("mod_rank")
        if rank is None and max_rank == 0:
            rank = 0
        amount = hour.get("volume")
        if (isinstance(rank, bool) or not isinstance(rank, int) or rank < 0 or
                rank > max_rank or isinstance(amount, bool) or
                not isinstance(amount, (int, float)) or not math.isfinite(amount) or
                amount < 0 or not float(amount).is_integer()):
            continue
        volume = int(amount)
        total_volume += volume
        if rank in ranks:
            ranks[rank].append((hour, volume))

    def summary(entries: list[tuple[dict, int]]) -> dict:
        volume = sum(count for _, count in entries)
        weighted_price = 0.0
        priced_volume = 0
        recent = None
        for hour, count in entries:
            if count == 0:
                continue
            average = _price(hour.get("wa_price"))
            if average is None:
                average = _price(hour.get("avg_price"))
            if average is not None:
                weighted_price += average * count
                priced_volume += count
            # Preserve the hourly timestamp: no trading means no new price.
            timestamp = hour.get("datetime")
            if not isinstance(timestamp, str) or not timestamp:
                continue
            last_price = _price(hour.get("median"))
            if last_price is None:
                last_price = average
            if last_price is not None and (recent is None or timestamp > recent[0]):
                recent = (timestamp, last_price)
        return {
            "avg48h": round(weighted_price / priced_volume, 2) if priced_volume else None,
            "latestHourly": round(recent[1], 2) if recent else None,
            "latestHourAt": recent[0] if recent else None,
            "volume48h": volume,
        }

    return {
        "rank0": summary(ranks[0]),
        "rankMax": summary(ranks[max_rank]) if max_rank > 0 else None,
        "volume48hAll": total_volume,
    }


def collect(client: MarketClient, *, acquisition_index=None, clock=time.time, log=print) -> dict:
    started = int(clock() * 1000)
    items = filter_arcanes(client.get("/items"))
    if not items:
        raise MarketError("No arcane items found; refusing to publish an empty board.")
    attach_acquisition(items, acquisition_index or {})
    prices = []
    errors = 0
    consecutive = 0
    log(f"Scanning 48-hour closed-order statistics for {len(items)} PC crossplay arcanes.", flush=True)
    for index, item in enumerate(items, 1):
        try:
            hours = client.statistics(item["slug"])
            prices.append({
                "slug": item["slug"],
                "stats": summarize_closed_statistics(hours, item["maxRank"]),
                "fetchedAt": int(clock() * 1000), "error": None,
            })
            consecutive = 0
        except MarketError as error:
            errors += 1
            consecutive += 1
            prices.append({"slug": item["slug"], "stats": None,
                           "fetchedAt": None, "error": str(error)})
            log(f"  Could not read statistics for {item['slug']}: {error}", flush=True)
            if error.status in (401, 403) or consecutive >= 6:
                raise MarketError(
                    "Marketplace statistics requests are consistently failing. "
                    "Keeping the last published snapshot."
                ) from error
        if index % 50 == 0 or index == len(items):
            log(f"  {index}/{len(items)} checked; {errors} failed", flush=True)
    if errors / len(items) > 0.10:
        raise MarketError(
            f"{errors}/{len(items)} arcane statistics requests failed (>10%). "
            "Refusing to replace the previous snapshot."
        )
    completed = int(clock() * 1000)
    return {
        "schemaVersion": 2,
        "source": "Warframe.market API v1 hourly closed-order statistics (48hours)",
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
        try:
            acquisition_index = fetch_acquisition_index()
            print(f"Loaded acquisition data for {len(acquisition_index)} arcanes", flush=True)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f"Acquisition metadata unavailable; Wiki links remain accessible: {error}", file=sys.stderr, flush=True)
            acquisition_index = {}
        snapshot = collect(client, acquisition_index=acquisition_index)
        write_snapshot(snapshot, args.output)
        print(f"Wrote {len(snapshot['prices'])} closed-order summaries to {args.output} ({client.request_count} API requests)", flush=True)
        return 0
    except (MarketError, OSError, ValueError) as error:
        print(f"Snapshot not published: {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
