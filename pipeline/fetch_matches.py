"""
TI Matches Fetcher
-------------------
Fetches match data for a chosen "The International" (TI) Dota 2 tournament
from the OpenDota API and saves the results to JSON and CSV files.

Usage:
    python pipeline/fetch_matches.py
"""

import json
import os
import time
import csv
from pathlib import Path

import requests

BASE_URL = "http://api.opendota.com/api"
REQUEST_DELAY = 1.1  # seconds between requests to stay under the 60/min rate limit

# All paths are resolved relative to the repo root (this file's parent
# directory's parent), so scripts behave the same no matter where they're
# run from.
ROOT_DIR = Path(__file__).resolve().parent.parent
RESULT_DIR = ROOT_DIR / "data" / "matches"  # all output files (json + csv) are saved here


def get_all_leagues() -> json:
    """Fetch the full list of leagues from OpenDota."""
    response = requests.get(f"{BASE_URL}/leagues")
    response.raise_for_status()
    return response.json()


def find_ti_leagues(leagues):
    """Filter the league list down to entries whose name contains 'international'."""
    return [
        league for league in leagues
        if "international" in league.get("name", "").lower()
    ]


def choose_league(ti_leagues):
    """Print available TI leagues and let the user pick one by league_id."""
    if not ti_leagues:
        raise ValueError("No 'International' leagues found in OpenDota's league list.")

    print("Available TI leagues:")
    for league in ti_leagues:
        print(f"  {league['leagueid']:>8}  {league['name']}")

    chosen_id = input("\nEnter the league_id you want to fetch matches for: ").strip()
    return int(chosen_id)


def get_league_matches(league_id):
    """Fetch the list of matches for a given league_id."""
    response = requests.get(f"{BASE_URL}/leagues/{league_id}/matches")
    response.raise_for_status()
    return response.json()


def get_match_detail(match_id):
    """Fetch full detail for a single match_id."""
    response = requests.get(f"{BASE_URL}/matches/{match_id}")
    response.raise_for_status()
    return response.json()


def fetch_all_match_details(matches, delay=REQUEST_DELAY):
    """Fetch detailed data for every match in the given match list, with rate limiting."""
    details = []
    total = len(matches)

    for index, match in enumerate(matches, start=1):
        match_id = match["match_id"]
        print(f"Fetching match {index}/{total} (match_id={match_id})...")
        try:
            detail = get_match_detail(match_id)
            details.append(detail)
        except requests.RequestException as error:
            print(f"  Failed to fetch match {match_id}: {error}")
        time.sleep(delay)

    return details


def ensure_result_dir():
    """Create the result output folder if it doesn't exist yet."""
    os.makedirs(RESULT_DIR, exist_ok=True)


def save_json(data, filename):
    """Save data as a JSON file inside the result folder."""
    filepath = os.path.join(RESULT_DIR, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Saved JSON: {filepath}")


def save_summary_csv(match_details, filename):
    """Save a flat summary CSV (one row per match) from full match details, inside the result folder."""
    fieldnames = [
        "match_id", "radiant_name", "dire_name",
        "radiant_win", "duration", "start_time",
        "radiant_score", "dire_score",
    ]

    filepath = os.path.join(RESULT_DIR, filename)
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for match in match_details:
            writer.writerow({
                "match_id": match.get("match_id"),
                "radiant_name": match.get("radiant_name"),
                "dire_name": match.get("dire_name"),
                "radiant_win": match.get("radiant_win"),
                "duration": match.get("duration"),
                "start_time": match.get("start_time"),
                "radiant_score": match.get("radiant_score"),
                "dire_score": match.get("dire_score"),
            })
    print(f"Saved CSV: {filepath}")


def main():
    print("Fetching league list from OpenDota...")
    leagues = get_all_leagues()

    ti_leagues = find_ti_leagues(leagues)
    league_id = choose_league(ti_leagues)

    print(f"\nFetching matches for league_id={league_id}...")
    matches = get_league_matches(league_id)
    print(f"Found {len(matches)} matches.")

    if not matches:
        print("No matches found for this league_id. Exiting.")
        return

    match_details = fetch_all_match_details(matches)

    ensure_result_dir()
    save_json(match_details, f"ti_{league_id}_matches.json")
    save_summary_csv(match_details, f"ti_{league_id}_matches_summary.csv")

    print("\nDone.")


if __name__ == "__main__":
    main()
