"""
Prefix Pick-Rate Analyzer
--------------------------
Estimates, for each team's core/mid/support "slot", how often the heroes
that slot actually picked fall inside each prefix title's hero pool. That
percentage is what tells you whether a prefix is worth taking for a given
lineup -- a prefix's bonus only fires on a map where the hero played is in
its pool.

Reuses stats_analysis.py's role-owner detection (same "majority position_est
vote" logic used for the red/green/blue stats) so a player only counts
toward a role here if they already count toward it there -- no separate
role-assignment logic to keep in sync.

WEIGHTING, mirrors what score_pipeline.py does for stats, plus one twist:
  - core    = average of carry's and offlane's pick%   (each is itself
              already "one player's worth" of signal)
  - support = average of soft_support's and hard_support's pick%
  - mid     = the mid player's pick%, unchanged

  When those three slots are later combined into ONE roster-level pick%
  (done in the frontend, see docs/index.html), mid counts DOUBLE core's
  or support's weight. Reasoning: core and support are each an average of
  two real players, which smooths out any one player's strong hero-pool
  lean; mid is a single, undiluted player, so its number swings harder in
  both directions and would otherwise be underrepresented next to two
  averaged slots. Doubling it puts mid on equal footing rather than
  literally on equal weight.

REPEAT PICKS: pick% is computed over every match appearance, not distinct
heroes -- picking the same hero five times counts five times. That's
intentional (a heavily-repeated hero is a stronger signal than a one-off).

Output:
  - data/final_scores/{core,mid,support}_prefix.csv  (debugging/inspection)
  - merged into docs/data.json as `prefixPickPct` on every row, plus a new
    `meta.prefixes` block (label + bonus per prefix key) -- run this AFTER
    score_pipeline.py's export_site_json() has already written data.json.

Usage:
    python pipeline/prefix_analysis.py <matches_json_file>
"""

import csv
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import stats_analysis
from prefixes import PREFIX_TITLES, HERO_ID_TO_PREFIXES

ROOT_DIR = Path(__file__).resolve().parent.parent
FINAL_DIR = ROOT_DIR / "data" / "final_scores"
SITE_DIR = ROOT_DIR / "docs"
DATA_JSON_PATH = SITE_DIR / "data.json"

PREFIX_KEYS = list(PREFIX_TITLES.keys())

# which two single-position roles average into each site role -- mirrors
# score_pipeline.py's AVERAGE_GROUPS / PASSTHROUGH_FILES, just position
# names instead of filenames.
AVERAGE_GROUPS = {
    "core": ("carry", "offlane"),
    "support": ("soft_support", "hard_support"),
}
PASSTHROUGH = {"mid": "mid"}


# ---------------------------------------------------------------------------
# PART 1: PER-POSITION HERO-PICK POOLING
# ---------------------------------------------------------------------------

def collect_hero_ids_by_position(matches):
    """
    One pass over the matches (via stats_analysis.build_role_owners, so
    role assignment is identical to the red/green/blue stats), returning
    {team: {position: [hero_id, hero_id, ...]}} -- one entry per match
    appearance by whoever holds that position for that team (pooled
    across every player who ever held it, same as stats_analysis does for
    stat means). Appearances with no readable hero_id are skipped.
    """
    per_team_role_owners, appearances, _names, _skipped = stats_analysis.build_role_owners(matches)

    per_team_position_heroes = defaultdict(lambda: defaultdict(list))
    for team_name, role_owners in per_team_role_owners.items():
        for position, owner_ids in role_owners.items():
            for pid in owner_ids:
                for player, _match in appearances[(team_name, pid)]:
                    hero_id = player.get("hero_id")
                    if isinstance(hero_id, int) and hero_id > 0:
                        per_team_position_heroes[team_name][position].append(hero_id)

    return per_team_position_heroes


def pick_pct_for_hero_ids(hero_ids):
    """
    {prefix_key: pct} for one player/slot's list of hero_ids (one entry
    per match appearance, repeats included on purpose -- see module
    docstring). pct is 0-100. Returns None for every prefix if the list is
    empty (no data yet, e.g. role never resolved for this team).
    """
    total = len(hero_ids)
    if total == 0:
        return {key: None for key in PREFIX_KEYS}

    counts = {key: 0 for key in PREFIX_KEYS}
    for hero_id in hero_ids:
        for key in HERO_ID_TO_PREFIXES.get(hero_id, ()):
            counts[key] += 1

    return {key: round(100 * counts[key] / total, 2) for key in PREFIX_KEYS}


def compute_position_pct(matches):
    """{team: {position_name: {prefix_key: pct_or_None}}} for all 5 base positions."""
    per_team_position_heroes = collect_hero_ids_by_position(matches)
    position_names = {v: k for k, v in stats_analysis.ROLE_POSITIONS.items()}

    result = defaultdict(dict)
    for team_name, by_position in per_team_position_heroes.items():
        for position_num, hero_ids in by_position.items():
            position_name = position_names.get(position_num)
            if position_name is None:
                continue
            result[team_name][position_name] = pick_pct_for_hero_ids(hero_ids)
    return result


# ---------------------------------------------------------------------------
# PART 2: AVERAGE INTO core / mid / support
# ---------------------------------------------------------------------------

def _average_pct(a, b):
    """Average two {prefix_key: pct_or_None} dicts per-key, skipping missing sides."""
    out = {}
    for key in PREFIX_KEYS:
        values = [d.get(key) for d in (a, b) if d and d.get(key) is not None]
        out[key] = round(sum(values) / len(values), 2) if values else None
    return out


def compute_role_pct(matches):
    """
    {team: {"core": {...}, "mid": {...}, "support": {...}}}, built from the
    5 base positions -- same averaging groups score_pipeline.py uses for
    core_score.csv/support_score.csv, mid passed through unchanged.
    """
    position_pct = compute_position_pct(matches)
    all_teams = sorted(position_pct.keys())

    role_pct = {}
    for team in all_teams:
        by_position = position_pct[team]
        entry = {}
        for role, (pos_a, pos_b) in AVERAGE_GROUPS.items():
            a = by_position.get(pos_a, {})
            b = by_position.get(pos_b, {})
            if not a and not b:
                continue
            entry[role] = _average_pct(a, b)
        for role, pos in PASSTHROUGH.items():
            if pos in by_position:
                entry[role] = by_position[pos]
        if entry:
            role_pct[team] = entry
    return role_pct


# ---------------------------------------------------------------------------
# PART 3: CSV OUTPUT (debugging/inspection, mirrors data/final_scores/*.csv)
# ---------------------------------------------------------------------------

def save_role_prefix_csv(role_pct, role):
    os.makedirs(FINAL_DIR, exist_ok=True)
    path = FINAL_DIR / f"{role}_prefix.csv"
    fieldnames = ["team"] + PREFIX_KEYS
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for team in sorted(role_pct):
            entry = role_pct[team].get(role)
            if entry is None:
                continue
            row = {"team": team}
            for key in PREFIX_KEYS:
                val = entry.get(key)
                row[key] = "n/a" if val is None else val
            writer.writerow(row)
    print(f"Saved: {path}")


# ---------------------------------------------------------------------------
# PART 4: MERGE INTO docs/data.json
# ---------------------------------------------------------------------------

def inject_into_data_json(role_pct, data_json_path=DATA_JSON_PATH):
    """
    Adds `prefixPickPct` ({prefix_key: pct_or_0.0}) to every row of
    data['core'|'mid'|'support']['rows'], matched by team name, and adds
    `meta.prefixes` ({prefix_key: {label, bonus}}). Requires data.json to
    already exist (run score_pipeline.py's export_site_json() first).
    """
    if not os.path.exists(data_json_path):
        print(f"Missing {data_json_path} -- run score_pipeline.py first (or --site) "
              "before injecting prefix data.")
        return None

    with open(data_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for role in ("core", "mid", "support"):
        rows = data.get(role, {}).get("rows", [])
        for row in rows:
            entry = role_pct.get(row["team"], {}).get(role)
            if entry is None:
                row["prefixPickPct"] = {key: 0.0 for key in PREFIX_KEYS}
                continue
            row["prefixPickPct"] = {
                key: (entry.get(key) if entry.get(key) is not None else 0.0)
                for key in PREFIX_KEYS
            }

    data.setdefault("meta", {})["prefixes"] = {
        key: {"label": title["label"], "bonus": title["bonus"]}
        for key, title in PREFIX_TITLES.items()
    }

    with open(data_json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"Updated with prefix data: {data_json_path}")
    return data_json_path


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def run(matches_path):
    """Callable directly (e.g. from build_dataset.py) as well as via the CLI below."""
    with open(matches_path, "r", encoding="utf-8") as f:
        matches = json.load(f)

    role_pct = compute_role_pct(matches)
    for role in ("core", "mid", "support"):
        save_role_prefix_csv(role_pct, role)

    return inject_into_data_json(role_pct)


def main():
    if len(sys.argv) < 2:
        print("Usage: python pipeline/prefix_analysis.py <matches_json_file>")
        return
    run(sys.argv[1])


if __name__ == "__main__":
    main()
