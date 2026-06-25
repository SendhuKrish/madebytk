#!/usr/bin/env python3
"""Cron job: Generate predictions for the next draw and store in Supabase.

Schedule: Mon & Thu at 08:00 SGT (before the 6:30 PM draw).
Crontab:  0 0 * * 1,4 cd /home/azureuser/toto && /home/azureuser/toto/venv/bin/python -m app.jobs.predict
"""

import asyncio
import logging
import sys
from datetime import date

from app.services.engine import generate_all
from app.services.scraper import fetch_latest_draw
from app.services.db import get_draw_by_date, upsert_draw

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cron-predict")


async def main():
    today = date.today().isoformat()
    logger.info(f"Running prediction cron for {today}")

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

    # 2. Generate predictions
    concentrated, diverse, low_skew, total_passed = generate_all(last_draw)

    predictions = []
    for r in concentrated:
        predictions.append(r.pick)
    for r in diverse:
        predictions.append(r.pick)
    for r in low_skew:
        predictions.append(r.pick)

    logger.info(f"Generated {len(predictions)} prediction lines")

    # 3. Check if draw record already exists for today
    existing = get_draw_by_date(today)

    if existing:
        existing["predictions"] = predictions
        upsert_draw(existing)
        logger.info(f"Updated existing draw record for {today}")
    else:
        draw_record = {
            "draw_date": today,
            "draw_number": next_draw_number or "",
            "predictions": predictions,
            "bets": [],
            "results": {"winning": [], "additional": None},
        }
        upsert_draw(draw_record)
        logger.info(f"Created new draw record for {today}")

    logger.info("Prediction cron complete")


if __name__ == "__main__":
    asyncio.run(main())
