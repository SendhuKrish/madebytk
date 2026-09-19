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

# Deploy: merge to main. GitHub Actions deploys the Function App (main_madebytk-api.yml)
# and the Static Web App (azure-static-web-apps-*.yml) automatically.
```

Azure setup, settings and the Flex scale values that matter: `docs/azure_functions.md`.

## Architecture

```
Cloudflare DNS (DNS only, not proxied) → madebytk.com, www.madebytk.com
│
├── Azure Static Web Apps (Free)     → website/ (static frontend)
│   └── website/config.js            → API_BASE = the Function App URL (cross-origin calls)
│
├── Azure Function App "madebytk-api" (Flex Consumption, Python 3.11, rg-madebytk-func)
│   ├── HTTP: FastAPI via AsgiFunctionApp (function_app.py), no /api prefix
│   │   ├── GET  /draws, /health, /last-draw   → public
│   │   ├── POST/DELETE /draws, /extract-bets, /fetch-results, /backfill-*, /predict
│   │   │                                        → need a Supabase bearer token
│   │   └── POST /auth/login                   → returns the token
│   └── Timer triggers (UTC NCRONTAB from app settings)
│       ├── predict_timer  PREDICT_SCHEDULE   → Mon & Thu 08:00 SGT — generate predictions
│       └── results_timer  RESULTS_SCHEDULE   → hourly 19:00–22:00 SGT daily — single-shot;
│                                               fetches results for pending draws
│
└── Supabase (external DB)
    ├── draws table              → predictions, bets, results per draw
    └── settings table           → API keys, config
```

The old dedicated VM (Docker + nginx + APScheduler) is retired. `SCHEDULER_ENABLED`
defaults to true so `uvicorn app.main:app` still runs APScheduler locally; the Function
App sets it to false because the timer triggers run the jobs.

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
    ├── index.html           — Static frontend (Azure Static Web Apps)
    └── config.js            — Runtime config: window.API_BASE (the Function App URL)
docs/
    └── supabase_setup.sql   — Database schema
```

## Hard Rules

- **No hardcoding** — all config values (URLs, keys, thresholds, schedules) go in `.env` / config.py. Never inline them in code.
- **Singapore Pools only for results** — `results.py` fetches exclusively from Singapore Pools. No fallback scrapers. If data is missing, retry hourly until available.
- **All three fields required before saving results** — winning numbers, winning shares (Groups 1-7), AND Group 1 Prize amount. No partial saves. The cron retries until all are present or the deadline is reached.
- **Predictions always triggered after results** — whenever results are saved (cron or manual endpoint), predictions for the next draw must be generated immediately. Use the `generate_next_predictions()` function in `results.py`.
- **No disturbance to Pally** — MadeByTK has its own Azure resources (resource group `rg-madebytk-func`), entirely separate from Pally.

## Key Design Decisions

- **Supabase for storage**: Same Supabase instance as Pally but different tables (`draws`, `settings`).
- **Frontend calls the Function App directly**: `website/config.js` sets `window.API_BASE` (falls back to same-origin `/api`). CORS is answered by the Function App's *platform* CORS setting, not FastAPI: the Functions host handles preflight first, so setting both sends duplicate headers.
- **Scheduling**: jobs are single-shot; the schedule is the retry. Timer triggers on Azure (`use_monitor=False` so a restart never fires a missed run — predictions are random and would be overwritten). Never run two schedulers at once.
- **API auth**: write/costly endpoints (`POST/DELETE /draws`, `/extract-bets`, `/fetch-results`, `/backfill-*`, `/predict`) require a Supabase access token (`Authorization: Bearer`) obtained via `/auth/login`. `GET /draws`, `/health`, `/last-draw`, `/postmortem` are public.
- **Prediction after results uses override params** — when `results.py` triggers predictions, it passes winning numbers directly to `predict.py` via `override_*` params. This avoids re-scraping external sites that may lag behind SG Pools.

## Environment Variables

Copy `.env.example` to `.env`. Required:

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_KEY` | Supabase anon key |
| `CORS_ORIGINS` | Optional. Origins FastAPI itself answers CORS for. Leave unset on Azure (platform CORS handles it) |
