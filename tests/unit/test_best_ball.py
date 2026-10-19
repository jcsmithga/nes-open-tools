"""Best-ball cards from a scores CSV: reading it, matching pairs, and the cards."""

import pytest

from golf.randomizer.best_ball import (
    Matching,
    Player,
    ScoresError,
    best_ball,
    card,
    leaderboard,
    read_scores,
    report,
    to_par,
)
from server.admin import ScoreRow, SeedScores
from server.routes.admin_pages import scores_csv_text

PARS = (4, 3, 4, 5, 3, 5, 4, 4, 4) * 2


def round_row(name: str, strokes, kind_slot: int = 0, username: str = "") -> ScoreRow:
    return ScoreRow(
        public_id="0000000001",
        user_name=name,
        username=username or name.lower(),
        slot=kind_slot,
        received_at="2026-10-21T18:00:00Z",
        flagged=False,
        strokes=tuple(strokes),
        total_putts=36,
    )


def csv_text(*rounds: ScoreRow) -> str:
    """A CSV as the admin page writes it, so the reader is checked against the writer."""
    return scores_csv_text(SeedScores(pars=PARS, rounds=list(rounds)))


ALICE = Player("Alice", (4,) * 9 + (5,) * 9)
BOB = Player("Bob", (5,) * 9 + (3,) * 9)


def test_reading_takes_pars_and_own_rounds_and_skips_teammates():
    scores = read_scores(
        csv_text(
            round_row("Alice", ALICE.strokes),
            round_row("Alice", (6,) * 18, kind_slot=1),
            round_row("Bob", BOB.strokes),
        )
    )
    assert scores.pars == PARS
    assert scores.players == (ALICE, BOB)
    assert scores.skipped == 1


@pytest.mark.parametrize(
    "text",
    ["", "a,b\n1,2\n", "round,player\npar,\n"],
)
def test_reading_refuses_what_is_not_a_scores_csv(text):
    with pytest.raises(ScoresError):
        read_scores(text)


def test_best_ball_is_the_better_score_on_each_hole():
    assert best_ball(ALICE, BOB) == (4,) * 9 + (3,) * 9


@pytest.mark.parametrize("strokes, expected", [(72, "E"), (68, "-4"), (75, "+3")])
def test_to_par(strokes, expected):
    assert to_par(strokes, 72) == expected


def test_matching_pairs_by_number_and_lists_who_is_left():
    matching = Matching((ALICE, BOB, Player("Carol", (4,) * 18)))
    assert matching.pair("1, 2") == "matched Alice and Bob"
    assert matching.pairs == [(1, 2)]
    assert matching.unmatched() == [3]
    listing = matching.listing()
    assert "1. Alice  +   2. Bob" in listing
    assert "3. Carol" in listing
    assert matching.undo() == "unmatched Alice and Bob"
    assert matching.unmatched() == [1, 2, 3]


@pytest.mark.parametrize(
    "text, message",
    [
        ("1", "two player numbers"),
        ("1, 2, 3", "two player numbers"),
        ("a, b", "two player numbers"),
        ("3, 9", "no player 9"),
        ("0, 1", "no player 0"),
        ("3, 3", "two different players"),
        ("1, 3", "Alice is already matched"),
    ],
)
def test_matching_refuses_a_bad_pair(text, message):
    matching = Matching((ALICE, BOB, Player("Carol", (4,) * 18)))
    matching.pair("1 2")
    with pytest.raises(ValueError, match=message):
        matching.pair(text)


def test_undo_with_nothing_matched_says_so():
    with pytest.raises(ValueError, match="nothing to undo"):
        Matching((ALICE, BOB)).undo()


def test_a_card_marks_the_counted_scores_and_totals_each_row():
    lines = card(PARS, ALICE, BOB, "Team 1: Alice + Bob").splitlines()
    assert lines[0] == "Team 1: Alice + Bob"
    par, alice, bob, best = lines[3], lines[5], lines[6], lines[8]
    assert par.endswith("|  36  36  72")
    assert alice.startswith("Alice         4*")
    assert alice.endswith("|  36  45  81")
    assert bob.endswith("|  45  27  72")
    assert " 3*" in bob
    assert best.endswith("|  36  27  63  -9")
    # every row's columns line up with the header's
    assert len({len(line) for line in (lines[2], par, alice, bob)}) == 1


def test_ten_stroke_holes_stay_apart_and_aligned():
    """10 is a hole's most strokes; a marked 4 before a 10 must not read as 410."""
    tens = Player("Tens", (4, 10) * 9)
    worse = Player("Worse", (10, 10) * 9)
    lines = card(PARS, tens, worse, "tens").splitlines()
    assert "  4* 10*" in lines[5]
    assert " 10  10 " in lines[6]
    assert len({len(line) for line in lines[2:4] + lines[5:7]}) == 1


def test_a_tie_marks_only_the_first_players_score():
    twin = Player("Twin", ALICE.strokes)
    lines = card(PARS, ALICE, twin, "tie").splitlines()
    assert lines[5].count("*") == 18
    assert lines[6].count("*") == 0


def test_the_leaderboard_ranks_pairs_and_ties_share_a_place():
    carol, dave = Player("Carol", (4,) * 18), Player("Dave", (5,) * 18)
    board = leaderboard(PARS, [(carol, dave), (ALICE, BOB), (dave, carol)])
    lines = board.splitlines()[2:]
    assert lines == [
        "  1.  63 ( -9)  Alice + Bob",
        "  2.  72 (  E)  Carol + Dave",
        "  2.  72 (  E)  Dave + Carol",
    ]


def test_the_report_lists_cards_then_the_board_then_who_was_left():
    carol = Player("Carol", (4,) * 18)
    scores = read_scores(
        csv_text(
            round_row("Alice", ALICE.strokes),
            round_row("Bob", BOB.strokes),
            round_row("Carol", carol.strokes),
        )
    )
    matching = Matching(scores.players)
    matching.pair("1, 2")
    text = report(scores, matching)
    assert text.index("Team 1: Alice + Bob") < text.index("Leaderboard")
    assert text.endswith("Not matched: Carol")
