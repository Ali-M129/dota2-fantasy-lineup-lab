"""
Score Pipeline
--------------
Three stages, runnable together or separately:

PART 1 -- per-position scores
    Reads data/raw_data/<category>_stats_for_<role>s.csv (from
    stats_analysis.py) and converts each team's mean stat into a score
    via the fixed multipliers below.
    -> data/score_per_position/<role>_score.csv

PART 2 -- final scores
    core_score.csv = avg(carry_score, offlane_score); support_score.csv
    = avg(soft_support_score, hard_support_score); mid_score.csv is
    copied through unchanged. A team missing from one side of an
    average is carried through as-is, with a warning.
    -> data/final_scores/{core,support,mid}_score.csv

PART 3 -- docs/data.json
    Exports the 3 final tables as one JSON file for the frontend.
    'n/a' cells become 0.0 (with a warning).
    -> docs/data.json

Usage:
    python pipeline/score_pipeline.py            # all roles + aggregation + docs/data.json
    python pipeline/score_pipeline.py <role>     # just that role's score
    python pipeline/score_pipeline.py --site     # just rebuild docs/data.json
"""

import csv
import json
import os
import shutil
import sys
from pathlib import Path

# Resolved relative to the repo root so this works no matter the cwd.
ROOT_DIR = Path(__file__).resolve().parent.parent
INPUT_DIR = ROOT_DIR / "data" / "raw_data"               # stats_analysis.py output lives here
POSITION_DIR = ROOT_DIR / "data" / "score_per_position"  # per-role score CSVs go here
FINAL_DIR = ROOT_DIR / "data" / "final_scores"            # core/support/mid final tables go here
SITE_DIR = ROOT_DIR / "docs"                              # index.html + data.json for the frontend live here (GitHub Pages source)

# ---------------------------------------------------------------------------
# PART 1: PER-POSITION SCORES
# ---------------------------------------------------------------------------

ROLE_ALIASES = {
    "midlane": "mid",
}

# Which stat categories apply to each role
ROLE_CATEGORIES = {
    "carry": ["red", "green"],
    "offlane": ["red", "green"],
    "mid": ["red", "green", "blue"],
    "soft_support": ["green", "blue"],
    "hard_support": ["green", "blue"],
}

# SCORE FORMULAS
# stat_name (as it appears in the CSV, before "_mean") -> function(mean) -> score
RED_SCORE_FORMULAS = {
    "tower_kills": lambda mean: mean * 352,
    "last_hits": lambda mean: mean * 3,               # creep score
    "deaths": lambda mean: 1950 - 195 * mean,          # 1950 - 195d
    "madstone_collected": lambda mean: mean * 13,
    "gpm": lambda mean: mean * 2,
    "kills": lambda mean: mean * 107,
}

GREEN_SCORE_FORMULAS = {
    "teamfight_participation": lambda mean: mean * 2124,
    "roshan_kills": lambda mean: mean * 1172,
    "stuns_seconds": lambda mean: mean * 10,
    "tormentor_participate": lambda mean: mean * 879,  # tormentor kills
    "courier_kills": lambda mean: mean * 703,
    "first_blood": lambda mean: mean * 1934,
}

BLUE_SCORE_FORMULAS = {
    "observer_wards_planted": lambda mean: mean * 117,
    "watcher_taken": lambda mean: mean * 147,
    "camps_stacked": lambda mean: mean * 234,
    "rune_grabbed": lambda mean: mean * 141,
    "smoke_used": lambda mean: mean * 293,
    # lotus_gained is handled separately below -- always 0, no data exists for it
}

FORMULAS_BY_CATEGORY = {
    "red": RED_SCORE_FORMULAS,
    "green": GREEN_SCORE_FORMULAS,
    "blue": BLUE_SCORE_FORMULAS,
}

# Which color category each stat belongs to, for the frontend's banner
# picker. Built from FORMULAS_BY_CATEGORY so it can never drift from the
# actual scoring categories -- lotus_gained is the one exception (it has
# no formula since it's always 0) so it's added in manually.
STAT_COLOR_OF = {
    stat: color
    for color, formulas in FORMULAS_BY_CATEGORY.items()
    for stat in formulas
}
STAT_COLOR_OF["lotus_gained"] = "blue"

# Column header shown in the output table, per stat
COLUMN_LABELS = {
    "tower_kills": "Tower Kills",
    "last_hits": "Creep Score",
    "deaths": "Death",
    "madstone_collected": "Madstone",
    "gpm": "GPM",
    "kills": "Kills",
    "teamfight_participation": "Teamfight",
    "roshan_kills": "Roshan Kills",
    "stuns_seconds": "Stuns",
    "tormentor_participate": "Tormentor Kills",
    "courier_kills": "Courier Kills",
    "first_blood": "First Blood",
    "observer_wards_planted": "OBS Wards Planted",
    "watcher_taken": "Watcher Taken",
    "camps_stacked": "Camps Stacked",
    "rune_grabbed": "Runes Grabbed",
    "smoke_used": "Smokes Used",
    "lotus_gained": "Lotuses Gained",
}


def resolve_role(raw_role):
    role = ROLE_ALIASES.get(raw_role, raw_role)
    if role not in ROLE_CATEGORIES:
        valid = ", ".join(sorted(set(ROLE_CATEGORIES) | set(ROLE_ALIASES)))
        print(f"Unknown role '{raw_role}'. Valid roles: {valid}")
        sys.exit(1)
    return role


def load_category_csv(csv_path, formulas):
    """
    Read a stats_analysis.py output CSV and return:
        {team_name: {"names": str, "scores": {stat_name: score_or_None}}}
    A score is None if the mean was empty (stat had <2 data points).
    """
    rows = {}
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            team = row["team"]
            scores = {}
            for stat_name, formula in formulas.items():
                raw = row.get(f"{stat_name}_mean", "")
                scores[stat_name] = None if raw in ("", "None") else formula(float(raw))
            rows[team] = {
                "names": row.get("players_seen", ""),
                "scores": scores,
            }
    return rows


def merge_categories(data_by_category):
    """
    Merge per-category {team: {...}} dicts into one row per team.
    Warns (but doesn't fail) if a team is missing from one category.
    """
    all_teams = set()
    for data in data_by_category.values():
        all_teams |= set(data)

    merged = {}
    for team in sorted(all_teams):
        names = ""
        scores = {}
        for category, data in data_by_category.items():
            entry = data.get(team)
            if entry is None:
                print(f"Warning: '{team}' missing from {category} data -- those columns left blank.")
                continue
            names = names or entry["names"]
            scores.update(entry["scores"])
        merged[team] = {"names": names, "scores": scores}
    return merged


def build_table_rows(merged, stat_order):
    """
    Turn the merged per-team dict into a list of row dicts ready for CSV/printing.
    "team" and "player" are kept as separate columns (rather than one combined
    "team (player)" string) so the aggregation part below -- which joins
    carry_score.csv with offlane_score.csv on team -- can match rows
    without having to parse text back apart.
    """
    rows = []
    for team in sorted(merged):
        entry = merged[team]
        row = {"team": team, "player": entry["names"]}
        for stat_name in stat_order:
            if stat_name == "lotus_gained":
                score = 0
            else:
                score = entry["scores"].get(stat_name)
            row[COLUMN_LABELS[stat_name]] = round(score, 1) if score is not None else "n/a"
        rows.append(row)
    return rows


def print_table(rows):
    """Pretty-print a score table to the console."""
    if not rows:
        print("No data to show.")
        return

    headers = list(rows[0].keys())
    col_widths = {h: max(len(h), max(len(str(r[h])) for r in rows)) + 2 for h in headers}

    header_line = "".join(h.ljust(col_widths[h]) for h in headers)
    print(header_line)
    print("-" * len(header_line))
    for row in rows:
        print("".join(str(row[h]).ljust(col_widths[h]) for h in headers))


def save_position_csv(rows, filename):
    os.makedirs(POSITION_DIR, exist_ok=True)
    path = os.path.join(POSITION_DIR, filename)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved: {path}")


def compute_for_role(role):
    """
    Run the full score computation for one role: load its category CSVs,
    merge them, build the table, print it, and save it to POSITION_DIR.
    Returns the saved file path, or None if an input file was missing.
    Callable directly (e.g. from build_dataset.py) as well as via the CLI below.
    """
    categories = ROLE_CATEGORIES[role]

    data_by_category = {}
    for category in categories:
        csv_path = os.path.join(INPUT_DIR, f"{category}_stats_for_{role}s.csv")
        if not os.path.exists(csv_path):
            print(f"Missing input file: {csv_path}. Run stats_analysis.py for '{role}' {category} first.")
            return None
        data_by_category[category] = load_category_csv(csv_path, FORMULAS_BY_CATEGORY[category])

    # Build column order: all stats from each category in the role's category order,
    # with lotus_gained tacked onto the end of the blue block.
    stat_order = []
    for category in categories:
        stat_order.extend(FORMULAS_BY_CATEGORY[category])
        if category == "blue":
            stat_order.append("lotus_gained")

    merged = merge_categories(data_by_category)
    rows = build_table_rows(merged, stat_order)

    print_table(rows)
    save_position_csv(rows, f"{role}_score.csv")
    return os.path.join(POSITION_DIR, f"{role}_score.csv")


# ---------------------------------------------------------------------------
# PART 2: FINAL SCORES (aggregated from the per-position tables above)
# ---------------------------------------------------------------------------

# (output_filename, [role1_score.csv, role2_score.csv])
AVERAGE_GROUPS = [
    ("core_score.csv", ["carry_score.csv", "offlane_score.csv"]),
    ("support_score.csv", ["soft_support_score.csv", "hard_support_score.csv"]),
]

# score file copied through as-is, no averaging
PASSTHROUGH_FILES = ["mid_score.csv"]


def load_score_csv(path):
    """
    Read a compute_for_role() output CSV.
    Returns (fieldnames, {team_name: row_dict}).
    """
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = {row["team"]: row for row in reader}
    return fieldnames, rows


def average_stat(value_a, value_b):
    """Average two stat cells, treating 'n/a' as missing. Returns rounded float or 'n/a'."""
    vals = [float(v) for v in (value_a, value_b) if v not in (None, "n/a")]
    if not vals:
        return "n/a"
    return round(sum(vals) / len(vals), 1)


def build_averaged_table(path_a, path_b):
    """Average two per-position score CSVs (same stat columns) into one table, per team.
    Also combines the "player" column from both sides into one "player" field
    (e.g. "ammar_T_F, skiter"), same as mid_score.csv already has, so core/support
    rows can show a player-style label in the frontend too."""
    fields_a, rows_a = load_score_csv(path_a)
    fields_b, rows_b = load_score_csv(path_b)

    stat_columns = [c for c in fields_a if c not in ("team", "player")]

    all_teams = sorted(set(rows_a) | set(rows_b))
    output_rows = []
    for team in all_teams:
        row_a = rows_a.get(team)
        row_b = rows_b.get(team)

        if row_a is None:
            print(f"Warning: '{team}' has no row in {os.path.basename(path_a)} -- using {os.path.basename(path_b)} values as-is.")
        if row_b is None:
            print(f"Warning: '{team}' has no row in {os.path.basename(path_b)} -- using {os.path.basename(path_a)} values as-is.")

        player_a = (row_a.get("player") or "").strip() if row_a else ""
        player_b = (row_b.get("player") or "").strip() if row_b else ""
        combined_player = ", ".join(p for p in (player_a, player_b) if p)

        out_row = {"team": team, "player": combined_player}
        for col in stat_columns:
            val_a = row_a.get(col) if row_a else None
            val_b = row_b.get(col) if row_b else None
            out_row[col] = average_stat(val_a, val_b)
        output_rows.append(out_row)

    return ["team", "player"] + stat_columns, output_rows


def save_final_csv(fieldnames, rows, filename):
    os.makedirs(FINAL_DIR, exist_ok=True)
    path = os.path.join(FINAL_DIR, filename)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved: {path}")
    return path


def aggregate_final_scores():
    """
    Build core_score.csv, support_score.csv, and mid_score.csv in
    FINAL_DIR from whatever's currently in POSITION_DIR.
    Callable directly (e.g. from build_dataset.py) as well as via the CLI below.
    """
    os.makedirs(FINAL_DIR, exist_ok=True)

    for output_name, (name_a, name_b) in AVERAGE_GROUPS:
        path_a = os.path.join(POSITION_DIR, name_a)
        path_b = os.path.join(POSITION_DIR, name_b)
        missing = [p for p in (path_a, path_b) if not os.path.exists(p)]
        if missing:
            print(f"Skipping {output_name}: missing {', '.join(missing)}. "
                  f"Run compute_for_role() for the relevant roles first.")
            continue

        fieldnames, rows = build_averaged_table(path_a, path_b)
        save_final_csv(fieldnames, rows, output_name)

    for filename in PASSTHROUGH_FILES:
        src = os.path.join(POSITION_DIR, filename)
        if not os.path.exists(src):
            print(f"Skipping {filename}: not found in {POSITION_DIR}. Run compute_for_role() for it first.")
            continue
        os.makedirs(FINAL_DIR, exist_ok=True)
        dst = os.path.join(FINAL_DIR, filename)
        shutil.copyfile(src, dst)
        print(f"Copied: {dst}")


# ---------------------------------------------------------------------------
# PART 3: JSON EXPORT FOR THE FRONTEND
# ---------------------------------------------------------------------------

# which final_scores file feeds which role in the frontend, and whether
# that file has a "player" column (all three do now: core/support combine
# both position players' names, e.g. "ammar_T_F, skiter"; mid keeps its
# single player as before)
SITE_ROLE_FILES = {
    "core": ("core_score.csv", True),
    "mid": ("mid_score.csv", True),
    "support": ("support_score.csv", True),
}


def load_final_csv_for_site(path, has_player):
    """
    Read a final_scores CSV and return (columns, rows) in the shape the
    frontend expects: columns = stat column names only (no team/player),
    rows = list of dicts with team (+ player) and numeric stat values.
    'n/a' cells become 0.0 so they don't break the frontend's math -- a
    warning is printed so it's not silent.
    """
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        id_cols = {"team", "player"} if has_player else {"team"}
        stat_cols = [c for c in fieldnames if c not in id_cols]

        rows = []
        for raw_row in reader:
            row = {"team": raw_row["team"]}
            if has_player:
                row["player"] = raw_row.get("player", "")
            for col in stat_cols:
                val = raw_row.get(col, "")
                if val in ("", "n/a", None):
                    print(f"Warning: '{raw_row['team']}' has n/a for '{col}' in "
                          f"{os.path.basename(path)} -- using 0.0 in data.json.")
                    row[col] = 0.0
                else:
                    row[col] = float(val)
            rows.append(row)

    return stat_cols, rows


def export_site_json():
    """
    Build docs/data.json from whatever's currently in FINAL_DIR
    (core_score.csv, mid_score.csv, support_score.csv). This is the file
    the frontend (docs/index.html) fetches at load time -- overwrite it
    any time the underlying data changes, no HTML edits required.

    Also includes a "meta.stat_colors" block ({color: [column_label, ...]})
    built from STAT_COLOR_OF/COLUMN_LABELS, so the frontend's banner
    picker always matches whatever stats actually exist in the data --
    it never hardcodes its own copy of this mapping.
    """
    data = {}
    for role, (filename, has_player) in SITE_ROLE_FILES.items():
        path = os.path.join(FINAL_DIR, filename)
        if not os.path.exists(path):
            print(f"Skipping data.json: missing {path}. Run aggregate_final_scores() first.")
            return None
        columns, rows = load_final_csv_for_site(path, has_player)
        data[role] = {"columns": columns, "rows": rows}

    stat_colors = {"red": [], "green": [], "blue": []}
    for stat_name, color in STAT_COLOR_OF.items():
        stat_colors[color].append(COLUMN_LABELS[stat_name])
    data["meta"] = {"stat_colors": stat_colors}

    os.makedirs(SITE_DIR, exist_ok=True)
    out_path = os.path.join(SITE_DIR, "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"Saved: {out_path}")
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) >= 2:
        if sys.argv[1] == "--site":
            # Just rebuild docs/data.json from the current final_scores CSVs
            # -- use this after hand-editing a final CSV, or any time you
            # want to refresh the site without recomputing everything.
            print("--- exporting docs/data.json ---")
            export_site_json()
            return

        # Single-role mode: just compute that one role's score (same as the
        # old compute_score.py CLI). No aggregation, since aggregation needs
        # all the relevant per-position files to exist.
        role = resolve_role(sys.argv[1])
        compute_for_role(role)
        return

    # No role given: compute every role's score, then build the final tables
    # -- this replaces running compute_score.py once per role followed by
    # aggregate_final_scores.py.
    for role in ROLE_CATEGORIES:
        print(f"\n--- role={role} ---")
        compute_for_role(role)

    print("\n--- aggregating final scores ---")
    aggregate_final_scores()

    print("\n--- exporting docs/data.json ---")
    export_site_json()


if __name__ == "__main__":
    main()