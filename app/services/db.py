"""Supabase client wrapper for Toto Engine."""

from supabase import create_client, Client
from app.utils.config import settings

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
    """Insert or update a draw record."""
    resp = get_client().table("draws").upsert(draw).execute()
    return resp.data[0] if resp.data else {}


def get_draw_by_number(draw_number: str) -> dict | None:
    """Get a draw by draw_number."""
    resp = (
        get_client()
        .table("draws")
        .select("*")
        .eq("draw_number", draw_number)
        .execute()
    )
    return resp.data[0] if resp.data else None


def fetch_draws_without_results() -> list[dict]:
    """Fetch draws that have no winning numbers."""
    all_draws = fetch_all_draws()
    return [
        d for d in all_draws
        if not d.get("results") or not d["results"].get("winning") or len(d["results"]["winning"]) == 0
    ]


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
