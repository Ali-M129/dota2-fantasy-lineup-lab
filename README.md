# TI 2026 · Fantasy Lineup Lab

A small end-to-end data pipeline + static web app that turns raw **The
International 2026** Dota 2 match data into role-by-role scouting scores, and
lets you build and compare a fantasy lineup from them in the browser.

**Live site:** `https://<your-username>.github.io/fantasy-lineup-lab/`
*(update this link once GitHub Pages is enabled — see [Deploying the site](#deploying-the-site))*

---

## What it does

1. **Fetch** — pulls every match from a chosen TI league via the
   [OpenDota API](https://docs.opendota.com/).
2. **Analyze** — computes per-team, per-role statistical means (kills, GPM,
   wards, teamfight participation, etc.) across three stat categories
   (`red` / `green` / `blue`).
3. **Score** — converts those means into weighted scores per role using a
   fixed set of formulas, then aggregates them into three final tables:
   `core`, `mid`, and `support`.
4. **Publish** — exports the final tables as `docs/data.json`, which the
   static frontend in `docs/index.html` fetches to render an interactive
   lineup builder (drag stat weights, compare teams, get a suggested lineup).

```
OpenDota API  →  fetch_matches.py  →  stats_analysis.py  →  score_pipeline.py  →  docs/data.json  →  docs/index.html
```

## Project structure

```
fantasy-lineup-lab/
├── docs/                     # Static site — GitHub Pages serves from here
│   ├── index.html            #   Fantasy lineup lab UI
│   └── data.json             #   Generated scoring data consumed by the UI
├── pipeline/                 # Python ETL scripts (run locally, not deployed)
│   ├── fetch_matches.py      #   Stage 1 — pull matches from OpenDota
│   ├── stats_analysis.py     #   Stage 2 — per-role/category stat means
│   ├── score_pipeline.py     #   Stage 3+4 — scoring, aggregation, JSON export
│   └── build_dataset.py      #   Orchestrates all stages end to end
├── data/                     # Generated intermediate data (git-ignored, kept via .gitkeep)
│   ├── matches/               #   Raw fetched match JSON/CSV
│   ├── raw_data/               #   Per-role/category stat CSVs
│   ├── score_per_position/    #   Per-role score CSVs
│   └── final_scores/           #   core/mid/support final score CSVs
├── requirements.txt
├── LICENSE
└── README.md
```

All pipeline scripts resolve their input/output paths relative to the repo
root, so they can be run from anywhere (`python pipeline/build_dataset.py`
works the same whether you're in the repo root or not).

## Running the pipeline locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run the full pipeline (fetch -> stats -> scores -> final -> docs/data.json)
python pipeline/build_dataset.py

# ...or resume partway through if you already have earlier stage output:
python pipeline/build_dataset.py --from scores
```

Stages, in order: `fetch → stats → scores → final → site`. The last stage
always runs and rebuilds `docs/data.json` from the final score CSVs — so a
plain `python pipeline/build_dataset.py` run is enough to update the live
site's data end to end, no separate command needed. See the docstring at
the top of `pipeline/build_dataset.py` for what each stage reads/writes.

To only refresh `docs/data.json` from already-computed final scores (e.g.
after hand-editing a CSV, skipping the fetch/stats/scores stages entirely):

```bash
python pipeline/build_dataset.py --from site
# equivalent shortcut:
python pipeline/score_pipeline.py --site
```

## Running the site locally

The frontend fetches `data.json` with `fetch()`, which browsers block on
`file://` pages — serve the `docs/` folder over HTTP instead:

```bash
cd docs
python -m http.server 8000
# then open http://localhost:8000
```

## Deploying the site

This repo is set up so **GitHub Pages can serve directly from `docs/`** —
no build step, no separate branch needed.

1. Push this repo to GitHub.
2. Go to **Settings → Pages**.
3. Under **Build and deployment → Source**, choose **Deploy from a branch**.
4. Set **Branch** to `main` and **Folder** to **`/docs`**, then **Save**.
5. After a minute or two, your site is live at
   `https://<your-username>.github.io/<repo-name>/`.

Any time you regenerate `docs/data.json` (via the pipeline) and push, the
live site updates automatically — no HTML changes required.

## Prefix suggestion

On top of the role scores, the pipeline also estimates which **prefix
title** (Crimson, Cerulean, Royal, ...) is worth taking for a given
lineup. A prefix gives a fixed bonus, but only on maps where the hero
played belongs to that prefix's hero pool — so its value depends on how
often a lineup's players actually pick from that pool.

- `pipeline/prefixes.py` — reference data (bonus % + hero pool) for each
  prefix, ported from
  [TinyKiecoo/Calculator-for-DOTA2-TI-Fantasy](https://github.com/TinyKiecoo/Calculator-for-DOTA2-TI-Fantasy).
- `pipeline/prefix_analysis.py` — reuses `stats_analysis.py`'s role-owner
  detection to compute each team's core/mid/support hero-pick % per
  prefix (repeat picks count extra, on purpose), then merges it into
  `docs/data.json` as `prefixPickPct` per row plus a `meta.prefixes`
  block. Runs automatically as step 6 of `build_dataset.py`, or on its
  own: `python pipeline/prefix_analysis.py data/matches/<file>.json`
  (needs `docs/data.json` to already exist).
- Combining core/mid/support into one roster-level prefix pick % weighs
  **mid double** core's or support's weight — core and support are each
  an average of two real players (smoothing out one player's hero-pool
  lean), while mid is a single, undiluted player, so doubling it keeps
  the three slots on equal footing. See the comments at the top of
  `pipeline/prefix_analysis.py` for the full reasoning.
- The frontend (`docs/index.html`) shows the best prefix inline on every
  suggested lineup, plus a manual section where you pick a specific
  core/mid/support entry and get the top 3 prefixes (pick %, bonus %,
  and expected/"final" %) for that exact lineup.

## Tech notes

- **Data source:** [OpenDota API](https://docs.opendota.com/), no API key
  required, rate-limited client-side to stay under 60 req/min.
- **Frontend:** plain HTML/CSS/JS, no build tooling or framework — kept
  dependency-free on purpose so it can be served as-is from `docs/`.
- **Scoring formulas** live in `pipeline/score_pipeline.py` and are the
  single source of truth for both the CSV scores and the frontend's stat
  color grouping (`meta.stat_colors` in `data.json`), so they can't drift
  out of sync.

## License

MIT — see [LICENSE](LICENSE).