"""Singapore Toto Smart Filter Engine — FastAPI Service.

Endpoints:
    GET  /health              — Health check
    GET  /draws               — All draws from Supabase (descending)
    GET  /predict             — Auto-fetch latest draw, generate predictions
    POST /predict             — Manual draw input, generate predictions
    POST /postmortem          — Compare predictions against actual results
    GET  /last-draw           — Fetch latest draw results only
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from app.utils.config import settings
from app.services.engine import (
    generate_all,
    run_postmortem,
    score_filter,
    score_position,
)
from app.models import (
    HealthResponse,
    PickLine,
    PostMortemLine,
    PostMortemResponse,
    PredictionResponse,
    RuleResult,
)
from app.services.scraper import fetch_latest_draw
from app.services.db import fetch_all_draws

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("toto-api")

START_TIME = time.time()
SGT = pytz.timezone(settings.tz)


def _run_predict_job():
    """Wrapper to run the async predict job from APScheduler."""
    from app.jobs.predict import main as predict_main
    logger.info("Scheduler: running prediction job")
    asyncio.run(predict_main())


def _run_results_job():
    """Wrapper to run the async results job from APScheduler."""
    from app.jobs.results import main as results_main
    logger.info("Scheduler: running results job")
    asyncio.run(results_main())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown events."""
    logger.info("Toto Engine API starting up")

    # Schedule jobs — Mon & Thu, SGT
    scheduler = BackgroundScheduler(timezone=SGT)
    scheduler.add_job(
        _run_predict_job,
        CronTrigger(
            day_of_week=settings.predict_days,
            hour=settings.predict_hour,
            minute=settings.predict_minute,
            timezone=SGT,
        ),
        id="predict",
        name="Generate predictions",
    )
    scheduler.add_job(
        _run_results_job,
        CronTrigger(
            day_of_week=settings.results_days,
            hour=settings.results_hour,
            minute=settings.results_minute,
            timezone=SGT,
        ),
        id="results",
        name="Fetch draw results",
    )
    scheduler.start()
    logger.info(
        f"Scheduler started — predict {settings.predict_days} "
        f"{settings.predict_hour:02d}:{settings.predict_minute:02d}, "
        f"results {settings.results_days} "
        f"{settings.results_hour:02d}:{settings.results_minute:02d} ({settings.tz})"
    )

    yield

    scheduler.shutdown()
    logger.info("Toto Engine API shutting down")


app = FastAPI(
    title="Singapore Toto Smart Filter Engine",
    description="20 validated structural + inter-draw rules · Concentrated + Diverse + Low-Skew modes",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ──────────────────────────────────────────────────


class ManualPredictRequest(BaseModel):
    """Manual prediction request with explicit last draw."""
    numbers: list[int] = Field(
        ..., min_length=6, max_length=6,
        description="Last draw: 6 numbers between 1-49",
    )
    seed: int | None = Field(None, description="Random seed for reproducibility")


class PostMortemRequest(BaseModel):
    """Post-mortem request."""
    previous_draw: list[int] = Field(..., min_length=6, max_length=6)
    actual_winning: list[int] = Field(..., min_length=6, max_length=6)
    actual_bonus: int | None = None
    predictions: list[list[int]] = Field(
        ..., description="List of predicted lines to evaluate",
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _build_pick_line(
    result,
    line_num: int,
    strategy: str,
    last_draw: list[int],
) -> PickLine:
    """Convert a ScoredPick into a PickLine response."""
    pick = result.pick
    ps = set(pick)
    near = [c for c in pick if any(0 < abs(c - p) <= 3 for p in last_draw)]
    comp = any(50 - n in ps and 50 - n != n for n in pick)
    consec = [
        [pick[j], pick[j + 1]]
        for j in range(5)
        if pick[j + 1] - pick[j] == 1
    ]

    pos_logic = None
    if strategy == "concentrated":
        pos_logic = []
        for i, n in enumerate(pick):
            sc, reasons = score_position(n, i, last_draw)
            pos_logic.append({
                "position": i + 1,
                "number": n,
                "score": sc,
                "reasons": reasons,
            })

    return PickLine(
        line_number=line_num,
        numbers=pick,
        score=result.filter_score,
        total_score=result.total_score if strategy == "concentrated" else None,
        sum_total=result.sum_total,
        odd_count=result.odd_count,
        even_count=6 - result.odd_count,
        low_count=result.low_count,
        high_count=6 - result.low_count,
        near_prev_count=result.nb3,
        near_prev_numbers=near,
        has_complement=comp,
        consecutive_pairs=consec,
        strategy=strategy,
        position_logic=pos_logic,
    )


def _validate_draw(numbers: list[int]) -> None:
    """Validate draw numbers."""
    if len(numbers) != 6:
        raise HTTPException(400, "Exactly 6 numbers required")
    if not all(1 <= n <= 49 for n in numbers):
        raise HTTPException(400, "Numbers must be between 1-49")
    if len(set(numbers)) != 6:
        raise HTTPException(400, "Numbers must be unique")


# ── Endpoints ───────────────────────────────────────────────────────


@app.get("/draws")
async def get_draws():
    """Fetch all draws from Supabase, ordered by draw_date descending."""
    return fetch_all_draws()


@app.get("/health", response_model=HealthResponse)
async def health():
    """Health check."""
    return HealthResponse(uptime_seconds=time.time() - START_TIME)


@app.get("/last-draw")
async def get_last_draw():
    """Fetch latest Singapore Toto draw results."""
    result = await fetch_latest_draw()
    if not result:
        raise HTTPException(503, "Could not fetch latest draw results")
    return result


@app.get("/predict", response_model=PredictionResponse)
async def predict_auto(seed: int | None = None):
    """Auto-fetch latest draw and generate predictions."""
    draw_data = await fetch_latest_draw()
    if not draw_data:
        raise HTTPException(
            503,
            "Could not fetch latest draw. Use POST /predict with manual input.",
        )

    last_draw = sorted(draw_data["numbers"])
    return _generate_predictions(
        last_draw=last_draw,
        seed=seed,
        draw_number=draw_data.get("draw_number"),
        last_draw_date=draw_data.get("date"),
    )


@app.post("/predict", response_model=PredictionResponse)
async def predict_manual(req: ManualPredictRequest):
    """Generate predictions from manually provided last draw."""
    _validate_draw(req.numbers)
    last_draw = sorted(req.numbers)
    return _generate_predictions(last_draw=last_draw, seed=req.seed)


def _generate_predictions(
    last_draw: list[int],
    seed: int | None = None,
    draw_number: int | None = None,
    last_draw_date: str | None = None,
) -> PredictionResponse:
    """Core prediction logic."""
    logger.info(f"Generating predictions for last_draw={last_draw}")

    concentrated, diverse, low_skew, total_passed = generate_all(
        last_draw, seed=seed,
    )

    lines = []
    line_num = 1

    for r in concentrated:
        lines.append(_build_pick_line(r, line_num, "concentrated", last_draw))
        line_num += 1

    for r in diverse:
        lines.append(_build_pick_line(r, line_num, "diverse", last_draw))
        line_num += 1

    for r in low_skew:
        lines.append(_build_pick_line(r, line_num, "low_skew", last_draw))
        line_num += 1

    all_nums = set()
    for line in lines:
        all_nums.update(line.numbers)

    next_draw = (draw_number + 1) if draw_number else None

    return PredictionResponse(
        draw_number=next_draw,
        draw_date=None,
        last_draw=last_draw,
        last_draw_date=last_draw_date,
        lines=lines,
        coverage=len(all_nums),
        candidates_passed=total_passed,
    )


@app.post("/postmortem", response_model=PostMortemResponse)
async def postmortem(req: PostMortemRequest):
    """Run post-mortem analysis."""
    _validate_draw(req.previous_draw)
    _validate_draw(req.actual_winning)

    result = run_postmortem(
        prev_draw=sorted(req.previous_draw),
        actual=sorted(req.actual_winning),
        predictions=req.predictions,
        bonus=req.actual_bonus,
    )

    # Build rule results
    full_result = score_filter(sorted(req.actual_winning), sorted(req.previous_draw))
    rule_names = [
        ("Spread>=20", 98), ("Sum80-220", 97), ("3+ranges", 95),
        ("Cluster", 94), ("P1<=14", 94), ("P6>=33", 93),
        ("4+rows", 88), ("RepUnits", 84), ("Balance", 82),
        ("Odd2-4", 81), ("Anchor", 73), ("7apart", 48),
        ("Consec", 45), ("Complement", 41), ("2+nb3", 94),
        ("3+nb3", 76), ("ZoneRecov", 76), ("Replace5", 71),
        ("DecCarry2", 90), ("P6shift8", 70),
    ]
    rule_results = [
        RuleResult(
            name=name,
            passed=name not in full_result.fails,
            hold_rate=rate,
            detail="passed" if name not in full_result.fails else "failed",
        )
        for name, rate in rule_names
    ]

    line_results = [
        PostMortemLine(**lr) for lr in result["line_results"]
    ]

    return PostMortemResponse(
        previous_draw=result["previous_draw"],
        actual_winning=result["actual_winning"],
        actual_bonus=result["actual_bonus"],
        rules_passed=result["rules_passed"],
        rules_total=result["rules_total"],
        rule_results=rule_results,
        line_results=line_results,
        numbers_we_covered=result["numbers_we_covered"],
        numbers_we_missed=result["numbers_we_missed"],
        root_cause=result["root_cause"],
    )
