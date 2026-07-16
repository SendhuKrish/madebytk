# Singapore Toto Prediction Engine — Project Handoff

Paste this at the start of a new chat to restore full context.

---

## What this is

I (TK) built a **self-learning statistical filter engine** for Singapore Toto (6 numbers from 1-49, draws Mon/Thu 6:30pm SGT). Stack: **FastAPI** backend on my **Azure VM** (port 8100, alongside my Pally app), **Supabase** for draw storage + auth, **React tracker UI** (predictions vs bets vs results with match highlighting), **APScheduler** jobs (auto-predict after each draw, auto-fetch results), bet-slip image extraction via Claude API, scrapers for lottolyzer/lotteryextreme/SG Pools/sglottoresult.

## The honest framing (keep this)

Draws are random — chi-square confirmed uniformity across 100 historical draws. This is a **FILTER system, not prediction**. All 20 "rules" were validated against 10,000 Monte Carlo simulated draws and turned out to be **structural properties of any random 6/49 draw** (they hold in simulation at nearly identical rates). They make picks structurally resemble real draws and reduce jackpot-splitting (anti-popular numbers), but odds remain 1 in 13,983,816 per line. Two mild anomalies watched: complement pairs (N + 50-N together, ~13% above chance) and units-digit-6 persistence.

## How we got here

1. Started from a Tesla 3-6-9 / lucky-number question → debunked with data (369 vortex groups underperformed expectation)
2. Analyzed 100 real draws: frequency, pairs, odd/even, chi-square (χ²=31.77 vs critical 65.17 → uniform)
3. Found high-frequency structural patterns; validated ALL against Monte Carlo → structural, not predictive
4. Built the 20-rule filter engine, then iterated via post-mortem after every draw

## The 20 rules (weight × name, historical hold-rate %)

- **Tier 1 (×3):** Spread≥20 (98), Sum 80-220 (97), 3+ ranges (95), Cluster gap≤3 (94), P1≤14 (94), P6≥33 (93)
- **Tier 2 (×2):** 4+ grid rows of 7 (88), Repeated units digit (84), Balance 2-4 low≤25 (82), 2-4 odd (81), Anchor ≤10 AND ≥35 (73)
- **Tier 3 (×1):** 7-apart pair (48), Consecutive pair (45), Complement N+50-N (41)
- **Inter-draw (×2, P6shift ×1):** 2+ numbers within ±3 of prev draw (94), 3+ within ±3 (76), Missing-zone recovery (76), Exits replaced within ±5 (71), Decade carry ≥2 (90), P6 shift ≤8 (70)
- Max score 41; MIN_FILTER_SCORE=38 for diverse, 36 for skew lines.

## Strategies — standard mode (6 lines, $6)

1. **★ Concentrated (1):** brute-force top-8 candidates per position. POS_RANGES = [(1,14),(2,24),(8,32),(14,38),(20,44),(33,49)]. score_position rewards: ±1/±3 proximity to prev draw's same position (5/3 pts), neighborhood echo ±2/±3 to ANY prev number (3/2), exact repeat (+2), complement potential (+1), hot number (+1). **Mean reversion (v3.1):** for P2-P5 only, when prev deviates >3 from learned average → proximity weight halved, reversion bonus up to +6 toward the average. P1/P6 stay anchored.
2. **Diverse (3):** 500K random candidates, score≥38, overlap≤1 between lines.
3. **▼ Low-skew (1):** 5+ numbers ≤25 (catches low-packed draws the Balance rule rejects, ~3.5% of draws).
4. **◆ Synthesis (1):** built STRICTLY from numbers already in lines 1-5 (hard requirement — no outside numbers). Consensus frequency ×5 + bonus 10/number appearing in 2+ lines; must differ from concentrated by ≥3 numbers; pool capped at top-18 by consensus.

## v4 self-learning (auto-adapts every prediction)

`learn_parameters(history)` — history is newest-first winning numbers from Supabase (`extract_history()` in main.py reads `d["results"]["winning"]` via `fetch_all_draws()`):
- **POS_AVERAGES** = 60% long-term average (all draws) + 40% last-8-draw average
- **HOT_NUMBERS** = numbers appearing 2+ times in last 8 draws
- Falls back to static defaults on empty/tiny history; learned params logged each run.

## Jackpot mode (v4.1) — for snowballed pots

`generate_jackpot(last_draw, seed, history, n_lines=30)` (clamp 10-40): ~10% concentrated (top-3), ~13% low-skew, ~13% **high-skew** (5+ numbers ≥25 — added after draw #4198 came in 1L/5H sum 208 and broke P1≤14), ~7% synthesis, remainder diverse in waves of 8 (fresh exclusion per wave; 120K candidates/wave, 150K for skews to keep runtime ~28s). API: `GET /predict?lines=30` or `POST /predict {"numbers":[...], "lines":30}`. Standard mode and the scheduler job are untouched.

## Performance record (post-mortems after every draw)

- Consistent shape: **all 6 winning numbers usually covered ACROSS the lines**, individual lines land 1-3 exact; misses are typically ±1 ("right neighborhood, wrong grouping" — the unfixable last mile, and I understand why).
- #4194: concentrated hugged an extreme-low prev draw while the draw mean-reverted up → added P2-P5 mean reversion.
- #4198 [23,27,31,38,42,47] sum 208: high-skew shape → added high-skew strategy.
- #4199 [14,22,32,33,36,46]+42 ($12.8M, won by 2 others): our 30-line jackpot set → **3× Group 7 wins, $30 won on $30 spent (break-even)**, 6/6 + bonus covered. What-if: {22,33,46} and {32,36,46} sat in different lines; combined would've been Group 3.
- Recent sum regime: 101→106→146→127→155→189→208→183 (running high).

## Files

`app/services/engine.py` (core, v4.1), `main.py` (FastAPI: GET/POST /predict with lines param, /postmortem, /draws CRUD, /fetch-results, /backfill-prizes, /backfill-g1prize, /extract-bets OCR, /auth/login), `app/services/scraper.py`, `app/services/db.py` (Supabase), `app/utils/config.py` (.env.config + .env.secret), `app/jobs/predict.py` + `results.py` (scheduled).

## Working conventions

- After each draw: post-mortem (rule check on actual, line-by-line matching, root cause). Propose engine changes **only when a failure shape repeats** — never overfit to one draw.
- Standard 6 lines for normal pots; jackpot mode (25-40 lines) only when the pot snowballs.
- Always keep the honest framing: filters + coverage + payout optimization, never "prediction."
- Latest known draw: #4199 (Mon 13 Jul 2026). Next: #4200 Thu 16 Jul 2026, pot reset ~$1M.
