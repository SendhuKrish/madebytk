# MadeByTK — Feature Specification

> **Keep this file updated** whenever a feature is added, changed, or removed.
> Last updated: 2026-09-23

---

## Overview

**MadeByTK** is a Singapore Toto prediction tracker served at madebytk.com. It generates predictions using a 20-rule structural filter engine, tracks bets placed, fetches actual draw results, and highlights matches — all displayed on a single-page website.

---

## Architecture at a Glance

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python, async), on Azure Functions (Flex Consumption) via `AsgiFunctionApp` |
| Prediction Engine | Custom 20-rule filter (structural + inter-draw) |
| Database | Supabase (PostgreSQL) - shared instance with Pally, separate tables |
| Frontend | Static HTML, on Azure Static Web Apps; calls the Function App directly (no proxy) |
| Auth | Supabase Auth; write/costly endpoints require a bearer token |
| Scheduling | Azure Functions timer triggers (Mon & Thu predict; hourly results, 19:00-22:00 SGT daily) |
| DNS / CDN | Cloudflare (DNS only, not proxied, for both Azure apps) |

---

## 1. Landing Page (madebytk.com)

### What the user sees

A dark-themed single-page tracker showing all Toto draws in **descending date order** (newest first). Each draw is an expandable card displaying three columns:

| Column | Content |
|---|---|
| **Predictions** | AI-generated prediction lines for that draw |
| **Bets Placed** | Actual bet slips entered (Ordinary, System 7–12) |
| **Draw Results** | Winning numbers + additional number |

### Match highlighting

When results are available, bet numbers that match winning numbers are highlighted in green. Additional number matches are highlighted in amber. A badge on each card header shows the best match count (e.g. "3 matches") or "No matches" / "Pending".

### Responsive design

On mobile (< 768px), columns stack vertically. Draw stats badges hide to save space.

---

## 2. API Endpoints

All endpoints are served by FastAPI through the Function App, with no `/api` prefix. The frontend reads the base URL from `website/config.js` (`window.API_BASE`) and calls it directly; the Function App's platform CORS setting (not FastAPI's) answers the browser preflight. `POST/DELETE /draws`, `/extract-bets`, `/fetch-results`, `/backfill-*`, and both `/predict` routes require a Supabase bearer token from `/auth/login`; the rest are public.

### GET /draws

Returns all draw records from Supabase, ordered by `draw_date` descending. Each record contains:

```json
{
  "id": "uuid",
  "draw_date": "2026-06-23",
  "draw_number": "3855",
  "predictions": [[1, 12, 23, 34, 40, 49], ...],
  "bets": [{"type": "Ordinary", "numbers": [3, 10, 15, 22, 33, 41]}, ...],
  "results": {"winning": [3, 15, 22, 31, 40, 47], "additional": 28}
}
```

### GET /predict

Auto-fetches the latest draw from third-party sites, generates 6 prediction lines by default (concentrated + diverse + skew + synthesis - see section 3), and returns them with scoring details. Accepts an optional `lines` param (10-40) for jackpot mode, which generates more lines across all four strategies.

### POST /predict

Same as GET /predict but accepts manual draw input: `{"numbers": [4, 11, 15, 16, 21, 39]}`.

### POST /postmortem

Compares predictions against actual results. Returns per-line match analysis, near misses, prize group, rule pass/fail breakdown, and root cause analysis.

### GET /last-draw

Fetches the latest draw results from third-party sites (no DB involved).

### GET /health

Returns service status, version, and uptime.

---

## 3. Prediction Engine (20 Rules)

The engine generates 500,000 random 6-number combinations and scores each against 20 validated structural rules derived from historical draw analysis.

### Rule tiers

**Tier 1 — hold 93–98% of draws (weight ×3):**
- Spread ≥ 20 (difference between highest and lowest number)
- Sum between 80–220
- Numbers span 3+ ranges (1–10, 11–20, 21–30, 31–40, 41–49)
- At least one cluster gap ≤ 3 between adjacent numbers
- P1 (smallest number) ≤ 14
- P6 (largest number) ≥ 33

**Tier 2 — hold 73–88% (weight ×2):**
- 4+ grid rows (7-per-row grid)
- Repeated units digit (same last digit appears twice)
- Balance: 2–4 low numbers (≤ 25)
- 2–4 odd numbers
- Anchor: at least one ≤ 10 and one ≥ 35

**Tier 3 — hold 41–48% (weight ×1):**
- 7-apart pair exists
- Consecutive pair exists
- Complement pair (numbers summing to 50)

**Inter-draw rules — hold 70–94% (weight ×2):**
- 2+ numbers within ±3 of previous draw numbers
- 3+ numbers within ±3 of previous draw
- Zone recovery (fills ranges missed by previous draw)
- Exit replacement (new numbers ±5 of departed numbers)
- Decade carry ≥ 2 (shared decade columns with previous)
- P6 shift ≤ 8 (largest number within 8 of previous largest)

### Four strategies (`generate_all` / `generate_jackpot` in `engine.py`)

| Strategy | Default lines | Method |
|---|---|---|
| **Concentrated** | 1 | Brute-force top candidates per position, maximise filter + position score |
| **Diverse** | remainder | Top-scoring from 500K random candidates, minimal overlap between lines |
| **Skew** | 1 | Same as diverse but requires 5+ numbers on one side (<=25 or >=25); direction is learned per-draw from recent history (`skew_direction`), not fixed to "low" |
| **Synthesis** | 1 | Built strictly from numbers already appearing in the other lines (`generate_synthesis`) - no new numbers |

Jackpot mode (10-40 lines) splits proportionally across all four strategies instead of using the fixed 1/1/1/remainder split above.

---

## 4. Scheduled Jobs

All times in Singapore Time (SGT = UTC+8). Both run as Azure Functions timer triggers
(`function_app.py`); the schedules below are UTC NCRONTAB (SGT has no DST, so they're
stable). Both jobs are single-shot - the schedule itself is the retry, not an internal
sleep loop.

| Job | Schedule (SGT) | Cron (UTC) | Description |
|---|---|---|---|
| `predict_timer` -> `app/jobs/predict.py` | Mon & Thu 08:00 | `0 0 0 * * 1,4` | Fetch last draw -> check the live next-draw feed (date + jackpot) -> run engine -> store predictions |
| `results_timer` -> `app/jobs/results.py` | hourly 19:00-22:00, daily | `0 0 11-14 * * *` | Fetch results for every pending draw (no results yet, dated today or earlier) from Singapore Pools; no-ops when nothing is pending |

### Prediction job

1. Fetches the latest draw results (third-party scraper for the numbers)
2. Calls `app/jobs/scheduling.py::next_draw_info()` for the real next draw date and
   jackpot estimate - Singapore Pools' own live feed first, falling back to Mon/Thu
   calendar math (correctable via `DRAW_DATE_OVERRIDES`) only if that fetch fails.
   This is what handles a draw postponed for a public holiday.
3. Skips generating ahead if an earlier draw still has predictions but no results
4. Runs `generate_all()`, then creates or updates the target draw's record, including
   `estimated_jackpot`

### Results job

1. Finds every draw with no results dated today or earlier
2. Fetches from Singapore Pools only, no fallback sources - retries hourly for today's
   draw, one attempt per run for older pending draws (falling back to the date-specific
   lookup, since the "latest results" page has moved on for those)
3. Requires all three of winning numbers, groupwise winning shares, and the Group 1
   Prize amount before saving - no partial saves
4. Triggers `generate_next_predictions()` for the next draw once results are saved

---

## 5. Data Scraping

### Sources, by purpose

| Source | Used for |
|---|---|
| Singapore Pools (`SINGAPORE_POOLS_URL`) | **Results job - the only source.** No fallback scrapers here (hard rule). |
| Singapore Pools next-draw feed (`TOTO_NEXT_DRAW_URL`) | The real next draw date + jackpot estimate - the same small data fragment the results page itself loads via AJAX |
| Lottolyzer, Lottery Extreme | Latest-draw numbers for the prediction job, prize/backfill endpoints, and history backfill - not used for saving official results |

### Extracted data

- 6 winning numbers (1-49) + additional (bonus) number
- Draw number, draw date
- Group 1 Prize, groupwise winning shares (results job)
- Next draw date + jackpot estimate (next-draw feed)

---

## 6. Admin Panel

Protected by Supabase Auth (email + password login via `/auth/login`, which returns a bearer token the frontend attaches to admin API calls). Provides:

### Draw management
- Add/edit draws: set date, draw number, predictions, bets, results
- Delete draws
- Clear form

### Bet entry
- **Image upload + OCR**: Upload a photo of a bet slip → Claude Vision extracts bet type and numbers
- **Manual entry**: Add bets with type selector (Ordinary, System 7–12)

### Fetch / backfill buttons
- "Fetch Missing Results" - calls `POST /fetch-results`
- "Re-predict" per draw row, and separate backfill actions for prize data and Group 1
  Prize, each calling their own endpoint (section 2)

Settings (Anthropic key, data source URLs, schedules) live in Function App / environment
config (`app/utils/config.py`), not in a database `settings` table or an admin UI.

---

## 7. Database Schema

### `draws` table

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `draw_date` | DATE | Draw date |
| `draw_number` | TEXT | e.g. "3855" |
| `predictions` | JSONB | Array of 6-number arrays |
| `bets` | JSONB | Array of `{type, numbers}` objects |
| `results` | JSONB | `{winning: [...], additional: N}` |
| `created_at` | TIMESTAMPTZ | Auto-set |
| `updated_at` | TIMESTAMPTZ | Auto-updated via trigger |

See `docs/supabase_setup.sql` for the exact schema and row-level security policies.

---

## 8. Infrastructure

```
Cloudflare DNS (DNS only, not proxied) -> madebytk.com, www.madebytk.com
|
+-- Azure Static Web Apps (Free, resource group rg-madebytk-func)  -> website/
|   +-- website/config.js -> API_BASE = the Function App URL
|
+-- Azure Function App "madebytk-api" (Flex Consumption, Python 3.11, same resource group)
    +-- HTTP: FastAPI via AsgiFunctionApp - no /api prefix
    +-- Timer triggers: predict_timer, results_timer (schedules in app settings, UTC)
```

Deploy is by merging to `main` - GitHub Actions builds and deploys both apps
automatically; there is no manual deploy step or SSH access involved. See
`docs/azure_functions.md` for app settings, the platform CORS requirement, and the Flex
Consumption scale settings (these matter - the wrong values caused random request
stalls during migration).

An earlier dedicated Azure VM (Docker + nginx + APScheduler, separate from Pally's VM)
has been retired.

---

## 9. Frontend Architecture

Single HTML file with embedded CSS and JavaScript. No build step.

### Dependencies
- **Google Fonts** - Inter (UI) + JetBrains Mono (numbers)
- No Supabase JS SDK - all data access goes through the FastAPI backend, never directly to Supabase from the browser

### Data flow
- **Public view**: `GET /draws` -> render draw cards (only draws from `PUBLIC_DRAWS_CUTOFF` onward)
- **Admin panel**: login via `POST /auth/login` returns a bearer token, held in memory
  only (not persisted); all admin calls attach it via `Authorization: Bearer`, and a 401
  clears it and reopens the login prompt

---

## Pending / Known Items

- Bet slip OCR requires `ANTHROPIC_API_KEY` configured in the Function App settings
- The admin panel's "Re-predict" button calls `POST /regenerate-predictions/{date}`,
  which does not exist as a backend endpoint - this is broken today
- Results are Singapore Pools only, by design (hard rule) - if that page is unreachable
  or blocks scraping, the results job retries hourly until the daily deadline rather
  than falling back to another source
