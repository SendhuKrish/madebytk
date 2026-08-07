"""Shared "next Toto draw date" logic for predict.py and results.py.

Draws are normally Mon/Thu (PREDICT_DAYS), but Singapore Pools occasionally
shifts one draw around a public holiday (e.g. National Day). There's no
scrapable page listing future draw dates, so DRAW_DATE_OVERRIDES lets you
correct a specific one-off shift by hand once SG Pools announces it.
"""

from datetime import date, timedelta

from app.utils.config import settings

_DAY_MAP = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _overrides() -> dict[str, str]:
    """Parse DRAW_DATE_OVERRIDES="2026-08-06=2026-08-07,..." -> {expected: actual}."""
    pairs = (p for p in settings.draw_date_overrides.split(",") if p.strip())
    return dict(p.split("=", 1) for p in pairs)


def next_draw_date(after: date) -> date:
    """Next scheduled draw date strictly after `after`, per PREDICT_DAYS,
    corrected by any matching DRAW_DATE_OVERRIDES entry."""
    draw_weekdays = {_DAY_MAP[d.strip().lower()] for d in settings.predict_days.split(",")}
    for offset in range(1, 8):
        candidate = after + timedelta(days=offset)
        if candidate.weekday() in draw_weekdays:
            override = _overrides().get(candidate.isoformat())
            return date.fromisoformat(override) if override else candidate
    return after + timedelta(days=1)  # unreachable with valid PREDICT_DAYS


def _demo():
    mon = date(2026, 8, 3)  # Monday
    assert next_draw_date(mon) == date(2026, 8, 6)  # -> Thursday

    settings.draw_date_overrides = "2026-08-06=2026-08-07"
    assert next_draw_date(mon) == date(2026, 8, 7)  # Thu postponed to Fri
    settings.draw_date_overrides = ""
    print("scheduling: ok")


if __name__ == "__main__":
    _demo()
