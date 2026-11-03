"""Construction vocabulary for Muse Voice Transcribe keyword biasing (PLAN 1.1).

`keywords_for(project_keywords)` = the base list below + the project's own keywords (company
names, unit numbers, fixture brands), de-duplicated case-insensitively, base order first.

Only project *keywords* are added, not project rooms: the ASR cache key includes the keyword
list, so a walk recorded with `make record` (base list only) replays offline for any project
whose `keywords` is empty. Common room names are already in the base list.
"""

from __future__ import annotations

from collections.abc import Iterable

TRADES = [
    "electrical", "plumbing", "drywall", "painting", "carpentry", "millwork", "hardware",
    "flooring", "glazing", "HVAC", "appliances", "cleaning",
]

FIXTURES_AND_PARTS = [
    "cover plate", "switch plate", "outlet", "receptacle", "GFCI", "light fixture", "bulb",
    "strike plate", "door stop", "hinge", "latch", "deadbolt", "knob", "pull", "escutcheon",
    "faucet", "aerator", "P-trap", "shutoff valve", "towel bar", "toilet paper holder", "vanity",
    "mirror", "shower head", "grout", "tile", "backsplash", "countertop", "cabinet", "drawer",
    "baseboard", "casing", "crown molding", "trim", "threshold", "transition strip",
    "window screen", "sash", "sill", "register", "grille", "thermostat", "smoke detector",
    "range hood", "dishwasher", "refrigerator",
]

FINISHES_AND_DEFECTS = [
    "drywall", "GWB", "caulk", "touch-up", "reveal", "scuff", "ding", "nail pop", "crack",
    "chip", "gouge", "stain", "smudge", "overspray", "drip", "gap", "misaligned", "crooked",
    "loose", "missing", "debris", "sawdust", "punch list",
]

ROOMS_AND_UNITS = [
    "kitchen", "bathroom", "powder room", "bedroom", "primary bedroom", "hallway", "living room",
    "dining room", "laundry", "closet", "entry", "foyer", "Unit 1A", "Unit 2B", "Unit 3A",
]

BASE_KEYWORDS: list[str] = [*TRADES, *FIXTURES_AND_PARTS, *FINISHES_AND_DEFECTS, *ROOMS_AND_UNITS]


def _dedupe(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        clean = " ".join(str(term).split())
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(clean)
    return out


def keywords_for(project_keywords: Iterable[str] | None = None) -> list[str]:
    """Base construction vocabulary + project keywords, de-duplicated (case-insensitive)."""
    return _dedupe([*BASE_KEYWORDS, *(project_keywords or [])])
