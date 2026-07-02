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
from app.services.db import get_draw_by_date, upsert_draw
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


async def main():
    today = date.today()
    logger.info(f"Running prediction cron on {today.isoformat()}")

    # 1. Fetch latest draw results to use as input
    draw_data = await fetch_latest_draw()
    if not draw_data:
        logger.error("Could not fetch latest draw data. Aborting.")
        sys.exit(1)

    last_draw = sorted(draw_data["numbers"])
    next_draw_number = None
    if draw_data.get("draw_number"):
        next_draw_number = str(int(draw_data["draw_number"]) + 1)

    logger.info(f"Last draw: {last_draw} (#{draw_data.get('draw_number')})")

    # 2. Determine the next draw date
    # If the latest scraped draw is today, predictions are for the next draw day.
    # If the latest scraped draw is in the past, predictions are for the next
    # draw day after that date.
    last_draw_date = today
    if draw_data.get("date"):
        try:
            last_draw_date = date.fromisoformat(draw_data["date"])
        except (ValueError, TypeError):
            pass

    target_date = _next_draw_date(last_draw_date)
    target_date_str = target_date.isoformat()
    logger.info(f"Predictions target draw date: {target_date_str}")

    # 3. Generate predictions
    concentrated, diverse, low_skew, synthesis, total_passed = generate_all(last_draw)

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
