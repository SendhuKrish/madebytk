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

    The page uses deeply nested tables so we parse the raw page text
    with regex rather than relying on CSS selectors or cell positions.

    Args:
        draw_date: ISO date string (YYYY-MM-DD).

    Returns:
        Dict with keys: numbers, bonus, draw_number, date, prizes, jackpot.
        None if page not available or parse fails.
    """
    url = f"{settings.lottery_extreme_winners_url}({draw_date})"

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers=HEADERS)
            resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        # Normalize whitespace: get_text() may insert newlines between
        # table cells, breaking regexes that expect adjacent text.
        text = re.sub(r"\s+", " ", soup.get_text()).strip()

        # ── Extract draw number from "(NNNN)" ──
        draw_number = None
        dn_match = re.search(r"\((\d{4})\)", text)
        if dn_match:
            draw_number = int(dn_match.group(1))

        # ── Extract winning numbers from raw text ──
        # Page renders numbers as concatenated digits followed by space
        # and additional number, e.g. "82534373946 44" before "Group"
        winning = None
        bonus = None
        nums_match = re.search(
            r"\(\d{4}\)\s*([\d\s]+?)\s*Group",
            text,
        )
        if nums_match:
            raw = nums_match.group(1).strip()
            winning, bonus = _parse_concatenated_numbers(raw)

        # ── Parse prizes from raw text via regex ──
        # Each prize row in the text looks like:
        #   "16 numbers0$0"  or  "25 numbers + Add no.4$92,756"
        # Pattern: group_digit, match_digits + "numbers" [+ Add no.],
        #          winners_digits, $payout
        prizes = []
        jackpot = None

        prize_pattern = re.compile(
            r"(\d)\s*"                       # group number
            r"(\d\s+numbers"                 # number of matches + "numbers"
            r"(?:\s*\+\s*Add\s*no\.?)?)"     # optional "+ Add no."
            r"\s*(\d[\d,]*)"                 # winners count
            r"\s*\$([\d,]+?)"                # payout amount (non-greedy)
            r"(?=\s*\d\s*\d\s+numbers"       # lookahead: next group
            r"|\s*Total|\s*$)",              # or end of prizes
        )
        for m in prize_pattern.finditer(text):
            group = int(m.group(1))
            winners = int(m.group(3).replace(",", ""))
            amount = int(m.group(4).replace(",", ""))

            # Deduplicate (nested tables cause repeated matches)
            if not any(p["group"] == group for p in prizes):
                prizes.append({"group": group, "amount": amount, "winners": winners})
                if group == 1 and amount > 0:
                    jackpot = amount

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


def _parse_concatenated_numbers(raw: str) -> tuple[list[int] | None, int | None]:
    """Parse concatenated TOTO numbers like '82534373946 44'.

    Singapore TOTO: 6 winning numbers (1-49) + 1 additional (1-49).
    The additional is usually separated by a space from the winning set.

    Returns (sorted winning list, additional) or (None, None) on failure.
    """
    parts = raw.split()

    if len(parts) == 7:
        # Already space-separated: "8 25 34 37 39 46 44"
        try:
            nums = [int(p) for p in parts]
            if all(1 <= n <= 49 for n in nums):
                return sorted(nums[:6]), nums[6]
        except ValueError:
            pass

    if len(parts) == 2:
        # "82534373946 44" — concatenated winning + separate additional
        concat, add_str = parts
        try:
            additional = int(add_str)
        except ValueError:
            additional = None

        winning = _split_concat_numbers(concat, 6)
        if winning and additional and 1 <= additional <= 49:
            return sorted(winning), additional

    if len(parts) == 1:
        # All 7 numbers concatenated: "8253437394644"
        winning = _split_concat_numbers(parts[0], 7)
        if winning:
            return sorted(winning[:6]), winning[6]

    return None, None


def _split_concat_numbers(s: str, count: int) -> list[int] | None:
    """Split a concatenated string into `count` numbers, each 1-49.

    Uses backtracking: tries 1-digit then 2-digit at each position.
    Returns the first valid split, or None.
    """
    results: list[list[int]] = []

    def backtrack(pos: int, nums: list[int]):
        if len(nums) == count:
            if pos == len(s):
                results.append(nums[:])
            return
        if pos >= len(s):
            return

        # Try 1-digit number
        d1 = int(s[pos])
        if 1 <= d1 <= 9:
            nums.append(d1)
            backtrack(pos + 1, nums)
            nums.pop()
            if results:
                return

        # Try 2-digit number
        if pos + 1 < len(s):
            d2 = int(s[pos : pos + 2])
            if 1 <= d2 <= 49:
                nums.append(d2)
                backtrack(pos + 2, nums)
                nums.pop()

    backtrack(0, [])
    return results[0] if results else None


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
