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
rating, made once when the seed was generated and never redone, so a round's
differential never changes and an index moves only when its player plays.
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


def blend(differentials: Sequence[float], rules: IndexRules) -> float:
    """The uncapped index from every differential so far, oldest first."""
    if rules.best_of is None:
        counted = list(differentials[-rules.window :])
    else:
        recent = sorted(differentials[-rules.best_of :])
        # a short record keeps the same share: 8 of 10 keeps 80% of its rounds
        take = max(1, int(len(recent) * rules.window / rules.best_of + 0.5))
        counted = recent[:take]
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


def initial(rules: IndexRules) -> IndexState:
    return IndexState(rules.start_index, None, 0, (), ())


def compute(rounds: Sequence[ScoredRound], rules: IndexRules) -> IndexState:
    """The index after every round, in the order played."""
    state = initial(rules)
    diffs: list[float] = []
    history: list[tuple[datetime, float]] = []
    low = None
    record = rules.best_of or rules.window
    for r in sorted(rounds, key=lambda r: r.played_at):
        diffs.append(differential(r.strokes, r.rating, state.index, rules))
        cut = exceptional_cut(diffs[-1], state.index, rules)
        for i in range(max(0, len(diffs) - record), len(diffs)):
            diffs[i] -= cut
        low = low_mark(history, r.played_at, rules)
        index = capped(blend(diffs, rules), low, rules)
        history.append((r.played_at, index))
        state = IndexState(index, low, len(diffs), tuple(diffs), tuple(history))
    return state
