"""
Dataset Builder
----------------
Runs the whole data pipeline for TI league_id=19719 end to end:

1. Fetches all matches (fetch_matches.py logic, skipping its
   interactive league picker since the league_id is already known)
   -> data/matches/ti_19719_matches.json + summary CSV

2. Runs stats_analysis.py for every role/category combo:
   - carry:          red, green
   - offlane:        red, green
   - mid:             red, green, blue
   - soft_support:   green, blue
   - hard_support:   green, blue
   -> data/raw_data/<category>_stats_for_<role>s.csv

3. Runs score_pipeline.py's compute_for_role() for all 5 roles
   -> data/score_per_position/<role>_score.csv

4. Runs score_pipeline.py's aggregate_final_scores()
   -> data/final_scores/core_score.csv, support_score.csv, mid_score.csv

5. Runs score_pipeline.py's export_site_json()
   -> docs/data.json (what the live site actually fetches)

By default all 5 steps run. Use --from to start partway through and skip
the earlier ones -- e.g. you already have data/matches/ti_19719_matches.json
and just changed a scoring formula, so you only want to redo steps 3-5:

    python pipeline/build_dataset.py --from scores

Stage order: fetch -> stats -> scores -> final -> site
("--from stats" reuses the existing matches file; "--from site" just
rebuilds docs/data.json from whatever's already in data/final_scores/.)

Usage:
    python pipeline/build_dataset.py [--from {fetch,stats,scores,final,site}]
"""

import argparse
import subprocess
import sys
from pathlib import Path

import fetch_matches as fetcher
import score_pipeline

PIPELINE_DIR = Path(__file__).resolve().parent
LEAGUE_ID = 19719

STAGES = ["fetch", "stats", "scores", "final", "site"]


# role -> list of categories to run through stats_analysis.py
# (reuse score_pipeline's mapping so there's one source of truth)
ROLE_CATEGORIES = score_pipeline.ROLE_CATEGORIES


def run_fetch(league_id):
    """Step 1: fetch matches for the given league_id, bypassing the interactive picker."""
    print(f"=== Step 1: fetching matches for league_id={league_id} ===")
    matches = fetcher.get_league_matches(league_id)
    print(f"Found {len(matches)} matches.")

    if not matches:
        print("No matches found for this league_id. Aborting pipeline.")
        sys.exit(1)

    match_details = fetcher.fetch_all_match_details(matches)

    fetcher.ensure_result_dir()
    json_filename = f"ti_{league_id}_matches.json"
    fetcher.save_json(match_details, json_filename)
    fetcher.save_summary_csv(match_details, f"ti_{league_id}_matches_summary.csv")

    return fetcher.RESULT_DIR / json_filename


def run_stats(matches_path, role_categories):
    """Step 2: run stats_analysis.py for every role/category combo."""
    print(f"\n=== Step 2: running stats_analysis.py on {matches_path} ===")
    for role, categories in role_categories.items():
        for category in categories:
            print(f"\n--- role={role} category={category} ---")
            subprocess.run(
                [sys.executable, str(PIPELINE_DIR / "stats_analysis.py"), str(matches_path), role, category],
                check=True,
            )


def run_scores(roles):
    """Step 3: run score_pipeline.py's compute_for_role() for every role."""
    print("\n=== Step 3: computing per-position scores for all roles ===")
    for role in roles:
        print(f"\n--- role={role} ---")
        score_pipeline.compute_for_role(role)


def run_final_aggregation():
    """Step 4: run score_pipeline.py's aggregate_final_scores() to build core/support/mid final tables."""
    print("\n=== Step 4: aggregating final scores ===")
    score_pipeline.aggregate_final_scores()


def run_site_export():
    """Step 5: run score_pipeline.py's export_site_json() to rebuild docs/data.json."""
    print("\n=== Step 5: exporting docs/data.json ===")
    out_path = score_pipeline.export_site_json()
    if out_path is None:
        print("docs/data.json was NOT updated -- see the warning above.")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Build the TI stats dataset, optionally starting partway through.")
    parser.add_argument(
        "--from", dest="from_stage", choices=STAGES, default="fetch",
        help="Stage to start from (default: fetch, i.e. run everything). "
             "Earlier stages' output must already exist on disk.",
    )
    args = parser.parse_args()
    start_index = STAGES.index(args.from_stage)

    print(f"Starting pipeline from stage: {args.from_stage}\n")

    if start_index <= STAGES.index("fetch"):
        matches_path = run_fetch(LEAGUE_ID)
    else:
        matches_path = fetcher.RESULT_DIR / f"ti_{LEAGUE_ID}_matches.json"
        print(f"Skipping fetch -- reusing {matches_path}")

    if start_index <= STAGES.index("stats"):
        run_stats(matches_path, ROLE_CATEGORIES)
    else:
        print("Skipping stats -- reusing existing data/raw_data/ files")

    if start_index <= STAGES.index("scores"):
        run_scores(ROLE_CATEGORIES.keys())
    else:
        print("Skipping scores -- reusing existing data/score_per_position/ files")

    if start_index <= STAGES.index("final"):
        run_final_aggregation()
    else:
        print("Skipping final aggregation -- reusing existing data/final_scores/ files")

    run_site_export()
    print("\nPipeline finished.")


if __name__ == "__main__":
    main()