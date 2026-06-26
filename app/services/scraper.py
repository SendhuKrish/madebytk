"""Fetch latest Singapore Toto draw results."""

import logging
import re

import httpx
from bs4 import BeautifulSoup

from app.utils.config import settings

logger = logging.getLogger(__name__)

LOTTOLYZER_URL = f"{settings.lottolyzer_url}/page/1/per-page/1/number-view"
LOTTERY_EXTREME_URL = settings.lottery_extreme_url

HEADERS = {"User-Agent": settings.scraper_user_agent}
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


async def fetch_lottolyzer_history(pages: int = 1) -> list[dict]:
    """Fetch multiple draws from lottolyzer.com history page.

    Returns list of dicts with keys: draw_number, date, winning, additional.
    """
    results = []
    url = f"{settings.lottolyzer_url}/page/1/per-page/{pages * 50}/number-view"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text()

        # Parse draw blocks: "Draw NNNN\nDD Mon YYYY\n" followed by ball images
        # Find all ball images grouped by draw
        draw_blocks = re.findall(
            r"Draw\s+(\d{4})\s*\n\s*(\d{1,2}\s+\w+\s+\d{4})",
            text,
        )

        # Find all ball image sequences
        all_imgs = soup.select("img[src*='ball']")
        ball_groups = []
        current_group = []

        for img in all_imgs:
            title = img.get("title") or img.get("alt") or ""
            src = img.get("src", "")

            # Skip the plus sign
            if "plus" in src:
                continue

            digits = re.findall(r"\d+", title or src.split("/")[-1])
            if digits:
                val = int(digits[0])
                if 1 <= val <= 49:
                    current_group.append(val)

            # Each draw has 7 balls (6 winning + 1 additional)
            if len(current_group) == 7:
                ball_groups.append(current_group)
                current_group = []

        if current_group and len(current_group) == 7:
            ball_groups.append(current_group)

        # Match draw info with ball groups
        for i, (draw_num, date_str) in enumerate(draw_blocks):
            if i >= len(ball_groups):
                break

            balls = ball_groups[i]
            winning = sorted(balls[:6])
            additional = balls[6]

            # Parse date
            from datetime import datetime
            try:
                draw_date = datetime.strptime(date_str.strip(), "%d %b %Y").strftime("%Y-%m-%d")
            except ValueError:
                draw_date = None

            results.append({
                "draw_number": draw_num,
                "draw_date": draw_date,
                "winning": winning,
                "additional": additional,
            })

        logger.info(f"Lottolyzer history: fetched {len(results)} draws")

    except Exception:
        logger.exception("Lottolyzer history fetch failed")

    return results
