#!/usr/bin/env python3
"""Cron job: Fetch actual draw results and store in Supabase.

Schedule: Mon & Thu at 19:00 SGT (after the 6:30 PM draw).
Retries hourly until results appear or deadline hour is reached.
After results are saved, auto-generates predictions for the next draw.
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
from app.services.scraper import _try_lottolyzer, _try_lottery_extreme
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
    """Try fetching latest results from Singapore Pools website."""
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

        # Fallback: scan all text
        if len(numbers) < 6:
            all_text = soup.get_text()
            num_matches = re.findall(r"\b(\d{1,2})\b", all_text)
            potential = [int(n) for n in num_matches if 1 <= int(n) <= 49]
            if len(potential) >= 7:
                seen = []
                for n in potential:
                    if n not in seen:
                        seen.append(n)
                    if len(seen) >= 7:
                        break
                if len(seen) >= 7:
                    numbers = seen

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
                "winning": winning, "additional": additional,
                "draw_number": draw_number, "draw_date": draw_date,
            }

    except Exception:
        logger.exception("Singapore Pools fetch failed")

    return None


async def fetch_from_lottolyzer() -> dict | None:
    """Fallback: fetch from lottolyzer.com."""
    result = await _try_lottolyzer()
    if result:
        return {
            "winning": result["numbers"],
            "additional": result.get("bonus"),
            "draw_number": str(result["draw_number"]) if result.get("draw_number") else None,
            "draw_date": result.get("date"),
        }
    return None


async def fetch_from_lottery_extreme() -> dict | None:
    """Fallback: fetch from lotteryextreme.com."""
    result = await _try_lottery_extreme()
    if result:
        return {
            "winning": result["numbers"],
            "additional": result.get("bonus"),
            "draw_number": None, "draw_date": None,
        }
    return None


async def _fetch_results() -> dict | None:
    """Try all sources in order."""
    result = await fetch_from_singapore_pools()

    if not result:
        logger.warning("Singapore Pools failed, trying lottolyzer...")
        result = await fetch_from_lottolyzer()

    if not result:
        logger.warning("Lottolyzer failed, trying lottery extreme...")
        result = await fetch_from_lottery_extreme()

    return result


def _save_results(today: str, result: dict) -> None:
    """Save results into the draw record for today."""
    existing = get_draw_by_date(today)

    if existing:
        existing["results"] = {"winning": result["winning"], "additional": result["additional"]}
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
            "results": {"winning": result["winning"], "additional": result["additional"]},
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

    result = await _fetch_results()

    while not result:
        now = datetime.now(tz)
        if now.hour >= retry_until:
            logger.error(f"All sources failed and past retry deadline ({retry_until}:00). Giving up.")
            sys.exit(1)

        logger.warning(f"No results yet. Retrying in {retry_interval} minutes (until {retry_until}:00 {settings.tz})...")
        await asyncio.sleep(retry_interval * 60)
        result = await _fetch_results()

    logger.info(f"Results: {result['winning']} +{result['additional']}")
    _save_results(today, result)
    await _generate_next_predictions(result)
    logger.info("Results cron complete")


if __name__ == "__main__":
    asyncio.run(main())
