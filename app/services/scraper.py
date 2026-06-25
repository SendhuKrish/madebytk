"""Fetch latest Singapore Toto draw results."""

import logging
import re

import httpx
from bs4 import BeautifulSoup

from app.utils.config import settings

logger = logging.getLogger(__name__)

LOTTOLYZER_URL = (
    "https://en.lottolyzer.com/history/singapore/toto/page/1/per-page/1/number-view"
)
LOTTERY_EXTREME_URL = "https://www.lotteryextreme.com/singapore/toto-results"

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TotoEngine/3.0)"}
TIMEOUT = float(settings.scraper_timeout)


async def fetch_latest_draw() -> dict | None:
    """Fetch the latest Singapore Toto draw.

    Returns:
        Dict with keys: numbers (list[int]), bonus (int|None),
        draw_number (int|None), date (str|None).
        None if fetch fails.
    """
    result = await _try_lottolyzer()
    if result:
        return result

    result = await _try_lottery_extreme()
    if result:
        return result

    logger.warning("All scraping sources failed")
    return None


async def _try_lottolyzer() -> dict | None:
    """Try fetching from lottolyzer.com."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(LOTTOLYZER_URL, headers=HEADERS)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Look for ball images with numbers in alt text or filenames
        balls = soup.select("img[src*='ball']")
        numbers = []
        for b in balls:
            for attr in [b.get("alt", ""), b.get("src", "").split("/")[-1]]:
                digits = re.findall(r"\d+", attr)
                for d in digits:
                    val = int(d)
                    if 1 <= val <= 49 and val not in numbers:
                        numbers.append(val)
                        break
                if numbers and numbers[-1] == (int(re.findall(r"\d+", b.get("alt", "") or b.get("src", ""))[0]) if re.findall(r"\d+", b.get("alt", "") or b.get("src", "")) else -1):
                    break

        if len(numbers) >= 6:
            main = sorted(numbers[:6])
            bonus = numbers[6] if len(numbers) > 6 else None

            draw_num = None
            date_str = None
            text = soup.get_text()
            draw_match = re.search(r"#?(\d{4})", text)
            if draw_match:
                draw_num = int(draw_match.group(1))
            date_match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
            if date_match:
                date_str = date_match.group(1)

            logger.info(f"Lottolyzer: {main} bonus={bonus} draw={draw_num}")
            return {
                "numbers": main,
                "bonus": bonus,
                "draw_number": draw_num,
                "date": date_str,
            }

    except Exception:
        logger.exception("Lottolyzer fetch failed")

    return None


async def _try_lottery_extreme() -> dict | None:
    """Try fetching from lotteryextreme.com."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(LOTTERY_EXTREME_URL, headers=HEADERS)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        result_divs = soup.select(".result, .draw-result, .ball")
        numbers = []
        for el in result_divs:
            text = el.get_text(strip=True)
            if text.isdigit():
                val = int(text)
                if 1 <= val <= 49:
                    numbers.append(val)

        if len(numbers) >= 6:
            main = sorted(numbers[:6])
            bonus = numbers[6] if len(numbers) > 6 else None
            logger.info(f"LotteryExtreme: {main} bonus={bonus}")
            return {
                "numbers": main,
                "bonus": bonus,
                "draw_number": None,
                "date": None,
            }

    except Exception:
        logger.exception("LotteryExtreme fetch failed")

    return None
