# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

MadeByTK is a Singapore Toto prediction tracker served at madebytk.com. A FastAPI backend generates predictions using a 20-rule filter engine, fetches actual draw results from Singapore Pools, and stores everything in Supabase. A static HTML frontend displays predictions, bets, and results with match highlighting.

## Commands

```bash
# Development setup
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Run locally
uvicorn app.main:app --reload --port 8100

# Run cron jobs manually
python -m app.jobs.predict
python -m app.jobs.results

# Deploy to Azure VM (nice = low priority so Pally isn't affected)
cd ~/toto && git pull && nice -n 19 docker compose build && docker compose up -d
```

## Architecture

```
Azure VM (shared with getpally.ai — port 8000)
│
├── Toto Engine (FastAPI)        → port 8100
│   ├── GET  /draws              → all draws from Supabase (descending)
│   ├── GET  /predict            → auto-fetch + generate predictions
│   ├── POST /predict            → manual input + generate
│   ├── POST /postmortem         → analyze past predictions
│   └── GET  /health             → status check
│
├── Cron Jobs (Mon & Thu)
│   ├── app/jobs/predict.py      → 08:00 SGT — generate predictions
│   └── app/jobs/results.py      → 19:00 SGT — fetch results from SG Pools (retries hourly until 22:00)
│
├── Nginx
│   ├── getpally.ai              → proxy to port 8000 (existing, untouched)
│   └── madebytk.com             → website/ static + /api/ proxy to port 8100
│
└── Supabase (external DB)
    ├── draws table              → predictions, bets, results per draw
    └── settings table           → API keys, config
```

## Project Structure

```
app/
├── main.py                  — FastAPI app with all endpoints
├── models.py                — Pydantic request/response models
├── utils/
│   └── config.py            — Central config (pydantic BaseSettings)
├── services/
│   ├── engine.py            — Core prediction engine (20 rules, 3 strategies)
│   ├── scraper.py           — Fetches latest draw from third-party sites
│   └── db.py                — Supabase client wrapper
└── jobs/
    ├── predict.py           — Cron: generate predictions
    └── results.py           — Cron: fetch actual results
website/
    └── index.html           — Static frontend (served by nginx)
docs/
    └── supabase_setup.sql   — Database schema
```

## Hard Rules

- **No hardcoding** — all config values (URLs, keys, thresholds, schedules) go in `.env` / config.py. Never inline them in code.
- **Singapore Pools only for results** — `results.py` fetches exclusively from Singapore Pools. No fallback scrapers. If data is missing, retry hourly until available.
- **All three fields required before saving results** — winning numbers, winning shares (Groups 1-7), AND Group 1 Prize amount. No partial saves. The cron retries until all are present or the deadline is reached.
- **Predictions always triggered after results** — whenever results are saved (cron or manual endpoint), predictions for the next draw must be generated immediately. Use the `generate_next_predictions()` function in `results.py`.
- **No disturbance to Pally** — separate nginx server block, separate port (8100), separate systemd service. Pally on port 8000 is completely untouched.

## Key Design Decisions

- **Supabase for storage**: Same Supabase instance as Pally but different tables (`draws`, `settings`).
- **Frontend calls `/api/draws`**: Nginx proxies `/api/*` to the FastAPI backend.
- **APScheduler (same as Pally)**: Jobs run in-process via APScheduler, so `docker compose up` is all you need — no host crontab setup required.
- **Prediction after results uses override params** — when `results.py` triggers predictions, it passes winning numbers directly to `predict.py` via `override_*` params. This avoids re-scraping external sites that may lag behind SG Pools.

## Environment Variables

Copy `.env.example` to `.env`. Required:

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon key |
