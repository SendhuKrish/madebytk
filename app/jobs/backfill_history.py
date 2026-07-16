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
DATE_RE = re.compile(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})")
DRAW_SPLIT_RE = re.compile(r"Draw\s+(\d{3,5})")
BALL_RE = re.compile(r"ball(\d{2})\.gif")

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}


def fetch_page(page: int) -> list[dict]:
    """Fetch and parse one history page -> list of draw dicts."""
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
    """Parse lottolyzer number-view history page.

    Real structure (verified Jul 2026): each draw is a block of
        Draw 4200
        16 Jul 2026
        <img src=".../ball06.gif" title="6"> x6, plus.gif, ball19.gif
    Numbers live in ball-image FILENAMES, not table cells. We split the
    raw HTML on "Draw NNNN" markers; within each chunk we read the date
    ("DD Mon YYYY") and the first 7 ballNN.gif matches (6 winning +
    1 additional).
    """
    draws = []
    parts = DRAW_SPLIT_RE.split(html)
    # parts = [preamble, drawno1, chunk1, drawno2, chunk2, ...]
    for i in range(1, len(parts) - 1, 2):
        draw_number = parts[i]
        chunk = parts[i + 1]

        dm = DATE_RE.search(chunk)
        if not dm:
            continue
        day, mon, year = dm.groups()
        month = _MONTHS.get(mon.title())
        if not month:
            continue
        date_str = f"{year}-{month:02d}-{int(day):02d}"

        balls = [int(b) for b in BALL_RE.findall(chunk)][:7]
        balls = [b for b in balls if 1 <= b <= 49]
        if len(balls) < 6:
            continue

        draws.append({
            "draw_date": date_str,
            "draw_number": draw_number,
            "winning": sorted(balls[:6]),
            "additional": balls[6] if len(balls) >= 7 else None,
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
