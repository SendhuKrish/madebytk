"""Fetch latest Singapore Toto draw results and prize data."""

import logging
import re
from datetime import datetime

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
    """Try fetching from lottolyzer.com.

    Parses ball images using title attribute (most reliable).
    Extracts only the first draw's numbers (first 7 ball images).
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(LOTTOLYZER_URL, headers=HEADERS)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Select ball images, excluding plus-sign images
        ball_imgs = [
            img for img in soup.select("img[src*='ball']")
            if "plus" not in img.get("src", "")
        ]

        numbers = []
        for img in ball_imgs:
            # Prefer title attribute (clean single number)
            title = (img.get("title") or "").strip()
            if title.isdigit():
                val = int(title)
                if 1 <= val <= 49:
                    numbers.append(val)
                    if len(numbers) >= 7:
                        break
                    continue

            # Fallback: extract from src filename (e.g. ball08.gif → 8)
            src = img.get("src", "")
            match = re.search(r"ball(\d{1,2})\.", src)
            if match:
                val = int(match.group(1))
                if 1 <= val <= 49:
                    numbers.append(val)
                    if len(numbers) >= 7:
                        break

        if len(numbers) < 6:
            logger.warning(f"Lottolyzer: only found {len(numbers)} ball numbers")
            return None

        main = sorted(numbers[:6])
        bonus = numbers[6] if len(numbers) > 6 else None

        # Extract draw number from "Draw NNNN" text
        draw_num = None
        date_str = None
        text = soup.get_text()

        draw_match = re.search(r"Draw\s+(\d{4})", text)
        if draw_match:
            draw_num = int(draw_match.group(1))

        # Extract date from "DD Mon YYYY" pattern (e.g. "06 Jul 2026")
        date_match = re.search(
            r"(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})",
            text, re.IGNORECASE,
        )
        if date_match:
            try:
                date_str = datetime.strptime(
                    f"{date_match.group(1)} {date_match.group(2)} {date_match.group(3)}",
                    "%d %b %Y",
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass

        logger.info(f"Lottolyzer: {main} bonus={bonus} draw={draw_num} date={date_str}")
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
    """Try fetching from lotteryextreme.com main results page."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(LOTTERY_EXTREME_URL, headers=HEADERS)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Try class-based selectors for number elements
        numbers = []
        for selector in [".ball", ".result", ".draw-result", ".number"]:
            els = soup.select(selector)
            for el in els:
                text = el.get_text(strip=True)
                if text.isdigit():
                    val = int(text)
                    if 1 <= val <= 49:
                        numbers.append(val)
            if len(numbers) >= 6:
                break

        # Fallback: find leaf elements with 1-2 digit text
        if len(numbers) < 6:
            numbers = []
            for el in soup.find_all(True):
                text = el.get_text(strip=True)
                if re.fullmatch(r"\d{1,2}", text) and not el.find_all(True):
                    val = int(text)
                    if 1 <= val <= 49:
                        numbers.append(val)
                        if len(numbers) >= 7:
                            break

        if len(numbers) < 6:
            logger.warning(f"LotteryExtreme: only found {len(numbers)} numbers")
            return None

        main = sorted(numbers[:6])
        bonus = numbers[6] if len(numbers) > 6 else None

        # Extract draw number and date
        draw_num = None
        date_str = None
        text = soup.get_text()

        dn_match = re.search(r"\((\d{4})\)", text)
        if dn_match:
            draw_num = int(dn_match.group(1))

        # Pattern: "DD/MM/YYYY" (e.g. "02/07/2026")
        date_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
        if date_match:
            try:
                date_str = datetime.strptime(
                    f"{date_match.group(1)}/{date_match.group(2)}/{date_match.group(3)}",
                    "%d/%m/%Y",
                ).strftime("%Y-%m-%d")
            except ValueError:
                pass

        logger.info(f"LotteryExtreme: {main} bonus={bonus} draw={draw_num} date={date_str}")
        return {
            "numbers": main,
            "bonus": bonus,
            "draw_number": draw_num,
            "date": date_str,
        }

    except Exception:
        logger.exception("LotteryExtreme fetch failed")

    return None


async def fetch_lottery_extreme_prizes(draw_date: str) -> dict | None:
    """Fetch results AND prizes from lottery extreme winners page.

    Args:
        draw_date: ISO date string (YYYY-MM-DD).

    Returns:
        Dict with keys: numbers, bonus, draw_number, date, prizes.
        None if page not available or parse fails.
    """
    url = f"{settings.lottery_extreme_winners_url}({draw_date})"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        text = soup.get_text()

        # ── Extract draw number from "(NNNN)" ──
        draw_number = None
        dn_match = re.search(r"\((\d{4})\)", text)
        if dn_match:
            draw_number = int(dn_match.group(1))

        # ── Extract winning numbers ──
        numbers = []
        for selector in [".ball", ".result", ".draw-result", ".number"]:
            els = soup.select(selector)
            for el in els:
                t = el.get_text(strip=True)
                if t.isdigit():
                    val = int(t)
                    if 1 <= val <= 49:
                        numbers.append(val)
            if len(numbers) >= 6:
                break

        # Fallback: leaf elements with 1-2 digit text
        if len(numbers) < 6:
            numbers = []
            for el in soup.find_all(True):
                t = el.get_text(strip=True)
                if re.fullmatch(r"\d{1,2}", t) and not el.find_all(True):
                    val = int(t)
                    if 1 <= val <= 49:
                        numbers.append(val)
                        if len(numbers) >= 7:
                            break

        winning = sorted(numbers[:6]) if len(numbers) >= 6 else None
        bonus = numbers[6] if len(numbers) > 6 else None

        # ── Parse prize table ──
        prizes = []
        jackpot = None

        for table in soup.find_all("table"):
            header_text = table.get_text().lower()
            if "group" not in header_text or "winner" not in header_text:
                continue

            rows = table.find_all("tr")
            for row in rows:
                cells = row.find_all(["td", "th"])
                if len(cells) < 4:
                    continue

                grp_text = cells[0].get_text(strip=True)
                grp_match = re.search(r"^(\d)$", grp_text)
                if not grp_match:
                    continue

                group = int(grp_match.group(1))
                winners_text = cells[2].get_text(strip=True)
                payout_text = cells[3].get_text(strip=True)

                winners = 0
                if re.search(r"\d", winners_text):
                    winners = int(re.sub(r"[^\d]", "", winners_text))

                amount = 0
                if re.search(r"\d", payout_text):
                    amount = int(re.sub(r"[^\d]", "", payout_text))

                prizes.append({"group": group, "amount": amount, "winners": winners})

                if group == 1 and amount > 0:
                    jackpot = amount

            if prizes:
                break

        if not winning and not prizes:
            logger.warning(f"LotteryExtreme winners: no data found for {draw_date}")
            return None

        logger.info(
            f"LotteryExtreme winners: {winning} bonus={bonus} draw={draw_number} "
            f"prizes={len(prizes)} groups"
        )
        return {
            "numbers": winning,
            "bonus": bonus,
            "draw_number": draw_number,
            "date": draw_date,
            "prizes": prizes,
            "jackpot": jackpot,
        }

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            logger.info(f"LotteryExtreme winners: page not yet available for {draw_date}")
        else:
            logger.exception("LotteryExtreme winners fetch failed")
    except Exception:
        logger.exception("LotteryExtreme winners fetch failed")

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

        # Parse draw blocks: "Draw NNNN\nDD Mon YYYY\n"
        draw_blocks = re.findall(
            r"Draw\s+(\d{4})\s*\n\s*(\d{1,2}\s+\w+\s+\d{4})",
            text,
        )

        # Collect ball images, skipping plus signs
        all_imgs = [
            img for img in soup.select("img[src*='ball']")
            if "plus" not in img.get("src", "")
        ]

        ball_groups = []
        current_group = []

        for img in all_imgs:
            title = (img.get("title") or "").strip()
            if title.isdigit():
                val = int(title)
            else:
                # Fallback to src filename
                src = img.get("src", "")
                match = re.search(r"ball(\d{1,2})\.", src)
                if match:
                    val = int(match.group(1))
                else:
                    continue

            if 1 <= val <= 49:
                current_group.append(val)

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
