#!/usr/bin/env python3
"""Cron job: Fetch actual draw results and store in Supabase.

Schedule: Mon & Thu at 19:00 SGT (after the 6:30 PM draw).
Retries hourly until results appear or deadline hour is reached.
After results are saved, auto-generates predictions for the next draw.

Source priority:
  1. Lottery Extreme winners page — numbers + prize breakdown
  2. Lottolyzer — numbers only (fast updates)
  3. Lottery Extreme main page — numbers only
  4. Singapore Pools — JS-rendered, rarely works with BeautifulSoup
"""

import asyncio
import logging
import re
import sys
from datetime import date, datetime

import httpx
import pytz
from bs4 import BeautifulSoup

from app.services.db import get_draw_by_date, upsert_draw
from app.services.scraper import (
    _try_lottery_extreme,
    _try_lottolyzer,
    fetch_lottery_extreme_prizes,
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
    and optionally jackpot + prizes.
    """
    # ── Source 1: Lottery Extreme winners page (numbers + prizes) ──
    le_winners = await fetch_lottery_extreme_prizes(draw_date)
    if le_winners and le_winners.get("numbers"):
        return {
            "winning": le_winners["numbers"],
            "additional": le_winners["bonus"],
            "draw_number": str(le_winners["draw_number"]) if le_winners.get("draw_number") else None,
            "draw_date": le_winners.get("date"),
            "jackpot": le_winners.get("jackpot"),
            "prizes": le_winners.get("prizes", []),
        }

    # ── Source 2: Lottolyzer (numbers only, fastest updates) ──
    lottolyzer = await _try_lottolyzer()
    if lottolyzer:
        result = {
            "winning": lottolyzer["numbers"],
            "additional": lottolyzer.get("bonus"),
            "draw_number": str(lottolyzer["draw_number"]) if lottolyzer.get("draw_number") else None,
            "draw_date": lottolyzer.get("date"),
        }
        # Try to get prizes from lottery extreme (may have updated by now)
        le_prizes = await fetch_lottery_extreme_prizes(draw_date)
        if le_prizes and le_prizes.get("prizes"):
            result["jackpot"] = le_prizes.get("jackpot")
            result["prizes"] = le_prizes["prizes"]
        return result

    # ── Source 3: Lottery Extreme main page (numbers only) ──
    le_main = await _try_lottery_extreme()
    if le_main:
        result = {
            "winning": le_main["numbers"],
            "additional": le_main.get("bonus"),
            "draw_number": str(le_main["draw_number"]) if le_main.get("draw_number") else None,
            "draw_date": le_main.get("date"),
        }
        return result

    # ── Source 4: Singapore Pools (JS-rendered, rarely works) ──
    logger.warning("Lottolyzer + LotteryExtreme failed, trying Singapore Pools...")
    sp = await fetch_from_singapore_pools()
    if sp:
        return sp

    return None


def _save_results(today: str, result: dict) -> None:
    """Save results into the draw record for today."""
    existing = get_draw_by_date(today)

    results_data = {
        "winning": result["winning"],
        "additional": result["additional"],
        "jackpot": result.get("jackpot"),
        "prizes": result.get("prizes", []),
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
