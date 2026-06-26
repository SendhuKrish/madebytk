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
│   └── app/jobs/results.py      → 22:00 SGT — fetch results from SG Pools
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

## Key Design Decisions

- **No disturbance to Pally**: Separate nginx server block, separate port (8100), separate systemd service. Pally on port 8000 is completely untouched.
- **Supabase for storage**: Same Supabase instance as Pally but different tables (`draws`, `settings`).
- **Frontend calls `/api/draws`**: Nginx proxies `/api/*` to the FastAPI backend. Frontend falls back to direct Supabase reads if API is down.
- **APScheduler (same as Pally)**: Jobs run in-process via APScheduler, so `docker compose up` is all you need — no host crontab setup required.

## Environment Variables

Copy `.env.example` to `.env`. Required:

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon key |
