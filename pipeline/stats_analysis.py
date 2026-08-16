"""
Role Stats Analyzer
--------------------
Loads TI match data and computes, per team, the mean and variance of
stats for a given ROLE and CATEGORY.

Usage:
    python stats_analysis.py <matches_json_file> [role] [category]
    role:     carry | mid | offlane | soft_support | hard_support (default: carry)
    category: red | blue | green (asked interactively if omitted)

Output file: <category>_stats_for_<role>s.csv

Role assignment: a player's real role is the position_est value they
had most often across all their matches for that team (majority vote,
not per-match) -- avoids one-off farm swaps skewing the role.

Stat categories:
    RED   -- scoreboard numbers (last hits, kills, gpm, deaths, etc.)
    BLUE  -- utility/vision/farm support (wards, smokes, runes, etc.)
    GREEN -- objectives/impact plays (teamfights, roshan, stuns, etc.)

To add a stat: add one line to the relevant category dict below.
"""

import sys
import os
import json
import csv
import statistics
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# OUTPUT LOCATION
# ---------------------------------------------------------------------------
# Resolved relative to the repo root so this works no matter the cwd.
ROOT_DIR = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT_DIR / "data" / "raw_data"  # summary CSV is saved here


# ---------------------------------------------------------------------------
# ROLE DEFINITIONS
# ---------------------------------------------------------------------------
ROLE_POSITIONS = {
    "carry": 1,
    "mid": 2,
    "offlane": 3,
    "soft_support": 4,
    "hard_support": 5,
}


# ---------------------------------------------------------------------------
# STAT EXTRACTOR HELPERS
# ---------------------------------------------------------------------------

def _direct_field(field_name):
    """Factory: build an extractor for a plain top-level numeric field."""
    def extractor(player, match):
        return player.get(field_name)
    return extractor


def _sum_fields(*field_names):
    """Factory: build an extractor that sums several plain top-level numeric
    fields (e.g. last_hits + denies). Returns None if any field is missing,
    so the row is dropped instead of silently treated as 0."""
    def extractor(player, match):
        values = [player.get(f) for f in field_names]
        if any(v is None for v in values):
            return None
        return sum(values)
    return extractor


def _item_use_count(item_key):
    """Factory: build an extractor for a count nested in item_uses."""
    def extractor(player, match):
        return (player.get("item_uses") or {}).get(item_key, 0)
    return extractor


def _ability_use_count(ability_key):
    """Factory: build an extractor for a count nested in ability_uses."""
    def extractor(player, match):
        return (player.get("ability_uses") or {}).get(ability_key, 0)
    return extractor


def _tormentor_participate(player, match):
    """
    Approximates per-player Tormentor participation (OpenDota has no
    per-kill log). Team's total Tormentor last-hits this match = team_kills.
    Any teammate who took damage from it or got a last-hit is credited
    team_kills points. Restricted to teammates only, so a nearby enemy
    taking reflect damage isn't credited.
    """
    teammates = [p for p in match.get("players", []) if p.get("isRadiant") == player.get("isRadiant")]
    team_kills = sum((p.get("killed") or {}).get("npc_dota_miniboss", 0) for p in teammates)
    if team_kills == 0:
        return 0

    took_damage = (player.get("damage_taken") or {}).get("npc_dota_miniboss", 0) > 0
    got_kill = (player.get("killed") or {}).get("npc_dota_miniboss", 0) > 0
    return team_kills if (took_damage or got_kill) else 0


# ---------------------------------------------------------------------------
# STAT CATEGORIES
# ---------------------------------------------------------------------------
# Each category: stat_name -> function(player_dict) -> value or None.
# To add a stat, add one line to the right category -- nothing else
# in the script needs to change.

STAT_CATEGORIES = {
    "red": {
        "last_hits": _sum_fields("last_hits", "denies"),
        "kills": _direct_field("kills"),
        "gpm": _direct_field("gold_per_min"),
        "deaths": _direct_field("deaths"),
        "tower_kills": _direct_field("tower_kills"),
        "madstone_collected": _item_use_count("madstone_bundle"),
    },
    "blue": {
        "observer_wards_planted": _direct_field("obs_placed"),
        "smoke_used": _item_use_count("smoke_of_deceit"),
        "rune_grabbed": _direct_field("rune_pickups"),
        "camps_stacked": _direct_field("camps_stacked"),
        "watcher_taken": _ability_use_count("ability_lamp_use"),
        # TODO: lotus_gained -- no such field exists in OpenDota's data.
    },
    "green": {
        "teamfight_participation": _direct_field("teamfight_participation"),
        "roshan_kills": _direct_field("roshan_kills"),
        "stuns_seconds": _direct_field("stuns"),
        "first_blood": _direct_field("firstblood_claimed"),
        "courier_kills": _direct_field("courier_kills"),
        "tormentor_participate": _tormentor_participate,
    },
}


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------

def load_matches(path):
    """Read the list of full match-detail dicts saved by ti_matches_fetcher.py."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# PER-PLAYER / PER-MATCH HELPERS
# ---------------------------------------------------------------------------

def get_team_name(match, player):
    """
    Which team this player belongs to IN THIS MATCH, using the match's
    own radiant_name/dire_name plus the player's isRadiant flag.
    Called by: collect_role_records(), once per player per match.
    """
    if player.get("isRadiant"):
        return match.get("radiant_name") or "Unknown Radiant Team"
    return match.get("dire_name") or "Unknown Dire Team"


def display_name(player):
    """
    Best human-readable name for this player: prefer the pro nickname
    (player['name']) over the raw Steam personaname.
    Called by: build_role_owners(), once per player per match.
    """
    return player.get("name") or player.get("personaname") or "Unknown"


def player_identity(player):
    """
    A stable key for "this specific human", used to tie one player's
    appearances together across matches so we can vote on their real
    role. Prefers account_id (stable even if their display name is
    typo'd/changed mid-tournament); falls back to display_name for the
    rare case account_id is hidden/anonymized.
    Called by: build_role_owners(), once per player per match.
    """
    account_id = player.get("account_id")
    if account_id:
        return f"acct:{account_id}"
    return f"name:{display_name(player)}"


# ---------------------------------------------------------------------------
# CORE COLLECTION
# ---------------------------------------------------------------------------

def build_role_owners(matches):
    """
    Two-pass role assignment: tally each player's position_est across all
    their matches for a team, then assign their role by majority vote
    (ties broken toward the lower position number).

    Returns: per_team_role_owners {team: {position: {player_id, ...}}},
    appearances {(team, player_id): [(player, match), ...]},
    names {(team, player_id): {name, ...}}, skipped_unparsed count.
    """
    position_counts = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    appearances = defaultdict(list)
    names = defaultdict(set)
    skipped_unparsed = 0

    for match in matches:
        players = match.get("players", [])
        if not any("position_est" in p for p in players):
            skipped_unparsed += 1
            continue

        for player in players:
            pos = player.get("position_est")
            if pos is None:
                continue
            team_name = get_team_name(match, player)
            pid = player_identity(player)
            position_counts[team_name][pid][pos] += 1
            appearances[(team_name, pid)].append((player, match))
            names[(team_name, pid)].add(display_name(player))

    per_team_role_owners = defaultdict(lambda: defaultdict(set))
    for team_name, players in position_counts.items():
        for pid, counts in players.items():
            # mode of position_est for this player: highest count wins,
            # tie broken toward the lower position number.
            primary_position = min(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            per_team_role_owners[team_name][primary_position].add(pid)

    return per_team_role_owners, appearances, names, skipped_unparsed


def collect_role_records(matches, role_name, stat_extractors):
    """
    Finds each team's real player at this role (via build_role_owners),
    then records their stats across all their matches for the team.

    Returns: per_team {team: {stat_name: [values]}},
    per_team_player_names {team: {name, ...}}.
    """
    position_target = ROLE_POSITIONS[role_name]
    per_team_role_owners, appearances, names, skipped_unparsed = build_role_owners(matches)

    per_team = defaultdict(lambda: defaultdict(list))
    per_team_player_names = defaultdict(set)

    for team_name, role_owners in per_team_role_owners.items():
        owner_ids = role_owners.get(position_target, set())
        for pid in owner_ids:
            key = (team_name, pid)
            per_team_player_names[team_name] |= names[key]
            for player, match in appearances[key]:
                for stat_name, extractor in stat_extractors.items():
                    value = extractor(player, match)
                    if value is not None:
                        per_team[team_name][stat_name].append(value)

    if skipped_unparsed:
        print(f"Warning: skipped {skipped_unparsed} match(es) with no "
              f"position_est data (unparsed).")

    return per_team, per_team_player_names


# ---------------------------------------------------------------------------
# SUMMARY STATS
# ---------------------------------------------------------------------------

def summarize(records):
    """
    Turn {stat_name: [values]} into {stat_name: {n, mean, variance}}.
    Uses sample variance (divide by n-1). Needs at least 2 data points
    per stat; otherwise mean/variance are reported as None.

    Called by: main(), once per team.
    """
    summary = {}
    for stat_name, values in records.items():
        if len(values) < 2:
            summary[stat_name] = {"n": len(values), "mean": None, "variance": None}
            continue
        summary[stat_name] = {
            "n": len(values),
            "mean": statistics.mean(values),
            "variance": statistics.variance(values),
        }
    return summary


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------

def print_team_summary(team_name, player_names, summary, n_matches, stat_names):
    """
    Pretty-print one team's stat table to the console.
    Called by: main(), once per team, in the main print loop.
    """
    names_str = ", ".join(sorted(player_names))
    plural = "es" if n_matches != 1 else ""
    print(f"\n=== {team_name} -- {names_str} ({n_matches} match{plural}) ===")
    print(f"{'Stat':<26}{'N':>6}{'Mean':>14}{'Variance':>16}")
    print("-" * 62)
    for stat_name in stat_names:
        s = summary.get(stat_name, {"n": 0, "mean": None, "variance": None})
        if s["mean"] is None:
            print(f"{stat_name:<26}{s['n']:>6}{'n/a':>14}{'n/a':>16}")
        else:
            print(f"{stat_name:<26}{s['n']:>6}{s['mean']:>14.2f}{s['variance']:>16.2f}")


def write_csv_safely(rows, base_name, fieldnames):
    """
    Write rows to data/<base_name>.csv, overwriting it if it already
    exists from a previous run. Creates the data/ folder if it doesn't
    exist yet. fieldnames is passed explicitly (rather than inferred
    from rows[0]) so this still works when rows is empty.
    Called by: main(), once at the end.
    """
    os.makedirs(RESULT_DIR, exist_ok=True)
    path = os.path.join(RESULT_DIR, f"{base_name}.csv")

    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return path
    except PermissionError:
        print(f"Could not write CSV: '{path}' is locked (open elsewhere?). "
              "Close it and rerun.")
        return None


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

def ask_category():
    """Interactively prompt for a stat category if none was given on the CLI."""
    valid = list(STAT_CATEGORIES)
    while True:
        choice = input(f"Which stat category? ({'/'.join(valid)}): ").strip().lower()
        if choice in STAT_CATEGORIES:
            return choice
        print(f"Not a valid category. Choose one of: {', '.join(valid)}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python stats_analysis.py <matches_json_file> [role] [category]")
        print(f"Valid roles: {', '.join(ROLE_POSITIONS)} (default: carry)")
        print(f"Valid categories: {', '.join(STAT_CATEGORIES)} (asked interactively if omitted)")
        return

    matches_path = sys.argv[1]
    role_name = sys.argv[2] if len(sys.argv) > 2 else "carry"
    category = sys.argv[3] if len(sys.argv) > 3 else None

    if role_name not in ROLE_POSITIONS:
        print(f"Unknown role '{role_name}'. Valid roles: {', '.join(ROLE_POSITIONS)}")
        return

    if category is None:
        category = ask_category()
    elif category not in STAT_CATEGORIES:
        print(f"Unknown category '{category}'. Valid categories: {', '.join(STAT_CATEGORIES)}")
        return

    stat_extractors = STAT_CATEGORIES[category]
    stat_names = list(stat_extractors)

    # 1. Load raw match data from disk.
    matches = load_matches(matches_path)
    print(f"Loaded {len(matches)} matches. Role: {role_name}. Category: {category}")

    # 2. Walk all matches and bucket every stat by team, for the chosen role.
    per_team, per_team_player_names = collect_role_records(matches, role_name, stat_extractors)
    print(f"Identified {len(per_team)} team(s) with a position_est=="
          f"{ROLE_POSITIONS[role_name]} ({role_name}) player.")

    # 3. Sort teams by how many matches they contributed (most first).
    #    Uses the first stat in the category as the "n_matches" reference.
    reference_stat = stat_names[0]
    sorted_teams = sorted(
        per_team.keys(),
        key=lambda t: len(per_team[t].get(reference_stat, [])),
        reverse=True,
    )

    # 4. Summarize + print + build CSV rows, one team at a time.
    csv_rows = []
    for team_name in sorted_teams:
        summary = summarize(per_team[team_name])
        n_matches = len(per_team[team_name].get(reference_stat, []))
        print_team_summary(team_name, per_team_player_names[team_name], summary, n_matches, stat_names)

        row = {
            "team": team_name,
            "role": role_name,
            "category": category,
            "players_seen": ", ".join(sorted(per_team_player_names[team_name])),
            "n_matches": n_matches,
        }
        for stat_name in stat_names:
            s = summary.get(stat_name, {"mean": None, "variance": None})
            row[f"{stat_name}_mean"] = s["mean"]
            row[f"{stat_name}_variance"] = s["variance"]
        csv_rows.append(row)

    # 5. Save everything to CSV, named after category + role, e.g.
    #    red_stats_for_carrys.csv
    # Always write, even with zero rows -- otherwise a run that finds no
    # qualifying teams would silently leave a stale CSV from an earlier
    # run untouched, instead of overwriting it.
    base_name = f"{category}_stats_for_{role_name}s"
    if not csv_rows:
        print(f"\nWarning: 0 teams found for role={role_name} category={category} -- "
              f"writing an empty (header-only) {base_name}.csv, overwriting any previous file.")
    fieldnames = ["team", "role", "category", "players_seen", "n_matches"]
    for stat_name in stat_names:
        fieldnames.append(f"{stat_name}_mean")
        fieldnames.append(f"{stat_name}_variance")
    saved_path = write_csv_safely(csv_rows, base_name, fieldnames)
    if saved_path:
        print(f"\nSaved per-team summary: {saved_path}")


if __name__ == "__main__":
    main()