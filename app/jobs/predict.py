#!/usr/bin/env python3
"""Cron job: Generate predictions for the next draw and store in Supabase.

Schedule: Mon & Thu at 08:00 SGT (before the 6:30 PM draw).
Also triggered automatically after results are fetched.
"""

import asyncio
import logging
import sys
from datetime import date, timedelta

from app.services.engine import generate_all
from app.services.scraper import fetch_latest_draw
from app.services.db import (
    fetch_all_draws,
    fetch_draws_without_results,
    get_draw_by_date,
    upsert_draw,
)
from app.utils.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cron-predict")

# Map day abbreviations to weekday numbers (mon=0 ... sun=6)
_DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _next_draw_date(after: date) -> date:
    """Calculate the next draw date strictly after `after`, based on PREDICT_DAYS."""
    draw_weekdays = sorted(
        _DAY_MAP[d.strip().lower()] for d in settings.predict_days.split(",")
    )
    for offset in range(1, 8):
        candidate = after + timedelta(days=offset)
        if candidate.weekday() in draw_weekdays:
            return candidate
    # Fallback: shouldn't happen with valid config
    return after + timedelta(days=1)


def _extract_history(max_draws: int = 100) -> list[list[int]]:
    """Pull winning numbers from Supabase, newest first, for v4 learning.

    Skips rows without 6 winning numbers (pending draws).
    """
    history = []
    try:
        for d in fetch_all_draws():
            winning = (d.get("results") or {}).get("winning") or []
            if len(winning) == 6:
                history.append(sorted(int(n) for n in winning))
            if len(history) >= max_draws:
                break
    except Exception:
        logger.exception("_extract_history failed — engine will use defaults")
    return history


async def main(
    override_numbers: list[int] | None = None,
    override_date: str | None = None,
    override_draw_number: str | None = None,
):
    """Generate predictions for the next draw.

    When called from the results cron, override_* params are passed directly
    so we don't need to re-scrape external sites (which may lag behind SG Pools).
    When called standalone (scheduled cron), it scrapes as before.
    """
    today = date.today()
    logger.info(f"Running prediction cron on {today.isoformat()}")

    # 1. Get latest draw numbers — use override if provided, else scrape
    if override_numbers:
        last_draw = sorted(override_numbers)
        draw_number_str = str(override_draw_number) if override_draw_number else None
        next_draw_number = str(int(override_draw_number) + 1) if override_draw_number else None
        last_draw_date_str = override_date
        logger.info(f"Using override: last_draw={last_draw} (#{draw_number_str}) date={last_draw_date_str}")
    else:
        draw_data = await fetch_latest_draw()
        if not draw_data:
            logger.error("Could not fetch latest draw data. Aborting.")
            sys.exit(1)

        last_draw = sorted(draw_data["numbers"])
        next_draw_number = None
        if draw_data.get("draw_number"):
            next_draw_number = str(int(draw_data["draw_number"]) + 1)
        last_draw_date_str = draw_data.get("date")
        logger.info(f"Last draw: {last_draw} (#{draw_data.get('draw_number')})")

    # 2. Determine the next draw date
    last_draw_date = today
    if last_draw_date_str:
        try:
            last_draw_date = date.fromisoformat(last_draw_date_str)
        except (ValueError, TypeError):
            pass

    target_date = _next_draw_date(last_draw_date)
    target_date_str = target_date.isoformat()
    logger.info(f"Predictions target draw date: {target_date_str}")

    # Guard: don't skip ahead if an earlier draw still has no results.
    # e.g. if 02-Jul predictions exist but results aren't in yet,
    # don't create predictions for 06-Jul.
    pending = [
        d for d in fetch_draws_without_results()
        if d.get("predictions") and len(d["predictions"]) > 0
        and d["draw_date"] < target_date_str
    ]
    if pending:
        logger.info(
            f"Draw {pending[0]['draw_date']} still awaiting results — "
            f"skipping prediction for {target_date_str}"
        )
        return

    # 3. Generate predictions (v4: learn weights from full draw history)
    history = _extract_history()
    logger.info(f"Learning from {len(history)} historical draws")
    concentrated, diverse, low_skew, synthesis, total_passed = generate_all(
        last_draw, history=history,
    )

    predictions = [r.pick for group in (concentrated, diverse, low_skew, synthesis) for r in group]

    logger.info(f"Generated {len(predictions)} prediction lines")

    # 4. Save to target draw date row (merge if exists)
    existing = get_draw_by_date(target_date_str)

    if existing:
        existing["predictions"] = predictions
        if next_draw_number and not existing.get("draw_number"):
            existing["draw_number"] = next_draw_number
        upsert_draw(existing)
        logger.info(f"Updated existing draw record for {target_date_str}")
    else:
        draw_record = {
            "draw_date": target_date_str,
            "draw_number": next_draw_number or "",
            "predictions": predictions,
            "bets": [],
            "results": {"winning": [], "additional": None},
        }
        upsert_draw(draw_record)
        logger.info(f"Created new draw record for {target_date_str}")

    logger.info("Prediction cron complete")


if __name__ == "__main__":
    asyncio.run(main())
