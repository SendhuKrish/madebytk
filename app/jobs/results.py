#!/usr/bin/env python3
"""Cron job: Fetch actual draw results and store in Supabase.

Schedule: Mon & Thu at 19:00 SGT (after the 6:30 PM draw).
Retries hourly until ALL required data is available or deadline is reached.
No partial saves — all three fields must be present.
After results are saved, auto-generates predictions for the next draw.

Source: Singapore Pools results page only (no fallbacks).
Required before saving:
  1. Winning numbers (6 numbers)
  2. Groupwise winning shares (Groups 1-7 with amounts and winner counts)
  3. Group 1 Prize amount
"""

import asyncio
import logging
import sys
from datetime import date, datetime, timedelta

import pytz

from app.services.db import get_draw_by_date, upsert_draw
from app.services.scraper import fetch_sg_pools_results
from app.utils.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cron-results")


async def _fetch_results(draw_date: str) -> dict | None:
    """Fetch results from Singapore Pools only.

    Returns structured dict or None if SG Pools doesn't have this draw yet.
    """
    sg = await fetch_sg_pools_results()
    if not sg or not sg.get("numbers") or sg.get("date") != draw_date:
        return None

    return {
        "winning": sg["numbers"],
        "additional": sg["bonus"],
        "draw_number": str(sg["draw_number"]) if sg.get("draw_number") else None,
        "draw_date": sg.get("date"),
        "prizes": sg.get("prizes", []),
        "group1_prize": sg.get("group1_prize"),
        "snowball_amount": sg.get("snowball_amount"),
    }


def _results_complete(result: dict) -> tuple[bool, str]:
    """Check all three required data points are present.

    Returns (True, "") if complete, or (False, reason) if something is missing.
    No partial saves — all three must be present.
    """
    # 1. Winning numbers
    if not result.get("winning") or len(result["winning"]) != 6:
        return False, "winning numbers"

    # 2. Groupwise winning shares (Groups 1-7)
    if not result.get("prizes") or len(result["prizes"]) == 0:
        return False, "groupwise winning shares"

    # 3. Group 1 Prize amount
    if not result.get("group1_prize"):
        return False, "Group 1 Prize amount"

    return True, ""


def _next_draw_date(draw_date_str: str) -> str:
    """Get the next Toto draw date (Mon/Thu) after the given date."""
    dt = datetime.strptime(draw_date_str, "%Y-%m-%d")
    weekday = dt.weekday()  # 0=Mon, 3=Thu
    if weekday == 0:    # Monday -> Thursday
        delta = 3
    elif weekday == 3:  # Thursday -> Monday
        delta = 4
    else:
        # Off-schedule draw: find next Mon or Thu
        delta = 1
        while True:
            nxt = dt + timedelta(days=delta)
            if nxt.weekday() in (0, 3):
                break
            delta += 1
    return (dt + timedelta(days=delta)).strftime("%Y-%m-%d")


def _save_results(today: str, result: dict) -> None:
    """Save results into the draw record for today."""
    existing = get_draw_by_date(today)

    results_data = {
        "winning": result["winning"],
        "additional": result["additional"],
        "prizes": result.get("prizes", []),
        "group1_prize": result.get("group1_prize"),
    }

    if existing:
        existing["results"] = results_data
        if result.get("draw_number") and not existing.get("draw_number"):
            existing["draw_number"] = result["draw_number"]
        upsert_draw(existing)
        logger.info(f"Updated results on existing draw for {today}")
    else:
        upsert_draw({
            "draw_date": today,
            "draw_number": result.get("draw_number") or "",
            "predictions": [],
            "bets": [],
            "results": results_data,
        })
        logger.info(f"Created new draw record with results for {today}")

    # ── Set estimated jackpot on the next draw ──
    next_date = _next_draw_date(today)
    next_draw = get_draw_by_date(next_date)

    snowball = result.get("snowball_amount")
    group1_prize = result.get("group1_prize")
    prizes = result.get("prizes", [])
    g1 = next((p for p in prizes if p.get("group") == 1), None)

    if snowball:
        estimated = snowball
        logger.info(f"Snowball: ${snowball:,} -> estimated_jackpot on {next_date}")
    elif g1 and g1.get("winners", 0) == 0 and group1_prize:
        # Group 1 not won, no explicit snowball text — use group1_prize
        estimated = group1_prize
        logger.info(f"Group 1 not won: ${group1_prize:,} -> estimated_jackpot on {next_date}")
    else:
        # Group 1 was won — next draw starts at minimum $1,000,000
        estimated = 1_000_000

    if next_draw:
        next_results = next_draw.get("results") or {}
        next_results["estimated_jackpot"] = estimated
        next_draw["results"] = next_results
        upsert_draw(next_draw)
    else:
        upsert_draw({
            "draw_date": next_date,
            "predictions": [],
            "bets": [],
            "results": {"estimated_jackpot": estimated},
        })


async def generate_next_predictions(draw_date: str, winning: list[int], draw_number: str | None = None) -> None:
    """Generate predictions for the next draw using the given winning numbers.

    Called after results are saved — either by the cron job or by manual
    endpoints. Passes data directly so we don't re-scrape external sites.
    """
    from app.jobs.predict import main as predict_main

    winning = sorted(winning)
    logger.info(f"Generating predictions for next draw using {draw_date} winning numbers {winning}")
    await predict_main(
        override_numbers=winning,
        override_date=draw_date,
        override_draw_number=draw_number,
    )


async def main():
    """Fetch results, retry hourly until all data is complete."""
    today = date.today().isoformat()
    tz = pytz.timezone(settings.tz)
    retry_until = settings.results_retry_until_hour
    retry_interval = settings.results_retry_interval_min

    logger.info(f"Running results cron for {today} (retry until {retry_until}:00, every {retry_interval}min)")

    while True:
        result = await _fetch_results(today)

        if result:
            complete, missing = _results_complete(result)
            if complete:
                break
            logger.warning(f"Incomplete results — missing: {missing}")
        else:
            logger.warning("No results from Singapore Pools yet")

        now = datetime.now(tz)
        if now.hour >= retry_until:
            logger.error(
                f"Deadline reached ({retry_until}:00) with incomplete data"
                f"{' — missing: ' + missing if result else ' — no results at all'}. "
                f"Will retry on next scheduled run or manual trigger."
            )
            sys.exit(1)

        logger.info(f"Retrying in {retry_interval} minutes (until {retry_until}:00 {settings.tz})...")
        await asyncio.sleep(retry_interval * 60)

    logger.info(f"Results: {result['winning']} +{result['additional']}")
    _save_results(today, result)
    await generate_next_predictions(today, result["winning"], result.get("draw_number"))
    logger.info("Results cron complete")


if __name__ == "__main__":
    asyncio.run(main())
