"""Stroke indexes, daily handicaps, strokes received, the index rules and the net
formats."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from golf.ratings import formats
from golf.ratings.handicap import daily_handicap, match_strokes, strokes_received
from golf.ratings.index import (
    ScoredRound,
    capped,
    compute,
    compute_steps,
    counting,
    differential,
)
from golf.ratings.params import IndexRules
from golf.ratings.seed import HoleRating, SeedRating, stroke_indexes

PARS = [4, 3, 5, 4, 4, 3, 4, 5, 4] * 2


def flat(slope: float = 1.0, over: float = 0.0) -> SeedRating:
    """Every hole rated its par plus `over` / 18."""
    order = stroke_indexes([(p, p + over / 18, slope) for p in PARS])
    return SeedRating(
        tuple(
            HoleRating(p, p + over / 18, slope, si)
            for p, si in zip(PARS, order, strict=True)
        )
    )


def test_stroke_index_ranks_difficulty_for_par():
    holes = [(3, 4.0, 0.8), (5, 5.5, 1.4), (4, 4.6, 1.1), (4, 4.6, 1.2)]
    assert stroke_indexes(holes) == [1, 4, 3, 2]
    assert stroke_indexes(holes, by="spread") == [4, 1, 3, 2]


def test_strokes_received_laps_and_gives_back():
    si = list(range(1, 19))
    assert strokes_received(20, si) == [2, 2] + [1] * 16
    assert strokes_received(-3, si) == [0] * 15 + [-1] * 3
    assert strokes_received(0, si) == [0] * 18


def test_match_gives_the_difference_to_the_weaker():
    si = list(range(1, 19))
    a, b = match_strokes(10, 7, si)
    assert sum(a) == 3 and sum(b) == 0
    a, b = match_strokes(-2, 4, si)
    assert sum(a) == 0 and b[:6] == [1] * 6


def test_daily_handicap_rounds_half_up():
    rating = flat(slope=1.0, over=0.5)
    assert daily_handicap(0.0, rating, 1.0) == 1
    assert daily_handicap(10.0, flat(slope=1.25), 0.9) == round(11.25)


def test_differential_caps_a_hole_at_net_double_bogey():
    rating = flat()
    blowup = [p for p in PARS]
    blowup[0] += 6
    assert differential(blowup, rating, 0.0, IndexRules()) == pytest.approx(2.0)


def rounds_at(over: list[int]) -> list[ScoredRound]:
    start = datetime(2026, 11, 1)
    out = []
    for i, x in enumerate(over):
        strokes = list(PARS)
        for h in range(x):
            strokes[h % 18] += 1
        out.append(ScoredRound(start + timedelta(days=i), flat(), tuple(strokes)))
    return out


LAST_8 = IndexRules(best_of=None, start_index=4.5, exceptional=None)


@pytest.mark.parametrize(("played", "expected"), [(1, 7.9), (4, 14.8), (8, 20.0)])
def test_starting_index_fades(played, expected):
    state = compute(rounds_at([20] * played), LAST_8)
    assert state.index == pytest.approx(expected, abs=0.05)


@pytest.mark.parametrize(("played", "expected"), [(1, 11.3), (4, 9.3), (8, 7.9)])
def test_a_thrown_first_round_is_diluted(played, expected):
    state = compute(rounds_at(([35] + [4] * 7)[:played]), LAST_8)
    assert state.index == pytest.approx(expected, abs=0.1)


def test_caps_above_the_low_mark():
    rules = IndexRules()
    assert capped(4.0, 0.0, rules) == pytest.approx(3.5)
    assert capped(20.0, 0.0, rules) == pytest.approx(5.0)
    assert capped(20.0, None, rules) == 20.0


def test_low_mark_needs_enough_rounds():
    rules = IndexRules(low_min_rounds=20)
    assert compute(rounds_at([0] * 20), rules).low_mark is None
    assert compute(rounds_at([0] * 21), rules).low_mark is not None


def test_stableford_and_match():
    pars = [4, 3, 5]
    assert formats.stableford([4, 5, 3], pars, [0, 1, 0]) == [2, 1, 4]
    assert formats.stableford([9, 3, 5], pars, [0, 0, 0]) == [0, 2, 2]
    assert formats.match([4, 3, 5], [5, 3, 4]) == (1, 1)


def test_exceptional_score_reduction_cuts_the_record():
    plain = IndexRules(best_of=None, start_weight=0.0, exceptional=None)
    esr = IndexRules(best_of=None, start_weight=0.0, exceptional=(7.0, 10.0))
    rounds = rounds_at([10] * 8) + rounds_at([0])[:1]
    rounds[-1] = ScoredRound(
        rounds[7].played_at + timedelta(days=1), rounds[-1].rating, rounds[-1].strokes
    )
    assert compute(rounds, plain).index == pytest.approx(70 / 8)
    # a round 10 better than the index of 10: every differential in the record drops 2
    assert compute(rounds, esr).index == pytest.approx(70 / 8 - 2)


def test_best_8_of_10_drops_the_two_worst():
    rules = IndexRules(start_weight=0.0, exceptional=None)
    state = compute(rounds_at([30, 30] + [6] * 8), rules)
    assert state.index == pytest.approx(6.0)
    assert compute(
        rounds_at([30, 30] + [6] * 8), replace(rules, best_of=None)
    ).index == (pytest.approx(6.0))
    assert compute(rounds_at([6] * 8 + [30, 30]), rules).index == pytest.approx(6.0)
    assert compute(
        rounds_at([6] * 8 + [30, 30]), replace(rules, best_of=None)
    ).index == pytest.approx(12.0)


def test_the_default_rules():
    rules = IndexRules()
    assert (rules.window, rules.best_of, rules.start_index) == (8, 10, 0.0)
    assert rules.exceptional == (7.0, 10.0)


def test_steps_follow_the_history():
    rules = IndexRules()
    rounds = rounds_at([20, 12, 35, 8, 15])
    state, steps = compute_steps(rounds, rules)
    assert [s.index_after for s in steps] == [i for _, i in state.history]
    assert [s.index_before for s in steps] == [rules.start_index] + [
        s.index_after for s in steps[:-1]
    ]
    assert [s.gross for s in steps] == [sum(r.strokes) for r in rounds]
    assert compute(rounds, rules) == state


def test_a_step_records_the_hole_cap_and_the_cut():
    esr = IndexRules(best_of=None, start_weight=0.0, exceptional=(7.0, 10.0))
    rounds = rounds_at([10] * 8 + [0])
    _, steps = compute_steps(rounds, esr)
    assert [s.cut for s in steps] == [0.0] * 8 + [2.0]
    assert steps[-1].differential == pytest.approx(0.0)
    blowup = rounds_at([0])
    strokes = list(blowup[0].strokes)
    strokes[0] += 6
    blowup[0] = replace(blowup[0], strokes=tuple(strokes))
    _, (step,) = compute_steps(blowup, IndexRules())
    assert (step.gross - sum(PARS), step.adjusted_gross - sum(PARS)) == (6, 2)


def test_counting_keeps_the_best_share():
    rules = IndexRules()
    assert counting([], rules) == []
    assert counting([5.0, 1.0, 9.0, 3.0, 4.0], rules) == [0, 1, 3, 4]
    diffs = [30.0, 30.0] + [6.0] * 8 + [1.0, 40.0]
    assert counting(diffs, rules) == [2, 3, 4, 5, 6, 7, 8, 10]
    assert counting(diffs, replace(rules, best_of=None)) == list(range(4, 12))
