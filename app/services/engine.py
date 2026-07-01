"""Core Toto filter engine — scoring, generation, post-mortem."""

import random
from collections import Counter
from dataclasses import dataclass, field

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

HOT_NUMBERS = {3, 10, 15, 22, 32, 34, 35, 37, 38, 39, 48}

# Long-term position averages (validated across 100 draws)
POS_AVERAGES = [6.6, 13.6, 21.4, 27.8, 35.6, 42.7]

# Mean-reversion threshold: if prev deviates more than this from avg, apply pull
REVERSION_THRESHOLD = 3.0

# Mean-reversion applies to middle positions only (P2-P5, 0-indexed: 1-4)
REVERSION_POSITIONS = {1, 2, 3, 4}


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

    # Tier 1 (×3)
    add("Spread>=20", spread >= 20, 3)
    add("Sum80-220", 80 <= sm <= 220, 3)
    add("3+ranges", len(ranges) >= 3, 3)
    add("Cluster", any(g <= 3 for g in gaps), 3)
    add("P1<=14", s[0] <= 14, 3)
    add("P6>=33", s[5] >= 33, 3)

    # Tier 2 (×2)
    add("4+rows", len(rows) >= 4, 2)
    add("RepUnits", any(v >= 2 for v in units.values()), 2)
    add("Balance", 2 <= low <= 4, 2)
    add("Odd2-4", 2 <= odds <= 4, 2)
    add("Anchor", any(n <= 10 for n in s) and any(n >= 35 for n in s), 2)

    # Tier 3 (×1)
    add("7apart", any(n + 7 in ss for n in s), 1)
    add("Consec", any(g == 1 for g in gaps), 1)
    add("Complement", any(50 - n in ss and 50 - n != n for n in s), 1)

    # Inter-draw (×2)
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


def score_position(n: int, pos: int, last_draw: list[int]) -> tuple[int, list[str]]:
    """Score a single number for position-level confidence.

    For P1/P6 (anchors): primarily reward proximity to previous draw.
    For P2-P5 (middle): blend proximity with mean-reversion pull when
    the previous draw was extreme (far from long-term average).
    """
    sc = 0
    reasons = []
    prev_at_pos = sorted(last_draw)[pos]
    avg_at_pos = POS_AVERAGES[pos]
    deviation = prev_at_pos - avg_at_pos  # positive = prev was high, negative = low
    dev_magnitude = abs(deviation)
    is_extreme = dev_magnitude > REVERSION_THRESHOLD
    is_middle = pos in REVERSION_POSITIONS

    # ── Proximity to previous draw ──
    # Full weight for anchors (P1, P6); reduced for middle positions when extreme
    shift = abs(n - prev_at_pos)
    prox_weight = 1.0
    if is_middle and is_extreme:
        # Dampen proximity reward — the draw is likely to move away
        prox_weight = 0.5

    if shift <= 1:
        prox_pts = int(5 * prox_weight)
        sc += prox_pts
        reasons.append(f"±1 from P{pos + 1}={prev_at_pos}")
    elif shift <= 3:
        prox_pts = int(3 * prox_weight)
        sc += prox_pts
        reasons.append(f"±3 from P{pos + 1}={prev_at_pos}")
    elif shift <= 5:
        sc += 1
        reasons.append(f"±5 from P{pos + 1}")

    # ── Mean reversion (P2-P5 only, when previous was extreme) ──
    if is_middle and is_extreme:
        dist_n_to_avg = abs(n - avg_at_pos)
        dist_prev_to_avg = dev_magnitude

        # How much closer to avg is this candidate vs prev?
        reversion_frac = 1.0 - (dist_n_to_avg / dist_prev_to_avg) if dist_prev_to_avg > 0 else 0

        if reversion_frac > 0:
            # Scale bonus: larger deviation = stronger pull (max +6)
            bonus = min(6, int(reversion_frac * dev_magnitude * 0.8))
            if bonus > 0:
                sc += bonus
                reasons.append(f"revert→avg({avg_at_pos:.0f}) +{bonus}")
        elif dist_n_to_avg <= 3:
            # Even if not "between," reward being near the average
            sc += 2
            reasons.append(f"near avg({avg_at_pos:.0f})")

    # ── Neighborhood echo (near ANY number in last draw) ──
    for p in last_draw:
        if 0 < abs(n - p) <= 2:
            sc += 3
            reasons.append(f"±2 of {p}")
            break
        elif 0 < abs(n - p) <= 3:
            sc += 2
            reasons.append(f"±3 of {p}")
            break

    # ── Repeat from last draw ──
    if n in last_draw:
        sc += 2
        reasons.append("repeat")

    # ── Complement potential ──
    comp = 50 - n
    if 1 <= comp <= 49 and comp != n:
        for pp, (lo, hi) in enumerate(POS_RANGES):
            if pp != pos and lo <= comp <= hi:
                sc += 1
                reasons.append(f"comp={comp}")
                break

    # ── Hot number bonus ──
    if n in HOT_NUMBERS:
        sc += 1
        reasons.append("hot")

    return sc, reasons


def generate_concentrated(last_draw: list[int]) -> list[ScoredPick]:
    """Generate concentrated lines via position-level brute-force."""
    top_per_pos = []
    for pos in range(6):
        lo, hi = POS_RANGES[pos]
        candidates = []
        for n in range(lo, hi + 1):
            sc, _ = score_position(n, pos, last_draw)
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
                                score_position(pick[i], i, last_draw)[0]
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
) -> tuple[list[ScoredPick], int]:
    """Generate diverse balanced lines."""
    if seed is not None:
        random.seed(seed)

    candidates = []
    for _ in range(CANDIDATE_COUNT):
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
) -> list[ScoredPick]:
    """Generate low-skew lines (5+ numbers ≤ 25)."""
    if seed is not None:
        random.seed(seed)

    candidates = []
    for _ in range(CANDIDATE_COUNT):
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


def generate_synthesis(
    last_draw: list[int],
    all_lines: list[ScoredPick],
) -> list[ScoredPick]:
    """Generate a synthesis line from the collective wisdom of all other lines.

    Counts how often each number appears across lines, scores by frequency +
    position confidence, then brute-forces the best 6-number combo that
    passes the filter rules.
    """
    from collections import Counter as _Counter

    # Count number frequency across all lines
    num_freq = _Counter()
    for line in all_lines:
        for n in line.pick:
            num_freq[n] += 1

    # Score each number: frequency weight + position score at best position
    num_scores = {}
    for n in range(1, 50):
        freq = num_freq.get(n, 0)
        freq_score = freq * 4  # strong weight for consensus

        # Find best position score
        best_pos_sc = 0
        for pos in range(6):
            lo, hi = POS_RANGES[pos]
            if lo <= n <= hi:
                sc, _ = score_position(n, pos, last_draw)
                best_pos_sc = max(best_pos_sc, sc)

        num_scores[n] = freq_score + best_pos_sc

    # Take top 18 candidates (enough for combinatorial search)
    top_nums = sorted(num_scores.keys(), key=lambda x: -num_scores[x])[:18]

    # Brute-force all 6-combos from top candidates
    from itertools import combinations as _combinations

    best = []
    for combo in _combinations(top_nums, 6):
        pick = sorted(combo)
        result = score_filter(pick, last_draw)
        if result.filter_score < MIN_FILTER_SCORE - 2:
            continue

        # Synthesis score = filter + sum of individual num_scores
        synth_score = result.filter_score + sum(num_scores[n] for n in pick)
        result.position_score = sum(num_scores[n] for n in pick)
        result.total_score = synth_score
        best.append(result)

    best.sort(key=lambda x: -x.total_score)
    return best[:1]


def generate_all(
    last_draw: list[int],
    seed: int | None = None,
) -> tuple[list[ScoredPick], list[ScoredPick], list[ScoredPick], list[ScoredPick], int]:
    """Generate concentrated + diverse + low-skew + synthesis lines.

    Returns:
        (concentrated, diverse, low_skew, synthesis, total_candidates_passed)
    """
    concentrated = generate_concentrated(last_draw)
    conc_best = concentrated[:1]

    used = set()
    if conc_best:
        used.update(conc_best[0].pick)

    diverse, total_passed = generate_diverse(
        last_draw, n_lines=3, exclude=used, seed=seed,
    )
    used.update(n for line in diverse for n in line.pick)

    low_skew = generate_low_skew(
        last_draw, n_lines=1, exclude=used, seed=(seed or 0) + 1,
    )

    # Synthesis: combine wisdom of all 5 lines into one
    all_lines = list(conc_best) + list(diverse) + list(low_skew)
    synthesis = generate_synthesis(last_draw, all_lines)

    return conc_best, diverse, low_skew, synthesis, total_passed


def run_postmortem(
    prev_draw: list[int],
    actual: list[int],
    predictions: list[list[int]],
    bonus: int | None = None,
) -> dict:
    """Run post-mortem analysis."""
    actual_set = set(actual)
    actual_bonus = set(actual + ([bonus] if bonus else []))

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
        root_cause = f"Low-skew draw ({low}/6 numbers ≤ 25). Balance filter excluded this shape."
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
