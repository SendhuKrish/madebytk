"""Supabase client wrapper for Toto Engine."""

import logging

from supabase import create_client, Client
from app.utils.config import settings

logger = logging.getLogger("toto-db")

_client: Client | None = None


def get_client() -> Client:
    """Get or create the Supabase client singleton."""
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_key)
    return _client


def fetch_all_draws() -> list[dict]:
    """Fetch all draws ordered by draw_date descending."""
    resp = get_client().table("draws").select("*").order(
        "draw_date", desc=True
    ).execute()
    return resp.data or []


def upsert_draw(draw: dict) -> dict:
    """Insert or update a draw record (keyed on draw_date)."""
    resp = (
        get_client()
        .table("draws")
        .upsert(draw, on_conflict="draw_date")
        .execute()
    )
    return resp.data[0] if resp.data else {}


def fetch_draws_without_results() -> list[dict]:
    """Fetch draws that have no winning numbers."""
    all_draws = fetch_all_draws()
    return [
        d for d in all_draws
        if not d.get("results") or not d["results"].get("winning") or len(d["results"]["winning"]) == 0
    ]


def delete_draw_by_id(draw_id: str) -> bool:
    """Delete a draw by id."""
    resp = get_client().table("draws").delete().eq("id", draw_id).execute()
    return bool(resp.data)


def get_draw_by_date(draw_date: str) -> dict | None:
    """Get a draw by draw_date (YYYY-MM-DD)."""
    resp = (
        get_client()
        .table("draws")
        .select("*")
        .eq("draw_date", draw_date)
        .execute()
    )
    return resp.data[0] if resp.data else None


def fetch_history(max_draws: int = 100) -> list[list[int]]:
    """Pull winning numbers from Supabase draws, newest first.

    Used by the v4 self-learning engine to compute POS_AVERAGES and
    HOT_NUMBERS at prediction time. Skips draws without results
    (future/pending draws). Bonus number is excluded.
    """
    history = []
    try:
        for d in fetch_all_draws():
            winning = (d.get("results") or {}).get("winning") or []
            if len(winning) == 6:
                history.append(sorted(int(n) for n in winning))
            if len(history) >= max_draws:
                break
    except Exception:
        logger.exception("fetch_history failed — engine will use defaults")
    return history


def sign_in_user(email: str, password: str) -> dict:
    """Sign in a user via Supabase Auth. Returns session data or raises."""
    try:
        resp = get_client().auth.sign_in_with_password({"email": email, "password": password})
        return {
            "access_token": resp.session.access_token,
            "user": {"id": str(resp.user.id), "email": resp.user.email},
        }
    except Exception as e:
        raise ValueError(str(e))
