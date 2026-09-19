#!/usr/bin/env python3
"""Cron job: Fetch actual draw results and store in Supabase.

Schedule: hourly, RESULTS_HOUR to RESULTS_RETRY_UNTIL_HOUR SGT, daily. Each run
is a single attempt (the schedule is the retry), so it works under any
scheduler. Acts only on pending draws (no results yet, dated today or
earlier), so postponed draws are handled automatically. The last run of the
day raises if today's draw still has no complete results.
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
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.jobs.scheduling import next_draw_info
from app.services.db import fetch_draws_without_results, get_draw_by_date, upsert_draw
from app.services.scraper import fetch_sg_pools_results, fetch_sg_pools_results_by_date
from app.utils.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cron-results")


async def _fetch_results(draw_date: str, today: str) -> dict | None:
    """Fetch results from Singapore Pools only.

    Uses the latest-results page; for an older pending draw that page has
    moved on, so fall back to the date-specific postback.
    Returns structured dict or None if SG Pools doesn't have this draw yet.
    """
    sg = await fetch_sg_pools_results()
    if (not sg or sg.get("date") != draw_date) and draw_date < today:
        sg = await fetch_sg_pools_results_by_date(draw_date)
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


async def _save_results(today: str, result: dict) -> None:
    """Save results into the draw record for `today` (the draw's own date)."""
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

    # ── Set next draw date + estimated jackpot — live feed first ──
    info = await next_draw_info(date.fromisoformat(today))
    next_date = info["date"].isoformat()
    next_draw = get_draw_by_date(next_date)

    if info["jackpot_est"] is not None:
        estimated = info["jackpot_est"]
        logger.info(f"Next draw jackpot (live): ${estimated:,} on {next_date}")
    else:
        # Live feed unavailable — estimate from today's snowball/group1 text
        snowball = result.get("snowball_amount")
        group1_prize = result.get("group1_prize")
        prizes = result.get("prizes", [])
        g1 = next((p for p in prizes if p.get("group") == 1), None)

        if snowball:
            estimated = snowball
        elif g1 and g1.get("winners", 0) == 0 and group1_prize:
            estimated = group1_prize
        else:
            estimated = 1_000_000
        logger.info(f"Next draw jackpot (estimated, live unavailable): ${estimated:,} on {next_date}")

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


async def _fetch_once(draw_date: str, today: str) -> dict | None:
    """One attempt at complete results for a pending draw; None if unavailable."""
    result = await _fetch_results(draw_date, today)
    if not result:
        logger.warning(f"{draw_date}: no results from Singapore Pools yet")
        return None

    complete, missing = _results_complete(result)
    if not complete:
        logger.warning(f"{draw_date}: incomplete results — missing: {missing}")
        return None
    return result


async def main():
    """Fetch results for every pending draw (no results yet, dated today or earlier).

    Does nothing when no draw is pending, so postponed or shifted draws are
    picked up without any Mon/Thu assumption. Raises on the day's last run
    if today's draw still has no complete results.
    """
    now = datetime.now(ZoneInfo(settings.tz))
    today = now.date().isoformat()

    pending = sorted(
        d["draw_date"] for d in fetch_draws_without_results()
        if d["draw_date"] <= today
    )
    if not pending:
        logger.info(f"No pending draws on or before {today} — nothing to fetch")
        return
    logger.info(f"Pending draws: {pending}")

    newest = None  # (draw_date, result) of the latest draw saved this run
    for draw_date in pending:
        result = await _fetch_once(draw_date, today)
        if result:
            logger.info(f"{draw_date} results: {result['winning']} +{result['additional']}")
            await _save_results(draw_date, result)
            newest = (draw_date, result)

    if newest:
        draw_date, result = newest
        await generate_next_predictions(draw_date, result["winning"], result.get("draw_number"))

    if today in pending and (not newest or newest[0] != today):
        if now.hour >= settings.results_retry_until_hour:
            raise RuntimeError(f"No complete results for {today} by {settings.results_retry_until_hour}:00")
        logger.info("Today's draw not ready — the next hourly run will retry")
        return
    logger.info("Results cron complete")


if __name__ == "__main__":
    asyncio.run(main())
