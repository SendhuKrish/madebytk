#!/usr/bin/env python3
"""Backfill prize data for all draws from Lottery Extreme.

Usage:
    python -m app.jobs.backfill_prizes          # backfill all draws missing prizes
    python -m app.jobs.backfill_prizes --all    # re-fetch prizes for ALL draws (overwrite)

Fetches prize breakdown (group, amount, winners) from Lottery Extreme's
winners page for each draw that has results but no prize data.
Also corrects winning numbers if they differ from Lottery Extreme.

Shared by the CLI above and the POST /backfill-prizes endpoint in main.py.
"""

import asyncio
import logging
import sys

from app.services.db import fetch_all_draws, upsert_draw
from app.services.scraper import fetch_lottery_extreme_prizes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("backfill-prizes")


async def backfill_prizes(force_all: bool = False) -> dict:
    draws = fetch_all_draws()
    logger.info(f"Found {len(draws)} total draws in database")

    updated = 0
    skipped = 0
    failed = 0

    for draw in draws:
        draw_date = draw.get("draw_date")
        results = draw.get("results") or {}
        has_winning = results.get("winning") and len(results.get("winning", [])) > 0
        has_prizes = results.get("prizes") and len(results.get("prizes", [])) > 0

        if not has_winning:
            logger.debug(f"{draw_date}: no results yet, skipping")
            skipped += 1
            continue

        if has_prizes and not force_all:
            logger.debug(f"{draw_date}: already has prizes, skipping")
            skipped += 1
            continue

        logger.info(f"{draw_date}: fetching prize data...")
        le_data = await fetch_lottery_extreme_prizes(draw_date)

        if not le_data:
            logger.warning(f"{draw_date}: no data from Lottery Extreme")
            failed += 1
            continue

        changed = False

        if le_data.get("prizes"):
            results["prizes"] = le_data["prizes"]
            results["jackpot"] = le_data.get("jackpot")
            changed = True

        # Also correct winning numbers if they differ
        if le_data.get("numbers"):
            le_winning = sorted(le_data["numbers"])
            current_winning = sorted(results.get("winning", []))
            if le_winning != current_winning:
                logger.warning(
                    f"{draw_date}: CORRECTING winning numbers "
                    f"{current_winning} → {le_winning}"
                )
                results["winning"] = le_winning
                changed = True

            if le_data.get("bonus") and le_data["bonus"] != results.get("additional"):
                logger.warning(
                    f"{draw_date}: CORRECTING additional number "
                    f"{results.get('additional')} → {le_data['bonus']}"
                )
                results["additional"] = le_data["bonus"]
                changed = True

        # Update draw number if missing
        if le_data.get("draw_number") and not draw.get("draw_number"):
            draw["draw_number"] = str(le_data["draw_number"])
            changed = True

        if changed:
            draw["results"] = results
            upsert_draw(draw)
            prizes_count = len(le_data.get("prizes", []))
            logger.info(f"{draw_date}: updated ({prizes_count} prize groups)")
            updated += 1
        else:
            skipped += 1

        # Small delay to be polite to the server
        await asyncio.sleep(0.5)

    logger.info(f"Done: {updated} updated, {skipped} skipped, {failed} failed")
    return {"updated": updated, "skipped": skipped, "failed": failed}


async def main():
    force_all = "--all" in sys.argv
    await backfill_prizes(force_all=force_all)


if __name__ == "__main__":
    asyncio.run(main())
