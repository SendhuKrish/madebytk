"""Core Toto filter engine v4 — self-learning scoring, generation, post-mortem.

v4 changes:
  1. LearnedParams: POS_AVERAGES and HOT_NUMBERS are computed from draw
     history at prediction time (recency-blended), with static fallbacks.
  2. generate_synthesis FIXED: line 6 is built ONLY from numbers already
     present in lines 1-5, and must differ from concentrated by >= 3.
  3. generate_all accepts `history` (list of past draws, newest first)
     and learns weights automatically before generating.
"""

import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

logger = logging.getLogger(__name__)

POOL = list(range(1, 50))
CANDIDATE_COUNT = 500_000
MIN_FILTER_SCORE = 38
MIN_SKEW_SCORE = 36

POS_RANGES = [
    (1, 14),   # P1
    (2, 24),   # P2
    (8, 32),   # P3
    (14, 38),  # P4
    (20, 44),  # P5
    (33, 49),  # P6
]

# ── Static fallbacks (used only when no history is provided) ──
DEFAULT_POS_AVERAGES = [6.6, 13.6, 21.4, 27.8, 35.6, 42.7]
DEFAULT_HOT_NUMBERS = {4, 7, 11, 21, 23, 31, 34, 37, 39, 46}

# Mean-reversion threshold: if prev deviates more than this from avg, apply pull
REVERSION_THRESHOLD = 3.0

# Mean-reversion applies to middle positions only (P2-P5, 0-indexed: 1-4)
REVERSION_POSITIONS = {1, 2, 3, 4}

# ── Learning knobs ──
RECENT_WINDOW = 8          # draws used for recency signals
RECENCY_BLEND = 0.4        # weight of recent avg vs long-term avg
HOT_MIN_APPEARANCES = 2    # appearances in recent window to be "hot"


@dataclass(slots=True)
class LearnedParams:
    """Weights learned from draw history."""
    pos_averages: list[float] = field(
        default_factory=lambda: list(DEFAULT_POS_AVERAGES)
    )
    hot_numbers: set[int] = field(
        default_factory=lambda: set(DEFAULT_HOT_NUMBERS)
    )
    skew_direction: str = "low"  # "low" or "high" — which skew line to generate
    draws_used: int = 0
    recent_window: int = 0


def learn_parameters(
    history: list[list[int]] | None,
    recent_n: int = RECENT_WINDOW,
    blend: float = RECENCY_BLEND,
) -> LearnedParams:
    """Learn POS_AVERAGES and HOT_NUMBERS from draw history.

    Args:
        history: Past draws (each a list of 6 winning numbers),
                 ordered NEWEST FIRST. Bonus number excluded.
        recent_n: How many recent draws drive the recency signals.
        blend: Weight of the recent average vs the long-term average
               (0.4 = 60% long-term + 40% recent).

    Returns:
        LearnedParams with blended position averages and fresh hot numbers.
        Falls back to static defaults when history is missing/too small.
    """
    valid = [
        sorted(d) for d in (history or [])
        if isinstance(d, (list, tuple)) and len(d) == 6
        and all(isinstance(n, int) and 1 <= n <= 49 for n in d)
    ]

    if len(valid) < 3:
        logger.info("learn_parameters: insufficient history, using defaults")
        return LearnedParams()

    recent = valid[:recent_n]

    # ── Position averages ──
    # Long-term from the full history; recent from the window; blended.
    long_avgs = [
        sum(d[i] for d in valid) / len(valid) for i in range(6)
    ]
    recent_avgs = [
        sum(d[i] for d in recent) / len(recent) for i in range(6)
    ]
    blended = [
        round((1.0 - blend) * long_avgs[i] + blend * recent_avgs[i], 1)
        for i in range(6)
    ]

    # ── Hot numbers ──
    # Numbers appearing HOT_MIN_APPEARANCES+ times in the recent window.
    freq = Counter(n for d in recent for n in d)
    hot = {n for n, c in freq.items() if c >= HOT_MIN_APPEARANCES}
    if not hot:  # degenerate case: tiny window with no repeats
        hot = set(DEFAULT_HOT_NUMBERS)

    # ── Skew direction (adaptive) ──
    # Which extreme shape is the recent regime producing? A draw is
    # "high-heavy" with 4+ numbers >= 25, "low-heavy" with 4+ <= 25.
    # The skew line in standard mode follows the dominant direction;
    # ties break on the recent average sum vs the theoretical 150.
    high_heavy = sum(1 for d in recent if sum(1 for n in d if n >= 25) >= 4)
    low_heavy = sum(1 for d in recent if sum(1 for n in d if n <= 25) >= 4)
    if high_heavy > low_heavy:
        skew_direction = "high"
    elif low_heavy > high_heavy:
        skew_direction = "low"
    else:
        avg_recent_sum = sum(sum(d) for d in recent) / len(recent)
        skew_direction = "high" if avg_recent_sum > 150 else "low"

    params = LearnedParams(
        pos_averages=blended,
        hot_numbers=hot,
        skew_direction=skew_direction,
        draws_used=len(valid),
        recent_window=len(recent),
    )
    logger.info(
        f"learn_parameters: {len(valid)} draws, recent={len(recent)}, "
        f"pos_avg={blended}, hot={sorted(hot)}, "
        f"skew={skew_direction} (hi_heavy={high_heavy}, lo_heavy={low_heavy})"
    )
    return params


@dataclass(slots=True)
class ScoredPick:
    """A scored 6-number pick."""
    pick: list[int]
    filter_score: int = 0
    position_score: int = 0
    total_score: int = 0
    sum_total: int = 0
    odd_count: int = 0
    low_count: int = 0
    nb3: int = 0
    fails: list[str] = field(default_factory=list)


def score_filter(pick: list[int], prev: list[int]) -> ScoredPick:
    """Score a pick against all 20 validated rules."""
    s = sorted(pick)
    p = sorted(prev)
    ss, ps = set(s), set(p)
    sc = 0
    fails = []

    spread = s[5] - s[0]
    sm = sum(s)
    gaps = [s[i + 1] - s[i] for i in range(5)]
    ranges = set(
        0 if n <= 10 else 1 if n <= 20 else 2 if n <= 30 else 3 if n <= 40 else 4
        for n in s
    )
    rows = set((n - 1) // 7 for n in s)
    units = Counter(n % 10 for n in s)
    low = sum(1 for n in s if n <= 25)
    odds = sum(1 for n in s if n % 2 == 1)
    nb3 = sum(1 for c in s if any(0 < abs(c - pp) <= 3 for pp in p))
    prev_dec = set((n - 1) // 10 for n in p)
    curr_dec = set((n - 1) // 10 for n in s)
    dec_sh = len(prev_dec & curr_dec)

    def add(name: str, passed: bool, weight: int) -> None:
        nonlocal sc
        if passed:
            sc += weight
        else:
            fails.append(name)

    # Tier 1 (x3)
    add("Spread>=20", spread >= 20, 3)
    add("Sum80-220", 80 <= sm <= 220, 3)
    add("3+ranges", len(ranges) >= 3, 3)
    add("Cluster", any(g <= 3 for g in gaps), 3)
    add("P1<=14", s[0] <= 14, 3)
    add("P6>=33", s[5] >= 33, 3)

    # Tier 2 (x2)
    add("4+rows", len(rows) >= 4, 2)
    add("RepUnits", any(v >= 2 for v in units.values()), 2)
    add("Balance", 2 <= low <= 4, 2)
    add("Odd2-4", 2 <= odds <= 4, 2)
    add("Anchor", any(n <= 10 for n in s) and any(n >= 35 for n in s), 2)

    # Tier 3 (x1)
    add("7apart", any(n + 7 in ss for n in s), 1)
    add("Consec", any(g == 1 for g in gaps), 1)
    add("Complement", any(50 - n in ss and 50 - n != n for n in s), 1)

    # Inter-draw (x2)
    add("2+nb3", nb3 >= 2, 2)
    add("3+nb3", nb3 >= 3, 2)
    prev_z = set(
        0 if n <= 10 else 1 if n <= 20 else 2 if n <= 30 else 3 if n <= 40 else 4
        for n in p
    )
    miss = set(range(5)) - prev_z
    filled = sum(1 for z in miss if z in ranges)
    add("ZoneRecov", len(miss) == 0 or filled >= 1, 2)
    exited = ps - ss
    entered = ss - ps
    cr = sum(1 for ex in exited if any(abs(en - ex) <= 5 for en in entered))
    add("Replace5", len(exited) == 0 or cr >= len(exited) * 0.5, 2)
    add("DecCarry2", dec_sh >= 2, 2)
    add("P6shift8", abs(s[5] - p[5]) <= 8, 1)

    return ScoredPick(
        pick=s, filter_score=sc, total_score=sc,
        sum_total=sm, odd_count=odds, low_count=low,
        nb3=nb3, fails=fails,
    )


def score_position(
    n: int,
    pos: int,
    last_draw: list[int],
    params: LearnedParams | None = None,
) -> tuple[int, list[str]]:
    """Score a single number for position-level confidence.

    For P1/P6 (anchors): primarily reward proximity to previous draw.
    For P2-P5 (middle): blend proximity with mean-reversion pull when
    the previous draw was extreme (far from the LEARNED average).
    """
    p = params or LearnedParams()
    sc = 0
    reasons = []
    prev_at_pos = sorted(last_draw)[pos]
    avg_at_pos = p.pos_averages[pos]
    deviation = prev_at_pos - avg_at_pos
    dev_magnitude = abs(deviation)
    is_extreme = dev_magnitude > REVERSION_THRESHOLD
    is_middle = pos in REVERSION_POSITIONS

    # Proximity to previous draw
    shift = abs(n - prev_at_pos)
    prox_weight = 0.5 if (is_middle and is_extreme) else 1.0

    if shift <= 1:
        sc += int(5 * prox_weight)
        reasons.append(f"±1 from P{pos + 1}={prev_at_pos}")
    elif shift <= 3:
        sc += int(3 * prox_weight)
        reasons.append(f"±3 from P{pos + 1}={prev_at_pos}")
    elif shift <= 5:
        sc += 1
        reasons.append(f"±5 from P{pos + 1}")

    # Mean reversion (P2-P5 only, when previous was extreme)
    if is_middle and is_extreme:
        dist_n_to_avg = abs(n - avg_at_pos)

        reversion_frac = 1.0 - (dist_n_to_avg / dev_magnitude) if dev_magnitude > 0 else 0

        if reversion_frac > 0:
            bonus = min(6, int(reversion_frac * dev_magnitude * 0.8))
            if bonus > 0:
                sc += bonus
                reasons.append(f"revert→avg({avg_at_pos:.0f}) +{bonus}")
        elif dist_n_to_avg <= 3:
            sc += 2
            reasons.append(f"near avg({avg_at_pos:.0f})")

    # Neighborhood echo (near ANY number in last draw)
    for pd in last_draw:
        if 0 < abs(n - pd) <= 2:
            sc += 3
            reasons.append(f"±2 of {pd}")
            break
        elif 0 < abs(n - pd) <= 3:
            sc += 2
            reasons.append(f"±3 of {pd}")
            break

    # Repeat from last draw
    if n in last_draw:
        sc += 2
        reasons.append("repeat")

    # Complement potential
    comp = 50 - n
    if 1 <= comp <= 49 and comp != n:
        for pp, (lo, hi) in enumerate(POS_RANGES):
            if pp != pos and lo <= comp <= hi:
                sc += 1
                reasons.append(f"comp={comp}")
                break

    # Hot number bonus (LEARNED from recent draws)
    if n in p.hot_numbers:
        sc += 1
        reasons.append("hot")

    return sc, reasons


def generate_concentrated(
    last_draw: list[int],
    params: LearnedParams | None = None,
) -> list[ScoredPick]:
    """Generate concentrated lines via position-level brute-force."""
    p = params or LearnedParams()

    top_per_pos = []
    for pos in range(6):
        lo, hi = POS_RANGES[pos]
        candidates = []
        for n in range(lo, hi + 1):
            sc, _ = score_position(n, pos, last_draw, p)
            candidates.append((n, sc))
        candidates.sort(key=lambda x: -x[1])
        top_per_pos.append([c[0] for c in candidates[:8]])

    best = []
    for p1 in top_per_pos[0]:
        for p2 in top_per_pos[1]:
            if p2 <= p1:
                continue
            for p3 in top_per_pos[2]:
                if p3 <= p2:
                    continue
                for p4 in top_per_pos[3]:
                    if p4 <= p3:
                        continue
                    for p5 in top_per_pos[4]:
                        if p5 <= p4:
                            continue
                        for p6 in top_per_pos[5]:
                            if p6 <= p5:
                                continue
                            pick = [p1, p2, p3, p4, p5, p6]
                            result = score_filter(pick, last_draw)
                            if result.filter_score < MIN_FILTER_SCORE - 4:
                                continue
                            pos_sc = sum(
                                score_position(pick[i], i, last_draw, p)[0]
                                for i in range(6)
                            )
                            result.position_score = pos_sc
                            result.total_score = result.filter_score + pos_sc
                            best.append(result)

    best.sort(key=lambda x: -x.total_score)
    return best[:5]


def generate_diverse(
    last_draw: list[int],
    n_lines: int = 3,
    exclude: set[int] | None = None,
    seed: int | None = None,
    n_candidates: int = CANDIDATE_COUNT,
) -> tuple[list[ScoredPick], int]:
    """Generate diverse balanced lines."""
    if seed is not None:
        random.seed(seed)

    candidates = []
    for _ in range(n_candidates):
        pick = sorted(random.sample(POOL, 6))
        result = score_filter(pick, last_draw)
        if result.filter_score >= MIN_FILTER_SCORE:
            candidates.append(result)

    candidates.sort(key=lambda x: -x.filter_score)
    total_passed = len(candidates)

    used = set(exclude) if exclude else set()
    lines = []
    for c in candidates:
        if len(lines) >= n_lines:
            break
        overlap = sum(1 for n in c.pick if n in used)
        if overlap <= 1:
            lines.append(c)
            used.update(c.pick)

    return lines, total_passed


def generate_low_skew(
    last_draw: list[int],
    n_lines: int = 1,
    exclude: set[int] | None = None,
    seed: int | None = None,
    n_candidates: int = CANDIDATE_COUNT,
) -> list[ScoredPick]:
    """Generate low-skew lines (5+ numbers <= 25)."""
    if seed is not None:
        random.seed(seed)

    candidates = []
    for _ in range(n_candidates):
        pick = sorted(random.sample(POOL, 6))
        result = score_filter(pick, last_draw)
        if result.low_count >= 5 and result.filter_score >= MIN_SKEW_SCORE:
            candidates.append(result)

    candidates.sort(key=lambda x: (-x.filter_score, -x.nb3))

    used = set(exclude) if exclude else set()
    lines = []
    for c in candidates:
        if len(lines) >= n_lines:
            break
        overlap = sum(1 for n in c.pick if n in used)
        if overlap <= 2:
            lines.append(c)
            used.update(c.pick)

    return lines


def generate_high_skew(
    last_draw: list[int],
    n_lines: int = 1,
    exclude: set[int] | None = None,
    seed: int | None = None,
    n_candidates: int = CANDIDATE_COUNT,
) -> list[ScoredPick]:
    """Generate high-skew lines (5+ numbers >= 25).

    Mirror of low-skew: catches draws like #4198 [23,27,31,38,42,47]
    where the draw packs into the upper half with one low outlier.
    """
    if seed is not None:
        random.seed(seed)

    candidates = []
    for _ in range(n_candidates):
        pick = sorted(random.sample(POOL, 6))
        result = score_filter(pick, last_draw)
        high_count = sum(1 for n in pick if n >= 25)
        if high_count >= 5 and result.filter_score >= MIN_SKEW_SCORE:
            candidates.append(result)

    candidates.sort(key=lambda x: (-x.filter_score, -x.nb3))

    used = set(exclude) if exclude else set()
    lines = []
    for c in candidates:
        if len(lines) >= n_lines:
            break
        overlap = sum(1 for n in c.pick if n in used)
        if overlap <= 2:
            lines.append(c)
            used.update(c.pick)

    return lines


def generate_synthesis(
    last_draw: list[int],
    all_lines: list[ScoredPick],
    concentrated_nums: set[int] | None = None,
) -> list[ScoredPick]:
    """Build line 6 STRICTLY from numbers already in lines 1-5.

    Pool = union of all numbers across the source lines. No new numbers.
    Picks the best 6-number combo from that pool, scored by consensus
    frequency + filter rules. Must differ from concentrated by >= 3 numbers.
    """
    conc = concentrated_nums or set()

    # Pool = ONLY numbers that exist in lines 1-5
    num_freq = Counter()
    for line in all_lines:
        for n in line.pick:
            num_freq[n] += 1

    pool = sorted(num_freq.keys())
    if len(pool) < 6:
        return []

    # Cap pool for combinatorial tractability: keep highest-consensus
    # numbers first (ties broken by nearness to last draw). C(18,6)=18k.
    if len(pool) > 18:
        def _pool_rank(n: int) -> tuple:
            near = min(abs(n - p) for p in last_draw)
            return (-num_freq[n], near)
        pool = sorted(pool, key=_pool_rank)[:18]

    best = []
    for combo in combinations(pool, 6):
        pick = sorted(combo)

        # Must differ from concentrated by at least 3
        overlap = len(set(pick) & conc)
        if overlap > 3:
            continue

        result = score_filter(pick, last_draw)
        if result.filter_score < MIN_FILTER_SCORE - 4:
            continue

        # Score: consensus frequency is king
        consensus = sum(num_freq[n] for n in pick)
        multi_bonus = sum(10 for n in pick if num_freq[n] >= 2)
        diversity_bonus = (6 - overlap) * 2

        synth_score = (
            result.filter_score + consensus * 5 + multi_bonus + diversity_bonus
        )
        result.position_score = consensus
        result.total_score = synth_score
        best.append(result)

    best.sort(key=lambda x: -x.total_score)
    return best[:1]


def generate_all(
    last_draw: list[int],
    seed: int | None = None,
    history: list[list[int]] | None = None,
) -> tuple[list[ScoredPick], list[ScoredPick], list[ScoredPick], list[ScoredPick], int]:
    """Generate concentrated + diverse + low-skew + synthesis lines.

    Args:
        last_draw: The most recent draw's 6 winning numbers.
        seed: Optional random seed for reproducibility.
        history: Past draws (newest first, each 6 winning numbers,
                 including last_draw itself is fine). When provided,
                 POS_AVERAGES and HOT_NUMBERS are LEARNED from it.

    Returns:
        (concentrated, diverse, low_skew, synthesis, total_candidates_passed)
    """
    # ── Learn weights from history (auto-adapts every prediction) ──
    params = learn_parameters(history)

    concentrated = generate_concentrated(last_draw, params)
    conc_best = concentrated[:1]

    used = set()
    if conc_best:
        used.update(conc_best[0].pick)

    diverse, total_passed = generate_diverse(
        last_draw, n_lines=3, exclude=used, seed=seed,
    )
    used.update(n for line in diverse for n in line.pick)

    # Adaptive skew: direction follows the learned recent regime.
    # Two 1L/5H draws in three (#4198, #4200) is exactly the shape a
    # hardcoded low-skew line kept missing.
    if params.skew_direction == "high":
        low_skew = generate_high_skew(
            last_draw, n_lines=1, exclude=used, seed=(seed or 0) + 1,
        )
    else:
        low_skew = generate_low_skew(
            last_draw, n_lines=1, exclude=used, seed=(seed or 0) + 1,
        )

    # Synthesis: recombination of lines 1-5 ONLY, diverse from concentrated
    all_lines = list(conc_best) + list(diverse) + list(low_skew)
    conc_nums = set(conc_best[0].pick) if conc_best else set()
    synthesis = generate_synthesis(last_draw, all_lines, concentrated_nums=conc_nums)

    return conc_best, diverse, low_skew, synthesis, total_passed


def generate_jackpot(
    last_draw: list[int],
    seed: int | None = None,
    history: list[list[int]] | None = None,
    n_lines: int = 30,
) -> tuple[dict[str, list[ScoredPick]], int]:
    """Jackpot mode: generate 10-40 lines across all strategies.

    Allocation for n_lines=30:
      concentrated 3, diverse 17, low_skew 4, high_skew 4, synthesis 2.
    Diverse lines are generated in WAVES (fresh exclusion set per wave of
    ~8 lines) so coverage layers across the whole 1-49 pool instead of
    exhausting after one pass.

    Returns:
        (groups dict keyed by strategy, total_candidates_passed)
    """
    n_lines = max(10, min(40, n_lines))
    params = learn_parameters(history)

    # ── Allocation (scales proportionally with n_lines) ──
    n_conc = max(2, round(n_lines * 0.10))
    n_skew_lo = max(2, round(n_lines * 0.13))
    n_skew_hi = max(2, round(n_lines * 0.13))
    n_synth = max(1, round(n_lines * 0.07))
    n_div = n_lines - n_conc - n_skew_lo - n_skew_hi - n_synth

    seen: set[tuple] = set()

    def dedupe(lines: list[ScoredPick]) -> list[ScoredPick]:
        out = []
        for line in lines:
            key = tuple(line.pick)
            if key not in seen:
                seen.add(key)
                out.append(line)
        return out

    # 1. Concentrated: top-N from position brute-force
    concentrated = dedupe(generate_concentrated(last_draw, params)[:n_conc])

    # 2. Diverse in waves (each wave gets a fresh exclusion set)
    diverse: list[ScoredPick] = []
    total_passed = 0
    wave_size = 8
    wave_idx = 0
    while len(diverse) < n_div and wave_idx < 6:
        want = min(wave_size, n_div - len(diverse))
        wave_exclude = set(n for l in concentrated for n in l.pick) if wave_idx == 0 else set()
        wave, passed = generate_diverse(
            last_draw, n_lines=want, exclude=wave_exclude,
            seed=(seed or 0) + wave_idx * 101,
            n_candidates=120_000,
        )
        if wave_idx == 0:
            total_passed = passed
        diverse.extend(dedupe(wave))
        wave_idx += 1

    # 3. Low-skew + 4. High-skew
    low_skew = dedupe(generate_low_skew(
        last_draw, n_lines=n_skew_lo, seed=(seed or 0) + 1,
        n_candidates=150_000,
    ))
    high_skew = dedupe(generate_high_skew(
        last_draw, n_lines=n_skew_hi, seed=(seed or 0) + 2,
        n_candidates=150_000,
    ))

    # 5. Synthesis: recombinations of everything above
    source = concentrated + diverse + low_skew + high_skew
    conc_nums = set(n for l in concentrated for n in l.pick)
    synth_all = generate_synthesis(last_draw, source, concentrated_nums=conc_nums)
    # generate_synthesis returns top-1; for more, re-run excluding prior picks
    synthesis: list[ScoredPick] = dedupe(synth_all)
    attempts = 0
    while len(synthesis) < n_synth and attempts < 5:
        pruned = [l for l in source if tuple(l.pick) not in
                  {tuple(s.pick) for s in synthesis}]
        extra = generate_synthesis(
            last_draw, pruned + synthesis,
            concentrated_nums=conc_nums | set(
                n for s in synthesis for n in s.pick
            ),
        )
        new = dedupe(extra)
        if not new:
            break
        synthesis.extend(new)
        attempts += 1

    groups = {
        "concentrated": concentrated,
        "diverse": diverse,
        "low_skew": low_skew,
        "high_skew": high_skew,
        "synthesis": synthesis[:n_synth],
    }
    logger.info(
        "generate_jackpot: " + ", ".join(
            f"{k}={len(v)}" for k, v in groups.items()
        )
    )
    return groups, total_passed


def run_postmortem(
    prev_draw: list[int],
    actual: list[int],
    predictions: list[list[int]],
    bonus: int | None = None,
) -> dict:
    """Run post-mortem analysis."""
    actual_set = set(actual)

    # Rule check on actual
    result = score_filter(actual, prev_draw)

    # Line-by-line comparison
    line_results = []
    for i, pick in enumerate(predictions):
        ps = set(pick)
        exact = sorted(ps & actual_set)
        near = []
        for a in actual:
            for p in pick:
                if p not in actual_set and 0 < abs(a - p) <= 1:
                    near.append({"our": p, "actual": a, "diff": a - p})
        bonus_hit = bonus in ps if bonus else False

        match_count = len(exact)
        prize = None
        if match_count == 6:
            prize = "Group 1 (Jackpot)"
        elif match_count == 5 and bonus_hit:
            prize = "Group 2"
        elif match_count == 5:
            prize = "Group 3"
        elif match_count == 4 and bonus_hit:
            prize = "Group 4"
        elif match_count == 4:
            prize = "Group 5"
        elif match_count == 3 and bonus_hit:
            prize = "Group 6"
        elif match_count == 3:
            prize = "Group 7"

        line_results.append({
            "line_number": i + 1,
            "numbers": pick,
            "exact_matches": exact,
            "exact_count": match_count,
            "bonus_match": bonus_hit,
            "near_misses": near,
            "prize_group": prize,
        })

    all_our = set(n for pick in predictions for n in pick)
    covered = sorted(actual_set & all_our)
    missed = sorted(actual_set - all_our)

    our_avg_sum = sum(sum(p) for p in predictions) / len(predictions)
    actual_sum = sum(actual)
    low = sum(1 for n in actual if n <= 25)

    if abs(our_avg_sum - actual_sum) > 40:
        root_cause = f"Sum mismatch: actual {actual_sum} vs our avg {our_avg_sum:.0f}."
    elif low >= 5:
        root_cause = f"Low-skew draw ({low}/6 numbers <= 25). Balance filter excluded this shape."
    else:
        root_cause = "Draw fell within expected structural range."

    return {
        "previous_draw": prev_draw,
        "actual_winning": actual,
        "actual_bonus": bonus,
        "rules_passed": 20 - len(result.fails),
        "rules_total": 20,
        "fails": result.fails,
        "line_results": line_results,
        "numbers_we_covered": covered,
        "numbers_we_missed": missed,
        "root_cause": root_cause,
    }
