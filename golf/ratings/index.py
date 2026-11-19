"""
A player's handicap index from their rounds (docs/competitive/design.md, The handicap
index, New players and Sandbagging).

- A round's differential is (adjusted gross - course rating) / slope: the index that
  round played to. Its adjusted gross counts a hole no worse than a net double bogey,
  under the course handicap of the index the player held before it.
- The index is the mean of the last 8 differentials, blended with a starting index worth
  4 rounds at first and half a round less with each of the player's own, so it is gone
  once they have 8.
- Past 3 strokes over the low mark a rise counts half, and it never passes 5 over. The
  low mark is the lowest index of the last 365 days, once a player has 20 rounds.
- With exceptional score reduction on, a round 7 or more strokes better than the index
  takes 1 or 2 strokes off every differential in the record.

Every number is in `IndexRules`. Which rounds count is the caller's: on the site, a
player's daily rounds and no others. Each round's rating is its seed's stored
rating, made once when the seed was generated and never redone.

The site scores each round once, at scan (`score`), and stores its differential and cut
(ADR 0034). A player's index is then read from those stored numbers (`replay`), which
never goes back to strokes or ratings; `compute` scores every round from its strokes,
for the backtest and for the one replay that filled the stored rows.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from .handicap import course_handicap, round_half_up, strokes_received
from .params import IndexRules
from .seed import SeedRating


@dataclass(frozen=True)
class ScoredRound:
    played_at: datetime
    rating: SeedRating
    strokes: tuple[int, ...]


@dataclass(frozen=True)
class IndexState:
    index: float
    low_mark: float | None
    rounds_counted: int
    differentials: tuple[float, ...]
    #: the index after each counted round
    history: tuple[tuple[datetime, float], ...]


def capped_holes(
    strokes: Sequence[int], rating: SeedRating, index: float, over: int
) -> list[int]:
    handicap = round_half_up(course_handicap(index, rating))
    received = strokes_received(handicap, [h.stroke_index for h in rating.holes])
    return [
        min(s, h.par + over + r)
        for s, h, r in zip(strokes, rating.holes, received, strict=True)
    ]


def differential(
    strokes: Sequence[int], rating: SeedRating, index_before: float, rules: IndexRules
) -> float:
    gross = sum(capped_holes(strokes, rating, index_before, rules.hole_cap_over_par))
    return (gross - rating.course_rating) / rating.slope


def start_weight(rules: IndexRules, rounds: int) -> float:
    """The starting index's weight, in rounds, after `rounds` of the player's own."""
    return max(0.0, rules.start_weight * (1 - rounds / rules.window))


def counting(differentials: Sequence[float], rules: IndexRules) -> list[int]:
    """The positions, oldest first, of the differentials the index counts."""
    if not differentials:
        return []
    if rules.best_of is None:
        return list(
            range(max(0, len(differentials) - rules.window), len(differentials))
        )
    recent = range(max(0, len(differentials) - rules.best_of), len(differentials))
    # a short record keeps the same share: 8 of 10 keeps 80% of its rounds
    take = max(1, int(len(recent) * rules.window / rules.best_of + 0.5))
    best = sorted(recent, key=lambda i: (differentials[i], i))[:take]
    return sorted(best)


def blend(differentials: Sequence[float], rules: IndexRules) -> float:
    """The uncapped index from every differential so far, oldest first."""
    counted = [differentials[i] for i in counting(differentials, rules)]
    weight = start_weight(rules, len(differentials))
    return (weight * rules.start_index + sum(counted)) / (weight + len(counted))


def low_mark(
    history: Sequence[tuple[datetime, float]], at: datetime, rules: IndexRules
) -> float | None:
    if len(history) < rules.low_min_rounds:
        return None
    since = at - timedelta(days=rules.low_window_days)
    window = [index for when, index in history if when >= since]
    return min(window) if window else None


def capped(index: float, low: float | None, rules: IndexRules) -> float:
    if low is None:
        return index
    if rules.soft_cap is not None and index - low > rules.soft_cap:
        index = low + rules.soft_cap + (index - low - rules.soft_cap) / 2
    if rules.hard_cap is not None:
        index = min(index, low + rules.hard_cap)
    return index


def exceptional_cut(diff: float, index_before: float, rules: IndexRules) -> float:
    """The exceptional score reduction a differential earns, 0 if none."""
    if rules.exceptional is None:
        return 0.0
    better = index_before - diff
    small, large = rules.exceptional
    if better >= large:
        return rules.exceptional_cut[1]
    if better >= small:
        return rules.exceptional_cut[0]
    return 0.0


@dataclass(frozen=True)
class RoundStep:
    """One round's part in an index: what it was scored with and what it made."""

    played_at: datetime
    gross: int
    adjusted_gross: int
    index_before: float
    #: the differential as the round was scored, before any cut
    differential: float
    #: the exceptional score reduction this round took off the record, 0 if none
    cut: float
    low_mark: float | None
    #: the index before the caps above the low mark
    uncapped: float
    index_after: float


def initial(rules: IndexRules) -> IndexState:
    return IndexState(rules.start_index, None, 0, (), ())


def advance(
    state: IndexState, played_at: datetime, diff: float, cut: float, rules: IndexRules
) -> tuple[IndexState, float]:
    """The index after one more round with this differential, before any cut, and the
    cut it earned, which comes off every differential in the record. Returns the new
    state and the index before the caps above the low mark."""
    record = rules.best_of or rules.window
    diffs = [*state.differentials, diff]
    for i in range(max(0, len(diffs) - record), len(diffs)):
        diffs[i] -= cut
    low = low_mark(state.history, played_at, rules)
    uncapped = blend(diffs, rules)
    index = capped(uncapped, low, rules)
    history = (*state.history, (played_at, index))
    return IndexState(index, low, len(diffs), tuple(diffs), history), uncapped


def score(
    state: IndexState, r: ScoredRound, rules: IndexRules
) -> tuple[IndexState, RoundStep]:
    """Score one round from the index the player holds: its adjusted gross, differential
    and cut, and the index after it."""
    adjusted = sum(
        capped_holes(r.strokes, r.rating, state.index, rules.hole_cap_over_par)
    )
    raw = differential(r.strokes, r.rating, state.index, rules)
    cut = exceptional_cut(raw, state.index, rules)
    after, uncapped = advance(state, r.played_at, raw, cut, rules)
    step = RoundStep(
        r.played_at,
        sum(r.strokes),
        adjusted,
        state.index,
        raw,
        cut,
        after.low_mark,
        uncapped,
        after.index,
    )
    return after, step


def compute_steps(
    rounds: Sequence[ScoredRound], rules: IndexRules
) -> tuple[IndexState, tuple[RoundStep, ...]]:
    """The index after every round, in the order played, and each round's step."""
    state = initial(rules)
    steps: list[RoundStep] = []
    for r in sorted(rounds, key=lambda r: r.played_at):
        state, step = score(state, r, rules)
        steps.append(step)
    return state, tuple(steps)


def compute(rounds: Sequence[ScoredRound], rules: IndexRules) -> IndexState:
    """The index after every round, in the order played."""
    return compute_steps(rounds, rules)[0]


@dataclass(frozen=True)
class StoredRound:
    """What a round stored when it was scored: its differential before any cut, and the
    cut it earned."""

    played_at: datetime
    differential: float
    cut: float


def replay(
    rounds: Sequence[StoredRound], rules: IndexRules
) -> tuple[IndexState, tuple[IndexState, ...]]:
    """The index from stored rounds, in the order given, and the state after each. While
    the rounds and rules are those they were scored with, it is the index `compute`
    gives."""
    state = initial(rules)
    states: list[IndexState] = []
    for r in rounds:
        state, _ = advance(state, r.played_at, r.differential, r.cut, rules)
        states.append(state)
    return state, tuple(states)
