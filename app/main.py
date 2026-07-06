"""Singapore Toto Smart Filter Engine — FastAPI Service."""

import asyncio
import base64
import json
import logging
import re
import time
from contextlib import asynccontextmanager

import anthropic
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.utils.config import settings
from app.services.engine import generate_all, run_postmortem, score_filter, score_position
from app.utils.models import (
    HealthResponse, PickLine, PostMortemLine,
    PostMortemResponse, PredictionResponse, RuleResult,
)
from app.services.scraper import fetch_latest_draw, fetch_lottolyzer_history, fetch_lottery_extreme_prizes
from app.services.db import (
    delete_draw_by_id, fetch_all_draws, fetch_draws_without_results,
    get_draw_by_date, sign_in_user, upsert_draw,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("toto-api")

START_TIME = time.time()
SGT = pytz.timezone(settings.tz)

# ── Rule definitions for post-mortem (name, historical hold-rate %) ──

RULE_HOLD_RATES = [
    ("Spread>=20", 98), ("Sum80-220", 97), ("3+ranges", 95),
    ("Cluster", 94), ("P1<=14", 94), ("P6>=33", 93),
    ("4+rows", 88), ("RepUnits", 84), ("Balance", 82),
    ("Odd2-4", 81), ("Anchor", 73), ("7apart", 48),
    ("Consec", 45), ("Complement", 41), ("2+nb3", 94),
    ("3+nb3", 76), ("ZoneRecov", 76), ("Replace5", 71),
    ("DecCarry2", 90), ("P6shift8", 70),
]

BET_SLIP_PROMPT = (
    "Extract all Singapore Toto bet information from this bet slip image.\n\n"
    "For each bet, identify:\n"
    "1. The bet type: \"Ordinary\" (6 numbers), \"System 7\" (7 numbers), "
    "\"System 8\" (8 numbers), etc.\n"
    "2. The numbers selected.\n\n"
    "Also extract the draw date and draw number if visible.\n\n"
    "Return ONLY valid JSON in this exact format, no other text:\n"
    '{\n'
    '  "draw_date": "2026-06-26",\n'
    '  "draw_number": "3850",\n'
    '  "bets": [\n'
    '    {"type": "Ordinary", "numbers": [1, 12, 23, 34, 40, 49]},\n'
    '    {"type": "System 7", "numbers": [3, 10, 15, 22, 33, 41, 48]}\n'
    '  ]\n'
    '}\n'
    "If draw date or draw number is not visible, set them to null."
)


# ── Scheduler ────────────────────────────────────────────────────────


def _run_predict_job():
    from app.jobs.predict import main as predict_main
    logger.info("Scheduler: running prediction job")
    asyncio.run(predict_main())


def _run_results_job():
    from app.jobs.results import main as results_main
    logger.info("Scheduler: running results job")
    asyncio.run(results_main())


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler(timezone=SGT)
    scheduler.add_job(
        _run_predict_job,
        CronTrigger(day_of_week=settings.predict_days, hour=settings.predict_hour,
                     minute=settings.predict_minute, timezone=SGT),
        id="predict", name="Generate predictions",
    )
    scheduler.add_job(
        _run_results_job,
        CronTrigger(day_of_week=settings.results_days, hour=settings.results_hour,
                     minute=settings.results_minute, timezone=SGT),
        id="results", name="Fetch results + auto-predict",
    )
    scheduler.start()
    logger.info(
        f"Scheduler started — predict {settings.predict_days} "
        f"{settings.predict_hour:02d}:{settings.predict_minute:02d}, "
        f"results {settings.results_days} "
        f"{settings.results_hour:02d}:{settings.results_minute:02d} "
        f"(retry until {settings.results_retry_until_hour:02d}:00) ({settings.tz})"
    )
    yield
    scheduler.shutdown()
    logger.info("Toto Engine API shutting down")


# ── App ──────────────────────────────────────────────────────────────


app = FastAPI(
    title="Singapore Toto Smart Filter Engine",
    version="4.0.0",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Request models ───────────────────────────────────────────────────


class ManualPredictRequest(BaseModel):
    numbers: list[int] = Field(..., min_length=6, max_length=6)
    seed: int | None = None


class PostMortemRequest(BaseModel):
    previous_draw: list[int] = Field(..., min_length=6, max_length=6)
    actual_winning: list[int] = Field(..., min_length=6, max_length=6)
    actual_bonus: int | None = None
    predictions: list[list[int]]


class LoginRequest(BaseModel):
    email: str
    password: str


# ── Helpers ──────────────────────────────────────────────────────────


def _validate_draw(numbers: list[int]) -> None:
    if len(numbers) != 6:
        raise HTTPException(400, "Exactly 6 numbers required")
    if not all(1 <= n <= 49 for n in numbers):
        raise HTTPException(400, "Numbers must be between 1-49")
    if len(set(numbers)) != 6:
        raise HTTPException(400, "Numbers must be unique")


def _build_pick_line(result, line_num: int, strategy: str, last_draw: list[int]) -> PickLine:
    pick = result.pick
    ps = set(pick)

    pos_logic = None
    if strategy == "concentrated":
        pos_logic = [
            {"position": i + 1, "number": n, "score": sc, "reasons": reasons}
            for i, n in enumerate(pick)
            for sc, reasons in [score_position(n, i, last_draw)]
        ]

    return PickLine(
        line_number=line_num,
        numbers=pick,
        score=result.filter_score,
        total_score=result.total_score if strategy in ("concentrated", "synthesis") else None,
        sum_total=result.sum_total,
        odd_count=result.odd_count,
        even_count=6 - result.odd_count,
        low_count=result.low_count,
        high_count=6 - result.low_count,
        near_prev_count=result.nb3,
        near_prev_numbers=[c for c in pick if any(0 < abs(c - p) <= 3 for p in last_draw)],
        has_complement=any(50 - n in ps and 50 - n != n for n in pick),
        consecutive_pairs=[[pick[j], pick[j + 1]] for j in range(5) if pick[j + 1] - pick[j] == 1],
        strategy=strategy,
        position_logic=pos_logic,
    )


def _generate_predictions(
    last_draw: list[int],
    seed: int | None = None,
    draw_number: int | None = None,
    last_draw_date: str | None = None,
) -> PredictionResponse:
    logger.info(f"Generating predictions for last_draw={last_draw}")

    concentrated, diverse, low_skew, synthesis, total_passed = generate_all(last_draw, seed=seed)

    strategies = [
        (concentrated, "concentrated"),
        (diverse, "diverse"),
        (low_skew, "low_skew"),
        (synthesis, "synthesis"),
    ]
    lines = []
    for group, strategy in strategies:
        for r in group:
            lines.append(_build_pick_line(r, len(lines) + 1, strategy, last_draw))

    return PredictionResponse(
        draw_number=(draw_number + 1) if draw_number else None,
        last_draw=last_draw,
        last_draw_date=last_draw_date,
        lines=lines,
        coverage=len({n for line in lines for n in line.numbers}),
        candidates_passed=total_passed,
    )


# ── Endpoints ────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(uptime_seconds=time.time() - START_TIME)


@app.get("/draws")
async def get_draws():
    return fetch_all_draws()


@app.get("/last-draw")
async def get_last_draw():
    result = await fetch_latest_draw()
    if not result:
        raise HTTPException(503, "Could not fetch latest draw results")
    return result


@app.get("/predict", response_model=PredictionResponse)
async def predict_auto(seed: int | None = None):
    draw_data = await fetch_latest_draw()
    if not draw_data:
        raise HTTPException(503, "Could not fetch latest draw. Use POST /predict with manual input.")
    return _generate_predictions(
        last_draw=sorted(draw_data["numbers"]),
        seed=seed,
        draw_number=draw_data.get("draw_number"),
        last_draw_date=draw_data.get("date"),
    )


@app.post("/predict", response_model=PredictionResponse)
async def predict_manual(req: ManualPredictRequest):
    _validate_draw(req.numbers)
    return _generate_predictions(last_draw=sorted(req.numbers), seed=req.seed)


@app.post("/postmortem", response_model=PostMortemResponse)
async def postmortem(req: PostMortemRequest):
    _validate_draw(req.previous_draw)
    _validate_draw(req.actual_winning)

    prev, actual = sorted(req.previous_draw), sorted(req.actual_winning)
    result = run_postmortem(prev_draw=prev, actual=actual, predictions=req.predictions, bonus=req.actual_bonus)
    full_result = score_filter(actual, prev)

    return PostMortemResponse(
        previous_draw=result["previous_draw"],
        actual_winning=result["actual_winning"],
        actual_bonus=result["actual_bonus"],
        rules_passed=result["rules_passed"],
        rules_total=result["rules_total"],
        rule_results=[
            RuleResult(name=name, passed=name not in full_result.fails,
                       hold_rate=rate, detail="passed" if name not in full_result.fails else "failed")
            for name, rate in RULE_HOLD_RATES
        ],
        line_results=[PostMortemLine(**lr) for lr in result["line_results"]],
        numbers_we_covered=result["numbers_we_covered"],
        numbers_we_missed=result["numbers_we_missed"],
        root_cause=result["root_cause"],
    )


@app.post("/extract-bets")
async def extract_bets(image: UploadFile = File(...)):
    b64 = base64.b64encode(await image.read()).decode()
    media_type = image.content_type or "image/jpeg"
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    try:
        response = client.messages.create(
            model=settings.claude_model,
            max_tokens=settings.claude_max_tokens,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": BET_SLIP_PROMPT},
            ]}],
        )
        text = response.content[0].text
        match = re.search(r'\{[\s\S]*\}', text)
        if not match:
            raise HTTPException(422, "Could not parse response from Claude")
        return json.loads(match.group())

    except anthropic.APIError as e:
        raise HTTPException(502, f"Claude API error: {e.message}")
    except json.JSONDecodeError:
        raise HTTPException(422, "Could not parse JSON from Claude response")


# ── Auth ─────────────────────────────────────────────────────────────


@app.post("/auth/login")
async def login(req: LoginRequest):
    try:
        return sign_in_user(req.email, req.password)
    except ValueError as e:
        raise HTTPException(401, str(e))


# ── Draw CRUD ────────────────────────────────────────────────────────


@app.post("/draws")
async def save_draw(draw: dict):
    return upsert_draw(draw)


@app.delete("/draws/{draw_id}")
async def remove_draw(draw_id: str):
    if not delete_draw_by_id(draw_id):
        raise HTTPException(404, "Draw not found")
    return {"message": "Deleted"}


@app.post("/fetch-results")
async def fetch_missing_results():
    draws = fetch_draws_without_results()
    if not draws:
        return {"message": "All draws already have results", "updated": 0}

    history = await fetch_lottolyzer_history()
    if not history:
        raise HTTPException(503, "Could not fetch historical results")

    by_date = {r["draw_date"]: r for r in history if r.get("draw_date")}
    by_number = {r["draw_number"]: r for r in history if r.get("draw_number")}

    updated = 0
    for draw in draws:
        match = by_date.get(draw["draw_date"]) or by_number.get(draw.get("draw_number"))
        if match:
            draw["results"] = {"winning": match["winning"], "additional": match["additional"]}
            if match.get("draw_number") and not draw.get("draw_number"):
                draw["draw_number"] = match["draw_number"]
            # Also try to fetch prizes
            le_data = await fetch_lottery_extreme_prizes(draw["draw_date"])
            if le_data and le_data.get("prizes"):
                draw["results"]["prizes"] = le_data["prizes"]
                draw["results"]["jackpot"] = le_data.get("jackpot")
            upsert_draw(draw)
            updated += 1
            await asyncio.sleep(0.5)

    return {"message": f"Updated {updated} draw(s) with results", "updated": updated}


@app.post("/backfill-prizes")
async def backfill_prizes():
    """Fetch prize data from Lottery Extreme for all draws missing it.

    Also corrects winning numbers if they differ from Lottery Extreme.
    """
    draws = fetch_all_draws()
    updated = 0
    failed = 0

    for draw in draws:
        draw_date = draw.get("draw_date")
        results = draw.get("results") or {}
        has_winning = results.get("winning") and len(results.get("winning", [])) > 0
        has_prizes = results.get("prizes") and len(results.get("prizes", [])) > 0

        if not has_winning or has_prizes:
            continue

        le_data = await fetch_lottery_extreme_prizes(draw_date)
        if not le_data:
            failed += 1
            continue

        changed = False

        if le_data.get("prizes"):
            results["prizes"] = le_data["prizes"]
            results["jackpot"] = le_data.get("jackpot")
            changed = True

        # Correct winning numbers if they differ
        if le_data.get("numbers"):
            le_winning = sorted(le_data["numbers"])
            current_winning = sorted(results.get("winning", []))
            if le_winning != current_winning:
                results["winning"] = le_winning
                changed = True
            if le_data.get("bonus") and le_data["bonus"] != results.get("additional"):
                results["additional"] = le_data["bonus"]
                changed = True

        if le_data.get("draw_number") and not draw.get("draw_number"):
            draw["draw_number"] = str(le_data["draw_number"])
            changed = True

        if changed:
            draw["results"] = results
            upsert_draw(draw)
            updated += 1

        await asyncio.sleep(0.5)

    return {"message": f"Backfilled {updated} draw(s) with prizes ({failed} failed)", "updated": updated, "failed": failed}
