"""Pydantic models for Toto API request/response."""

from datetime import datetime

from pydantic import BaseModel, Field


class PickLine(BaseModel):
    """A single predicted line."""
    line_number: int
    numbers: list[int]
    score: int
    max_score: int = 41
    total_score: int | None = None
    sum_total: int
    odd_count: int
    even_count: int
    low_count: int
    high_count: int
    near_prev_count: int
    near_prev_numbers: list[int]
    has_complement: bool
    consecutive_pairs: list[list[int]]
    strategy: str
    position_logic: list[dict] | None = None


class PredictionResponse(BaseModel):
    """Full prediction response."""
    draw_number: int | None = None
    draw_date: str | None = None
    last_draw: list[int]
    last_draw_date: str | None = None
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    lines: list[PickLine]
    coverage: int = Field(description="Unique numbers covered across all lines")
    total_candidates_scored: int = 500_000
    candidates_passed: int


class PostMortemLine(BaseModel):
    """Post-mortem result for a single line."""
    line_number: int
    numbers: list[int]
    strategy: str
    exact_matches: list[int]
    exact_count: int
    bonus_match: bool
    near_misses: list[dict]
    prize_group: str | None = None


class RuleResult(BaseModel):
    """Result of a single rule check."""
    name: str
    passed: bool
    hold_rate: int
    detail: str


class PostMortemResponse(BaseModel):
    """Full post-mortem response."""
    draw_number: int | None = None
    draw_date: str | None = None
    previous_draw: list[int]
    actual_winning: list[int]
    actual_bonus: int | None = None
    rules_passed: int
    rules_total: int
    rule_results: list[RuleResult]
    line_results: list[PostMortemLine]
    numbers_we_covered: list[int]
    numbers_we_missed: list[int]
    root_cause: str


class HealthResponse(BaseModel):
    """Health check response."""
    status: str = "ok"
    service: str = "toto-engine"
    version: str = "4.0.0"
    uptime_seconds: float
