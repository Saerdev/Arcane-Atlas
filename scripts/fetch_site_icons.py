#!/usr/bin/env python3
"""Download Warframe icon artwork for Arcane Atlas's static website.

One-time asset acquisition, not a source-code patcher. From repository root:
    python scripts/fetch_site_icons.py

Then commit site/favicon.png and site/assets/status/*.png.
"""
from __future__ import annotations

import json
import struct
import sys
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
STATUS = SITE / "assets" / "status"
SYMBOLS = (
    "ImpactSymbol.png", "PunctureSymbol.png", "SlashSymbol.png",
    "HeatSymbol.png", "ColdSymbol.png", "ElectricitySymbol.png",
    "ToxinSymbol.png", "BlastSymbol.png", "RadiationSymbol.png",
    "GasSymbol.png", "MagneticSymbol.png", "ViralSymbol.png",
    "CorrosiveSymbol.png", "VoidSymbol.png",
)
FAVICON = (
    "https://raw.githubusercontent.com/Aericio/warframe-exports-data/"
    "master/image/64x64/"
    "Lotus.Upgrades.CosmeticEnhancers.Utility."
    "GolemArcaneRadialEnergyOnEnergyPickup.png"
)
USER_AGENT = "ArcaneAtlas-IconSetup/1.1 (+https://github.com/Saerdev/Arcane-Atlas)"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_png(contents: bytes) -> bool:
    if len(contents) < 33 or contents[:8] != PNG_MAGIC or contents[12:16] != b"IHDR":
        return False
    width, height = struct.unpack(">II", contents[16:24])
    return 0 < width <= 4096 and 0 < height <= 4096 and b"IEND" in contents[-24:]


def request_bytes(url: str, accept: str = "image/png,image/*;q=0.9,*/*;q=0.1") -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urlopen(req, timeout=30) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("Response exceeds 2 MB")
    return data


def wiki_asset_url(filename: str) -> str | None:
    # Fallback: resolve the original filename via MediaWiki instead of
    # guessing hashed image URLs. Direct Wiki hotlinks may be blocked.
    for endpoint in ("https://wiki.warframe.com/api.php", "https://warframe.fandom.com/api.php"):
        params = urlencode({"action": "query", "format": "json", "formatversion": 2,
                            "prop": "imageinfo", "iiprop": "url", "titles": "File:" + filename})
        try:
            response = json.loads(request_bytes(f"{endpoint}?{params}", "application/json"))
            for page in response.get("query", {}).get("pages", []):
                infos = page.get("imageinfo", [])
                if infos and infos[0].get("url", "").startswith("https://"):
                    return infos[0]["url"]
        except Exception:
            pass
    return None


def save_icon(destination: Path, candidates: list[str]) -> bool:
    if destination.exists() and is_png(destination.read_bytes()):
        print(f"Already present: {destination.relative_to(ROOT)}")
        return True
    reasons = []
    for url in candidates:
        try:
            data = request_bytes(url)
            if not is_png(data):
                raise ValueError("not a complete PNG response")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(data)
            temporary.replace(destination)
            print(f"Saved: {destination.relative_to(ROOT)} ({len(data):,} bytes)")
            return True
        except Exception as exc:
            reasons.append(f"{url}: {exc}")
    print(f"FAILED: {destination.name}\n  " + "\n  ".join(reasons), file=sys.stderr)
    return False


def main() -> int:
    failures = []
    for name in SYMBOLS:
        # browse.wf serves the game texture path used by ExportTextIcons.json.
        candidates = ["https://browse.wf/Lotus/Interface/Icons/StatSymbols/" + name]
        if not save_icon(STATUS / name, candidates):
            # A local one-time fetch may work even when third-party browser
            # embedding is refused by the Wiki.
            alternative = wiki_asset_url(name)
            if not alternative or not save_icon(STATUS / name, [alternative]):
                failures.append(name)
        time.sleep(0.15)
    if not save_icon(SITE / "favicon.png", [FAVICON]):
        failures.append("favicon.png")
    if failures:
        print("\nSome files could not be downloaded: " + ", ".join(failures), file=sys.stderr)
        print("Do not deploy until all 15 PNGs exist. Check URLs and network access.", file=sys.stderr)
        return 1
    print("\nAll 15 icons ready; commit site/favicon.png and site/assets/status/*.png.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
