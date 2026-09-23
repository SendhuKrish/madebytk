# Singapore Toto Smart Filter Engine + Tracker

20 validated structural + inter-draw rules · Concentrated + Diverse + Skew + Synthesis strategies

Served at [madebytk.com](https://madebytk.com) on Azure Static Web Apps (frontend) + an
Azure Function App (API and scheduled jobs). See [`CLAUDE.md`](CLAUDE.md) for the full
architecture and [`docs/azure_functions.md`](docs/azure_functions.md) for Azure settings.

## Local development

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env.config       # then fill in .env.secret (see .env.example)

uvicorn app.main:app --reload --port 8100

# Run the cron jobs by hand
python -m app.jobs.predict
python -m app.jobs.results
```

## Deploying

Merge to `main`. GitHub Actions deploys the Function App and the Static Web App
automatically — there's no manual deploy step.

## Project Structure

```
app/
├── main.py                  — FastAPI app with all endpoints
├── utils/
│   ├── config.py            — Central config (pydantic BaseSettings)
│   └── models.py            — Pydantic request/response models
├── services/
│   ├── engine.py            — Core prediction engine (20 rules, 4 strategies)
│   ├── scraper.py           — Fetches draw results + the live next-draw feed
│   └── db.py                — Supabase client wrapper + token verification
└── jobs/
    ├── predict.py           — Generate predictions (Mon & Thu 08:00 SGT)
    ├── results.py           — Fetch results (hourly, 19:00–22:00 SGT daily)
    └── scheduling.py        — Shared "next draw date/jackpot" logic
website/
    ├── index.html           — Static frontend (Azure Static Web Apps)
    └── config.js            — Runtime config: window.API_BASE (the Function App URL)
function_app.py               — Azure Functions entry point (HTTP + timer triggers)
docs/
    ├── azure_functions.md   — Azure settings, CORS, scale values
    └── supabase_setup.sql   — Database schema
```

## API Endpoints

| Method | Path | Auth |
|--------|------|------|
| GET | `/draws` | public |
| GET | `/last-draw` | public |
| GET | `/health` | public |
| POST | `/postmortem` | public |
| POST | `/auth/login` | public (returns a bearer token) |
| GET, POST | `/predict` | token required |
| POST | `/extract-bets` | token required |
| POST, DELETE | `/draws`, `/draws/{id}` | token required |
| POST | `/fetch-results`, `/backfill-prizes`, `/backfill-g1prize` | token required |

"Token required" means a Supabase access token as `Authorization: Bearer <token>`,
obtained from `/auth/login`.
