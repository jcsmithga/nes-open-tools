"""The daily's formats: the standing with countback, skins with carryover, matches over
all 18, and a field too small to win."""

from dataclasses import replace

from golf.ratings.formats import (
    FieldCard,
    countback_key,
    day_results,
    skins,
    standing,
)
from golf.ratings.params import IndexRules
from golf.ratings.rules import DailyRules

RULES = DailyRules(allowance=0.9, index=IndexRules())
STROKE_INDEXES = list(range(1, 19))
COUNTBACK = (9, 6, 3, 1)


def points(front: int, back: list[int]) -> list[int]:
    """Nine holes of `front` points, then the back nine as given."""
    return [front] * 9 + back


def test_countback_reads_the_last_9_6_3_and_1_holes():
    pts = list(range(18))
    assert countback_key(pts, COUNTBACK) == (153, 117, 87, 48, 17)


def test_a_tie_on_points_is_broken_by_the_back_nine():
    by_back = {
        "ann": points(2, [3] + [2] * 8),  # 37 points, 19 on the back
        "bo": points(3, [1] * 9),  # 36 points
        "cy": [2] * 8 + [3] + [2] * 9,  # 37 points, 18 on the back
    }
    assert standing(by_back, COUNTBACK) == {"ann": 1, "cy": 2, "bo": 3}


def test_the_last_six_breaks_a_tie_the_back_nine_does_not():
    ann = points(2, [3, 1, 2, 2, 2, 2, 2, 2, 2])  # 18 on the back, 12 on the last 6
    bo = points(2, [1, 3, 2, 2, 2, 2, 2, 2, 2])
    cy = points(2, [2, 2, 1, 3, 2, 2, 2, 2, 2])  # 13 on the last 6
    assert standing({"ann": ann, "bo": bo, "cy": cy}, COUNTBACK) == {
        "cy": 1,
        "ann": 2,
        "bo": 2,
    }


def test_players_tied_past_countback_share_and_the_next_takes_the_place_after():
    same = [2] * 18
    assert standing({"ann": same, "bo": same, "cy": [1] * 18}, COUNTBACK) == {
        "ann": 1,
        "bo": 1,
        "cy": 3,
    }


def test_a_tied_holes_skin_carries_to_the_next_hole_won():
    nets = {"ann": [4, 4, 3] + [5] * 15, "bo": [4, 4, 4] + [5] * 15}
    # holes 1 and 2 tie; ann takes hole 3 and the two carried; 4 to 18 tie
    assert skins(nets) == {"ann": 3, "bo": 0}


def test_skins_carried_past_the_last_hole_are_won_by_nobody():
    nets = {"ann": [3] + [4] * 17, "bo": [4] * 18}
    assert skins(nets) == {"ann": 1, "bo": 0}


def test_without_carryover_a_tied_holes_skin_is_lost():
    nets = {"ann": [4, 4, 3] + [5] * 15, "bo": [4, 4, 4] + [5] * 15}
    assert skins(nets, carryover=False) == {"ann": 1, "bo": 0}


def card(player: str, strokes: list[int], handicap: int = 0) -> FieldCard:
    return FieldCard(
        player=player,
        strokes=tuple(strokes),
        handicap=handicap,
        nets=tuple(strokes),
        points=tuple(max(0, 2 + 4 - s) for s in strokes),
    )


def test_a_field_of_one_wins_nothing():
    results = day_results([card("ann", [3] * 18)], STROKE_INDEXES, RULES)
    ann = results.players["ann"]
    assert (ann.position, ann.won, ann.skins) == (1, False, 0)
    assert ann.points == 54
    assert results.matches == []


def test_a_field_of_two_has_a_winner_skins_and_a_match():
    results = day_results(
        [card("ann", [4] * 17 + [3]), card("bo", [4] * 18)], STROKE_INDEXES, RULES
    )
    assert results.players["ann"].won and not results.players["bo"].won
    assert results.players["ann"].skins == 18  # 17 carried onto the 18th
    (only,) = results.matches
    assert (only.holes_a, only.holes_b, only.winner, only.margin) == (1, 0, "ann", 1)


def test_tied_for_first_after_countback_both_win():
    same = [4] * 18
    results = day_results([card("ann", same), card("bo", same)], STROKE_INDEXES, RULES)
    assert results.players["ann"].won and results.players["bo"].won
    assert results.matches[0].winner is None


def test_a_match_gives_the_weaker_player_the_difference_by_stroke_index():
    # bo's 5 on every hole is a net 4 on the two hardest holes with 2 strokes
    ann = card("ann", [4] * 18, handicap=1)
    bo = card("bo", [5] * 18, handicap=3)
    (only,) = day_results([ann, bo], STROKE_INDEXES, RULES).matches
    assert (only.holes_a, only.holes_b) == (16, 0)


def test_a_format_left_out_of_the_rules_is_not_scored():
    rules = replace(RULES, formats=("standing",))
    results = day_results(
        [card("ann", [3] * 18), card("bo", [4] * 18)], STROKE_INDEXES, rules
    )
    assert results.matches == [] and results.players["ann"].skins == 0
