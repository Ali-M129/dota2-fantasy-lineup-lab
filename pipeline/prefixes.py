"""
Prefix (Title) Reference Data
-------------------------------
Static reference data for Dota 2 TI Fantasy "prefix" titles (e.g. Crimson,
Cerulean, Royal...). Ported from TinyKiecoo/Calculator-for-DOTA2-TI-Fantasy
(fantasy.js's `prefixTitles` for bonus/heroIds, heroids.txt for the
display names), which is the closest thing to an authoritative source for
these numbers since Valve doesn't publish them anywhere structured.

Each prefix title gives a fixed bonus multiplier to a map's fantasy score,
but ONLY if the hero played on that map belongs to the title's hero pool.
So a prefix is only as good as the odds your roster actually picks a hero
from its pool -- that's what pipeline/prefix_analysis.py estimates.

To update this table (e.g. after a balance patch), edit PREFIX_TITLES
below -- nothing else in the pipeline needs to change.
"""

# key -> {label: display name, bonus: fractional bonus (0.06 = +6%), hero_ids: [...]}
PREFIX_TITLES = {
    "crimson": {
        "label": "Crimson",
        "bonus": 0.06,
        "hero_ids": [2, 4, 11, 14, 18, 25, 35, 37, 38, 49, 51, 61, 64, 65, 69, 77, 78, 79, 81, 87, 88, 95, 104, 106, 110, 120, 129, 128, 137, 131],
    },
    "azure": {
        "label": "Cerulean",
        "bonus": 0.11,
        "hero_ids": [5, 9, 10, 12, 13, 15, 17, 18, 20, 22, 31, 39, 48, 52, 59, 60, 63, 64, 68, 71, 84, 91, 92, 102, 111, 112, 113, 138, 145],
    },
    "emerald": {
        "label": "Emerald",
        "bonus": 0.06,
        "hero_ids": [21, 29, 36, 40, 42, 44, 45, 47, 53, 58, 76, 83, 85, 86, 89, 94, 107, 108, 114, 119, 123, 138, 155],
    },
    "purple": {
        "label": "Royal",
        "bonus": 0.10,
        "hero_ids": [1, 3, 6, 26, 28, 30, 32, 33, 41, 46, 50, 55, 67, 70, 75, 98, 102, 109, 119, 126],
    },
    "golden": {
        "label": "Golden",
        "bonus": 0.08,
        "hero_ids": [27, 34, 56, 62, 65, 66, 72, 73, 86, 90, 99, 103, 110, 105, 135, 131, 7, 16, 19, 80, 83, 96, 97, 137, 155],
    },
    "elemental": {
        "label": "Elemental",
        "bonus": 0.08,
        "hero_ids": [10, 23, 28, 29, 89, 93, 25, 49, 56, 59, 64, 65, 69, 74, 78, 84, 106, 110, 105, 135, 5, 6, 31, 68, 100, 112],
    },
    "otherworldly": {
        "label": "Otherworldly",
        "bonus": 0.07,
        "hero_ids": [14, 20, 23, 31, 36, 42, 43, 45, 54, 56, 59, 67, 85, 121, 138, 11, 26, 39, 69, 79, 109, 108, 17, 106, 107, 126],
    },
    "heroic": {
        "label": "Heroic",
        "bonus": 0.09,
        "hero_ids": [4, 5, 6, 21, 26, 35, 37, 44, 45, 53, 57, 65, 74, 79, 86, 102, 111, 113, 114, 136, 138, 8, 18, 27, 34, 51, 62, 72, 81, 121],
    },
}

# Fast lookup: hero_id -> set of prefix keys it belongs to (a hero can be
# in more than one prefix's pool).
HERO_ID_TO_PREFIXES = {}
for _key, _title in PREFIX_TITLES.items():
    for _hero_id in _title["hero_ids"]:
        HERO_ID_TO_PREFIXES.setdefault(_hero_id, set()).add(_key)
