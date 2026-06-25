# MadeByTK — Feature Specification

> **Keep this file updated** whenever a feature is added, changed, or removed.
> Last updated: 2026-06-25

---

## Overview

**MadeByTK** is a Singapore Toto prediction tracker served at madebytk.com. It generates predictions using a 20-rule structural filter engine, tracks bets placed, fetches actual draw results, and highlights matches — all displayed on a single-page website.

---

## Architecture at a Glance

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python, async) |
| Prediction Engine | Custom 20-rule filter (structural + inter-draw) |
| Database | Supabase (PostgreSQL) |
| Frontend | Static HTML + Supabase JS client |
| Hosting | Azure VM (shared with getpally.ai) |
| Scheduling | System crontab (Mon & Thu) |
| Web Server | Nginx (reverse proxy + static files) |

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

All endpoints are served by FastAPI on port 8100. Nginx proxies `/api/*` from madebytk.com to the backend.

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

Auto-fetches the latest draw from third-party sites, generates 7 prediction lines (1 concentrated + 5 diverse + 1 low-skew), and returns them with scoring details.

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

### Three strategies

| Strategy | Lines | Method |
|---|---|---|
| **Concentrated** | 1 | Brute-force top 8 candidates per position, maximise filter + position score |
| **Diverse** | 5 | Top-scoring from 500K random candidates, minimal overlap between lines |
| **Low-skew** | 1 | Same as diverse but requires 5+ numbers ≤ 25 (catches low-heavy draws) |

---

## 4. Scheduled Jobs

All times in Singapore Time (SGT = UTC+8). Cron runs on the Azure VM.

| Job | Schedule | Cron (UTC) | Description |
|---|---|---|---|
| Generate predictions | Mon & Thu 08:00 SGT | `0 0 * * 1,4` | Fetch last draw → run engine → store predictions in Supabase |
| Fetch results | Mon & Thu 22:00 SGT | `0 14 * * 1,4` | Scrape Singapore Pools → store results in Supabase |

### Prediction job (`app/jobs/predict.py`)

1. Fetches the latest draw results from third-party scraper
2. Runs `generate_all()` to produce 7 lines
3. Creates or updates the draw record for today in Supabase

### Results job (`app/jobs/results.py`)

1. Tries Singapore Pools website directly
2. Falls back to lottolyzer.com if Singapore Pools fails
3. Falls back to lotteryextreme.com as last resort
4. Creates or updates the draw record for today in Supabase

---

## 5. Data Scraping

### Sources (in priority order)

| Source | URL | Reliability |
|---|---|---|
| Singapore Pools | singaporepools.com.sg/en/product/sr/Pages/toto_results.aspx | Primary — may block scrapers |
| Lottolyzer | en.lottolyzer.com/history/singapore/toto | Fallback 1 |
| Lottery Extreme | lotteryextreme.com/singapore/toto-results | Fallback 2 |

### Extracted data

- 6 winning numbers (1–49)
- Additional number (bonus)
- Draw number (e.g. 3855)
- Draw date

---

## 6. Admin Panel

Protected by Supabase Auth (email + password login). Provides:

### Draw management
- Add/edit draws: set date, draw number, predictions, bets, results
- Delete draws
- Clear form

### Bet entry
- **Image upload + OCR**: Upload a photo of a bet slip → Claude Vision extracts bet type and numbers
- **Manual entry**: Add bets with type selector (Ordinary, System 7–12)

### Settings
- Anthropic API key (for bet slip OCR)
- Toto Predictor API endpoint URL

### Fetch buttons
- "Fetch from Predictor API" — calls the configured predictor endpoint
- "Fetch from Singapore Pools" — currently shows a CORS warning (manual entry needed from browser)

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

### `settings` table

| Column | Type | Description |
|---|---|---|
| `key` | TEXT | Primary key (e.g. "anthropic_api_key", "predictor_url") |
| `value` | TEXT | Setting value |
| `updated_at` | TIMESTAMPTZ | Auto-updated |

### Row Level Security
- **draws**: Public read, authenticated-only write/update/delete
- **settings**: Authenticated-only read and write

---

## 8. Infrastructure

### Azure VM layout

```
Azure VM
├── getpally.ai (Pally)     → port 8000, nginx, existing — UNTOUCHED
├── madebytk.com (Toto)     → port 8100, separate nginx server block
├── Nginx
│   ├── sites-available/getpally   (existing)
│   └── sites-available/madebytk   (new — does not modify existing)
└── Crontab
    ├── [existing Pally jobs]
    └── [new Toto jobs — Mon & Thu only]
```

### Systemd service

`toto-engine.service` runs as `azureuser`, starts after `pally.service`, restarts on failure. Uses `EnvironmentFile=/home/azureuser/toto/.env`.

### DNS

`madebytk.com` A record points to the Azure VM's public IP. SSL via Let's Encrypt (certbot with nginx plugin).

---

## 9. Frontend Architecture

Single HTML file with embedded CSS and JavaScript. No build step.

### Dependencies
- **Supabase JS SDK** (CDN) — used for admin auth and fallback data reads
- **Google Fonts** — Inter (UI) + JetBrains Mono (numbers)

### Data flow
- **Public view**: `GET /api/draws` → render draw cards
- **Fallback**: If API is down, reads directly from Supabase via JS client
- **Admin panel**: All writes go through Supabase JS client (authenticated)

---

## Pending / Known Items

- Singapore Pools may block direct scraping — results job has fallback sources
- Bet slip OCR requires Anthropic API key configured in admin settings
- Frontend CORS: "Fetch from Singapore Pools" button in admin can't work from the browser due to cross-origin restrictions — results must be entered manually or fetched via the server-side cron job
