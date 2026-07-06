#!/usr/bin/env python3
"""Cron job: Fetch actual draw results and store in Supabase.

Schedule: Mon & Thu at 19:00 SGT (after the 6:30 PM draw).
Retries hourly until results appear or deadline hour is reached.
After results are saved, auto-generates predictions for the next draw.

Source priority:
  1. Singapore Pools results page — numbers + Group 1 Prize + prizes + snowball
  2. Lottery Extreme winners page — numbers + prize breakdown (fallback)
  3. Lottolyzer — numbers only (fallback)
  4. Lottery Extreme main page — numbers only (fallback)
"""

import asyncio
import logging
import re
import sys
from datetime import date, datetime, timedelta

import httpx
import pytz
from bs4 import BeautifulSoup

from app.services.db import get_draw_by_date, upsert_draw
from app.services.scraper import (
    _try_lottery_extreme,
    _try_lottolyzer,
    fetch_lottery_extreme_prizes,
    fetch_sg_pools_results,
)
from app.utils.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("cron-results")

HEADERS = {
    "User-Agent": settings.scraper_user_agent,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = float(settings.scraper_timeout)


async def fetch_from_singapore_pools() -> dict | None:
    """Try fetching latest results from Singapore Pools website.

    Note: This page is JS-rendered so BeautifulSoup usually gets
    incomplete HTML.  Kept as a last-resort fallback.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
            resp = await client.get(settings.singapore_pools_url, headers=HEADERS)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Extract draw date
        draw_date = None
        date_el = soup.select_one(".drawDate, .draw-date, #drawDate")
        if date_el:
            text = date_el.get_text(strip=True)
            date_match = re.search(
                r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
                text, re.IGNORECASE,
            )
            if date_match:
                draw_date = datetime.strptime(
                    f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}",
                    "%d %b %Y",
                ).strftime("%Y-%m-%d")

        # Extract draw number
        draw_number = None
        draw_num_el = soup.select_one(".drawNumber, .draw-number, #drawNumber")
        if draw_num_el:
            num_match = re.search(r"(\d{4})", draw_num_el.get_text())
            if num_match:
                draw_number = num_match.group(1)

        # Extract winning numbers
        numbers = []
        for selector in [
            ".winningNums .ball", ".winning-numbers .ball", ".drawNumber-ball",
            "table.tableWinNum td", ".winNum", ".toto-winning-number",
        ]:
            balls = soup.select(selector)
            for b in balls:
                text = b.get_text(strip=True)
                if text.isdigit():
                    val = int(text)
                    if 1 <= val <= 49:
                        numbers.append(val)
            if len(numbers) >= 6:
                break

        # Extract additional number
        additional = None
        add_el = soup.select_one(".additionalNum .ball, .additional-number, #additionalNum")
        if add_el:
            text = add_el.get_text(strip=True)
            if text.isdigit():
                additional = int(text)

        if len(numbers) >= 6:
            winning = sorted(numbers[:6])
            if additional is None and len(numbers) >= 7:
                additional = numbers[6]

            logger.info(f"Singapore Pools: {winning} +{additional} draw={draw_number} date={draw_date}")
            return {
                "winning": winning,
                "additional": additional,
                "draw_number": draw_number,
                "draw_date": draw_date,
            }

    except Exception:
        logger.exception("Singapore Pools fetch failed")

    return None


async def _fetch_results(draw_date: str) -> dict | None:
    """Try all sources in priority order.

    Returns dict with keys: winning, additional, draw_number, draw_date,
    and optionally prizes, group1_prize, snowball_amount.
    """
    # ── Source 1: Singapore Pools results page (primary) ──
    sg = await fetch_sg_pools_results()
    if sg and sg.get("numbers") and sg.get("date") == draw_date:
        return {
            "winning": sg["numbers"],
            "additional": sg["bonus"],
            "draw_number": str(sg["draw_number"]) if sg.get("draw_number") else None,
            "draw_date": sg.get("date"),
            "prizes": sg.get("prizes", []),
            "group1_prize": sg.get("group1_prize"),
            "snowball_amount": sg.get("snowball_amount"),
        }

    # ── Source 2: Lottery Extreme winners page (numbers + prizes) ──
    le_winners = await fetch_lottery_extreme_prizes(draw_date)
    if le_winners and le_winners.get("numbers"):
        result = {
            "winning": le_winners["numbers"],
            "additional": le_winners["bonus"],
            "draw_number": str(le_winners["draw_number"]) if le_winners.get("draw_number") else None,
            "draw_date": le_winners.get("date"),
            "prizes": le_winners.get("prizes", []),
        }
        # Enrich with SG Pools Group 1 Prize if available
        if sg and sg.get("group1_prize"):
            result["group1_prize"] = sg["group1_prize"]
            result["snowball_amount"] = sg.get("snowball_amount")
        return result

    # ── Source 3: Lottolyzer (numbers only, fastest updates) ──
    lottolyzer = await _try_lottolyzer()
    if lottolyzer:
        result = {
            "winning": lottolyzer["numbers"],
            "additional": lottolyzer.get("bonus"),
            "draw_number": str(lottolyzer["draw_number"]) if lottolyzer.get("draw_number") else None,
            "draw_date": lottolyzer.get("date"),
        }
        # Try to get prizes from lottery extreme
        le_prizes = await fetch_lottery_extreme_prizes(draw_date)
        if le_prizes and le_prizes.get("prizes"):
            result["prizes"] = le_prizes["prizes"]
        # Enrich with SG Pools Group 1 Prize
        if sg and sg.get("group1_prize"):
            result["group1_prize"] = sg["group1_prize"]
            result["snowball_amount"] = sg.get("snowball_amount")
        return result

    # ── Source 4: Lottery Extreme main page (numbers only) ──
    le_main = await _try_lottery_extreme()
    if le_main:
        return {
            "winning": le_main["numbers"],
            "additional": le_main.get("bonus"),
            "draw_number": str(le_main["draw_number"]) if le_main.get("draw_number") else None,
            "draw_date": le_main.get("date"),
        }

    # ── Source 5: Singapore Pools CSS-selector fallback ──
    logger.warning("All primary sources failed, trying SG Pools CSS fallback...")
    sp = await fetch_from_singapore_pools()
    if sp:
        return sp

    return None


def _next_draw_date(draw_date_str: str) -> str:
    """Get the next Toto draw date (Mon/Thu) after the given date."""
    dt = datetime.strptime(draw_date_str, "%Y-%m-%d")
    weekday = dt.weekday()  # 0=Mon, 3=Thu
    if weekday == 0:    # Monday → Thursday
        delta = 3
    elif weekday == 3:  # Thursday → Monday
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
        "jackpot": result.get("jackpot"),
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
    snowball = result.get("snowball_amount")
    next_date = _next_draw_date(today)
    next_draw = get_draw_by_date(next_date)

    if snowball:
        # Group 1 snowballed — next draw inherits this amount
        estimated = snowball
        logger.info(f"Snowball detected: ${snowball:,} → setting estimated_jackpot on {next_date}")
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


async def _generate_next_predictions(result: dict) -> None:
    """After results are in, immediately generate predictions for the next draw."""
    from app.jobs.predict import main as predict_main
    logger.info(f"Results saved — generating predictions for next draw using winning numbers {result['winning']}")
    await predict_main()


async def main():
    """Fetch results with retry. On success, auto-generate next predictions."""
    today = date.today().isoformat()
    tz = pytz.timezone(settings.tz)
    retry_until = settings.results_retry_until_hour
    retry_interval = settings.results_retry_interval_min

    logger.info(f"Running results cron for {today} (retry until {retry_until}:00, every {retry_interval}min)")

    result = await _fetch_results(today)

    while not result:
        now = datetime.now(tz)
        if now.hour >= retry_until:
            logger.error(f"All sources failed and past retry deadline ({retry_until}:00). Giving up.")
            sys.exit(1)

        logger.warning(f"No results yet. Retrying in {retry_interval} minutes (until {retry_until}:00 {settings.tz})...")
        await asyncio.sleep(retry_interval * 60)
        result = await _fetch_results(today)

    logger.info(f"Results: {result['winning']} +{result['additional']}")
    _save_results(today, result)
    await _generate_next_predictions(result)
    logger.info("Results cron complete")


if __name__ == "__main__":
    asyncio.run(main())
