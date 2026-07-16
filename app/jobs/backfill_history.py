"""One-off backfill of historical Toto draws into Supabase.

Fetches ~100 past draws from lottolyzer's number-view history pages and
upserts them into the `draws` table so the v4 self-learning engine has a
proper training base. Rows that already exist (your real rows with
predictions/bets) are NEVER touched — we skip any draw_date already
present in the table.

Usage (on the VM, from the project root):
    python -m app.scripts.backfill_history            # live run
    python -m app.scripts.backfill_history --dry-run  # parse + report only
    python -m app.scripts.backfill_history --pages 3  # ~150 draws

Historical rows are inserted with empty predictions/bets:
    {draw_date, draw_number, predictions: [], bets: [],
     results: {"winning": [...6 nums...], "additional": n}}
"""

import argparse
import logging
import re
import sys

import httpx
from bs4 import BeautifulSoup

from app.services import db
from app.utils.config import settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill")

HISTORY_URL = (
    "https://en.lottolyzer.com/history/singapore/toto"
    "/page/{page}/per-page/50/number-view"
)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DRAWNO_RE = re.compile(r"^\d{3,5}$")


def fetch_page(page: int) -> list[dict]:
    """Fetch and parse one history page → list of draw dicts."""
    url = HISTORY_URL.format(page=page)
    logger.info(f"Fetching {url}")
    resp = httpx.get(
        url,
        headers={"User-Agent": settings.scraper_user_agent},
        timeout=settings.scraper_timeout,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return parse_history_html(resp.text)


def parse_history_html(html: str) -> list[dict]:
    """Parse lottolyzer number-view table rows.

    Each row carries: draw number, date (YYYY-MM-DD), 6 winning numbers
    (comma-separated in one cell), and the additional number.
    """
    soup = BeautifulSoup(html, "html.parser")
    draws = []

    for tr in soup.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue

        date_str = next((c for c in cells if DATE_RE.fullmatch(c)), None)
        if not date_str:
            continue

        draw_number = next((c for c in cells if DRAWNO_RE.fullmatch(c)), None)

        winning: list[int] | None = None
        additional: int | None = None
        for c in cells:
            parts = [p for p in re.split(r"[,\s]+", c) if p.isdigit()]
            nums = [int(p) for p in parts]
            if len(nums) == 6 and all(1 <= n <= 49 for n in nums):
                winning = sorted(nums)
            elif winning is not None and additional is None and len(nums) == 1 \
                    and 1 <= nums[0] <= 49:
                additional = nums[0]

        if winning:
            draws.append({
                "draw_date": date_str,
                "draw_number": draw_number,
                "winning": winning,
                "additional": additional,
            })

    return draws


def backfill(pages: int, dry_run: bool) -> None:
    parsed: list[dict] = []
    for page in range(1, pages + 1):
        try:
            parsed.extend(fetch_page(page))
        except Exception:
            logger.exception(f"Page {page} failed — continuing")

    # Dedupe by date (pages can overlap after a new draw shifts rows)
    by_date: dict[str, dict] = {}
    for d in parsed:
        by_date.setdefault(d["draw_date"], d)
    logger.info(f"Parsed {len(by_date)} unique draws from {pages} page(s)")

    if not by_date:
        logger.error("Nothing parsed — page structure may have changed. "
                     "Inspect the HTML and adjust parse_history_html().")
        sys.exit(1)

    inserted = skipped = failed = 0
    for date_str in sorted(by_date, reverse=True):
        d = by_date[date_str]
        existing = db.get_draw_by_date(date_str)
        if existing:
            skipped += 1
            continue

        row = {
            "draw_date": d["draw_date"],
            "draw_number": d["draw_number"],
            "predictions": [],
            "bets": [],
            "results": {
                "winning": d["winning"],
                "additional": d["additional"],
            },
        }
        if dry_run:
            logger.info(f"[dry-run] would insert {d['draw_date']} "
                        f"#{d['draw_number']} {d['winning']} +{d['additional']}")
            inserted += 1
            continue

        try:
            db.upsert_draw(row)
            inserted += 1
        except Exception:
            logger.exception(f"Upsert failed for {date_str}")
            failed += 1

    logger.info(
        f"Done. inserted={inserted} skipped_existing={skipped} failed={failed}"
        + (" (dry run — nothing written)" if dry_run else "")
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pages", type=int, default=2,
                    help="history pages to fetch, 50 draws each (default 2)")
    ap.add_argument("--dry-run", action="store_true",
                    help="parse and report without writing to Supabase")
    args = ap.parse_args()
    backfill(pages=args.pages, dry_run=args.dry_run)
