"""Curated direct-vendor offers for arcanes (including vendor/drop overlaps).

Values are per *unranked* arcane, not a fully ranked set. Game offerings can
change; keep this list separate from the published WFCD drop statistics.
The occasional event offers are explicitly marked as event-only.
"""

from __future__ import annotations

# Vendor, faction, local faction-sigil icon, rank number, rank title,
# standing price for one rank-0 arcane, and the arcane names.
_GROUPS = (
    ("Hok", "Ostrons", "ostron.png", 5, "Kin", 10000,
     "Exodia Brave|Exodia Force|Exodia Hunt|Exodia Might|Exodia Triumph|Exodia Valor"),
    ("Rude Zuud", "Solaris United", "solaris.png", 5, "Old Mate", 10000,
     "Pax Bolt|Pax Charge|Pax Seeker|Pax Soar"),
    ("Onkko", "The Quills", "quills.png", 1, "Mote", 2500,
     "Magus Vigor|Virtuos Null"),
    ("Onkko", "The Quills", "quills.png", 2, "Observer", 5000,
     "Magus Husk|Virtuos Tempo"),
    ("Onkko", "The Quills", "quills.png", 3, "Adherent", 7500,
     "Virtuos Fury"),
    ("Onkko", "The Quills", "quills.png", 4, "Instrument", 10000,
     "Magus Cadence|Magus Cloud|Magus Replenish|Virtuos Strike"),
    ("Onkko", "The Quills", "quills.png", 5, "Architect", 10000,
     "Magus Elevate|Magus Nourish|Virtuos Ghost|Virtuos Shadow"),
    ("Little Duck", "Vox Solaris", "vox.png", 2, "Agent", 5000,
     "Virtuos Spike|Virtuos Surge"),
    ("Little Duck", "Vox Solaris", "vox.png", 3, "Hand", 7500,
     "Virtuos Forge|Virtuos Trojan"),
    ("Little Duck", "Vox Solaris", "vox.png", 4, "Instrument", 10000,
     "Magus Anomaly|Magus Destruct|Magus Drive|Magus Firewall|Magus Lockdown"),
    ("Little Duck", "Vox Solaris", "vox.png", 5, "Shadow", 10000,
     "Magus Accelerant|Magus Glitch|Magus Melt|Magus Overload|Magus Repair|Magus Revert"),
)

OFFERS: dict[str, list[dict]] = {}
for vendor, faction, icon, rank, rank_name, standing, names in _GROUPS:
    for name in names.split("|"):
        assert name.casefold() not in OFFERS, name
        OFFERS[name.casefold()] = [{
            "vendor": vendor, "faction": faction, "icon": icon,
            "rank": rank, "rankName": rank_name,
            "cost": standing, "currency": "standing",
        }]

# These purchases are *not* always available; don't display them as ordinary
# standing vendors. Historical event offerings are documented separately.
for name in ("Exodia Contagion", "Exodia Epidemic"):
    OFFERS[name.casefold()] = [
        {"vendor": "Nakak", "faction": "Operational Supply", "rank": 2,
         "rankName": "Defender", "cost": 5000, "currency": "standing",
         "extraCost": "1,500 Credits", "event": "Operation: Plague Star"},
        {"vendor": "Daughter", "faction": "Entrati", "cost": 50,
         "currency": "Mother Tokens", "event": "Nights of Naberus"},
    ]

# Kaya's weekly stock: one copy per arcane per week, sold for Pix Chips.
# Not Standing, and not a random drop. Access requires Rank 5 with The Hex.
for name in ("Arcane Hot Shot", "Arcane Universal Fallout", "Arcane Escapist"):
    OFFERS[name.casefold()] = [{
        "vendor": "Kaya Velasco",
        "faction": "The Hex",
        "icon": "pix-chip.png",
        "rank": 5,
        "rankName": "Pizza Party",
        "cost": 5,
        "currency": "Pix Chips",
        "weeklyLimit": 1,
        "location": "Höllvania Central Mall",
    }]


def vendor_sources(name: str) -> list[dict]:
    """Copy records so snapshot callers cannot mutate the shared catalog."""
    return [source.copy() for source in OFFERS.get(name.casefold(), ())]


def export_site_catalog(path=None) -> None:
    """Update the standalone catalog used by browsers with older price snapshots."""
    import json
    from pathlib import Path

    if path is None:
        path = Path(__file__).resolve().parents[1] / "site" / "data" / "vendor-offers.json"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schemaVersion": 1, "offers": OFFERS}, ensure_ascii=False,
                   indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    export_site_catalog()
