"""Shared "next Toto draw date" logic for predict.py and results.py.

Checks Singapore Pools' live next-draw feed first (reflects one-off
postponements, e.g. a draw shifted for a public holiday). Falls back to
plain mon/thu calendar math — correctable via DRAW_DATE_OVERRIDES — only
if that fetch fails.
"""

import logging
from datetime import date, timedelta

from app.services.scraper import fetch_next_draw
from app.utils.config import settings

logger = logging.getLogger("scheduling")

_DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _overrides() -> dict[str, str]:
    """Parse DRAW_DATE_OVERRIDES="2026-08-06=2026-08-07,..." -> {expected: actual}."""
    pairs = (p for p in settings.draw_date_overrides.split(",") if p.strip())
    return dict(p.split("=", 1) for p in pairs)


def calendar_next_draw_date(after: date) -> date:
    """Fallback: next PREDICT_DAYS weekday after `after`, corrected by DRAW_DATE_OVERRIDES."""
    draw_weekdays = {_DAY_MAP[d.strip().lower()] for d in settings.predict_days.split(",")}
    for offset in range(1, 8):
        candidate = after + timedelta(days=offset)
        if candidate.weekday() in draw_weekdays:
            override = _overrides().get(candidate.isoformat())
            return date.fromisoformat(override) if override else candidate
    return after + timedelta(days=1)  # unreachable with valid PREDICT_DAYS


async def next_draw_info(after: date) -> dict:
    """Real next draw date + jackpot estimate, strictly after `after`.

    Returns {"date": date, "jackpot_est": int|None}. jackpot_est is None
    when the live feed is unreachable — callers needing a number should
    fall back to their own estimate rather than assume one here.
    """
    live = await fetch_next_draw()
    if live and live["date"] > after.isoformat():
        return {"date": date.fromisoformat(live["date"]), "jackpot_est": live["jackpot_est"]}

    fallback = calendar_next_draw_date(after)
    logger.warning(f"Next-draw live check unavailable — using calendar estimate {fallback}")
    return {"date": fallback, "jackpot_est": None}


async def next_draw_date(after: date) -> date:
    """The real next draw date strictly after `after`."""
    return (await next_draw_info(after))["date"]


def _demo():
    mon = date(2026, 8, 3)  # Monday
    assert calendar_next_draw_date(mon) == date(2026, 8, 6)  # -> Thursday

    settings.draw_date_overrides = "2026-08-06=2026-08-07"
    assert calendar_next_draw_date(mon) == date(2026, 8, 7)  # Thu postponed to Fri
    settings.draw_date_overrides = ""
    print("scheduling: ok")


if __name__ == "__main__":
    _demo()
