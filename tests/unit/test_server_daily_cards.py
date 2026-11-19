"""A daily's cards: filled at scan from the index the player holds, the seed's stored
ratings and the day's rules, and filled later for a day opened without rules."""

import json
from datetime import date

import pytest

from golf.qr.payload import HoleRecord
from golf.randomizer.catalog import Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Settings
from golf.ratings.formats import stableford
from golf.ratings.handicap import daily_handicap
from golf.ratings.rules import DailyRules
from server.dailies import fill_days, load_day_results, restore_daily, withdraw_daily
from server.db import Database
from server.ratings import Ratings, refit, seed_rating
from server.round_indexes import stored_index
from server.rounds import restore_round, void_round
from server.submissions import submit_scan
from tests.stored_seeds import checked_in_catalog, store_seed
from tests.unit.test_server_ratings import (
    DAY,
    NOW,
    PARAMETERS,
    make_daily,
    play_all,
)
from tests.unit.test_server_submissions import Player

RULES = DailyRules.from_parameters(PARAMETERS)
#: a round of 5s, 2 putts each
FIVES = (HoleRecord(5, 2),) * 18


@pytest.fixture(scope="module")
def manifests():
    catalog, curation = Catalog.load(), CurationSnapshot.load()
    return [
        generate(catalog, curation, Settings(prng_seed=f"cards{i}")) for i in range(4)
    ]


@pytest.fixture
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def ratings():
    return Ratings(checked_in_catalog(), PARAMETERS)


@pytest.fixture
def seeds(db, ratings, manifests):
    """Seeds 0 and 1 played and a run fitted; 2 and 3 rated, for dailies."""
    played = [store_seed(db, m) for m in manifests[:2]]
    play_all(db, played)
    refit(db, ratings, DAY, NOW, min_rounds=5)
    return played + [store_seed(db, m, rater=ratings) for m in manifests[2:]]


def card(db: Database, day: str, user_id: int) -> dict:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM daily_cards WHERE day = ? AND user_id = ?", (day, user_id)
        ).fetchone()
        holes = conn.execute(
            """
            SELECT received, net, points FROM daily_card_holes
            WHERE day = ? AND user_id = ? ORDER BY position
            """,
            (day, user_id),
        ).fetchall()
    return dict(row) | {"holes": [tuple(h) for h in holes]}


def test_a_scan_on_a_day_with_rules_fills_its_card(db, seeds):
    make_daily(db, "2026-11-18", seeds[2], rules=RULES)
    dee = Player(db, seeds[2], "dee")
    submit_scan(db, dee.scan(holes=FIVES), now="2026-11-18T12:00:00Z")
    filled = card(db, "2026-11-18", dee.user.id)
    rating = seed_rating(db, seeds[2])
    assert rating is not None
    # dee has no earlier counted round: the starting index
    assert filled["index_used"] == PARAMETERS.index.start_index
    assert filled["handicap"] == daily_handicap(0.0, rating, RULES.allowance)
    received = [h[0] for h in filled["holes"]]
    assert sum(received) == filled["handicap"]
    assert [h[1] for h in filled["holes"]] == [5 - r for r in received]
    pars = [h.par for h in rating.holes]
    assert [h[2] for h in filled["holes"]] == stableford([5] * 18, pars, received)


def test_the_next_daily_is_scored_with_the_index_its_round_was_scanned_at(db, seeds):
    make_daily(db, "2026-11-18", seeds[2], rules=RULES)
    make_daily(db, "2026-11-19", seeds[3], rules=RULES)
    dee = Player(db, seeds[2], "dee")
    submit_scan(db, dee.scan(holes=FIVES), now="2026-11-18T12:00:00Z")
    dee_next = Player(db, seeds[3], "dee")
    second_round = submit_scan(
        db, dee_next.scan(holes=FIVES), now="2026-11-19T12:00:00Z"
    ).round
    first = card(db, "2026-11-18", dee.user.id)
    second = card(db, "2026-11-19", dee.user.id)
    with db.transaction() as conn:
        scanned_at = stored_index(conn, second_round.public_id)
    assert second["index_used"] == scanned_at
    assert second["index_used"] != first["index_used"]


def test_a_day_without_rules_keeps_its_cards_unfilled(db, seeds):
    make_daily(db, "2026-11-18", seeds[2])
    dee = Player(db, seeds[2], "dee")
    submit_scan(db, dee.scan(holes=FIVES), now="2026-11-18T12:00:00Z")
    unfilled = card(db, "2026-11-18", dee.user.id)
    assert unfilled["handicap"] is None and unfilled["holes"] == []


def test_filling_a_day_gives_it_rules_and_fills_its_cards_once(db, seeds):
    make_daily(db, "2026-11-18", seeds[2])
    dee = Player(db, seeds[2], "dee")
    submit_scan(db, dee.scan(holes=FIVES), now="2026-11-18T12:00:00Z")
    assert fill_days(db, RULES) == ["2026-11-18"]
    with db.transaction() as conn:
        stored = conn.execute("SELECT rules FROM dailies").fetchone()["rules"]
    assert DailyRules.from_json(json.loads(stored)) == RULES
    filled = card(db, "2026-11-18", dee.user.id)
    assert filled["handicap"] is not None and len(filled["holes"]) == 18
    assert fill_days(db, RULES) == []


def test_a_day_whose_seed_is_unrated_waits_for_its_rating(db, manifests):
    unrated = store_seed(db, manifests[2])
    make_daily(db, "2026-11-18", unrated)
    assert fill_days(db, RULES) == []


def test_a_new_round_taking_over_a_voided_card_refills_it(db, seeds):
    make_daily(db, "2026-11-18", seeds[2], rules=RULES)
    dee = Player(db, seeds[2], "dee")
    first = submit_scan(db, dee.scan(holes=FIVES), now="2026-11-18T12:00:00Z")
    void_round(db, first.round.public_id, admin_id=dee.user.id)
    fours = (HoleRecord(4, 2),) * 18
    second = submit_scan(db, dee.scan(holes=fours), now="2026-11-18T13:00:00Z")
    refilled = card(db, "2026-11-18", dee.user.id)
    assert refilled["round_public_id"] == second.round.public_id
    received = [h[0] for h in refilled["holes"]]
    assert [h[1] for h in refilled["holes"]] == [4 - r for r in received]


# -- Results --------------------------------------------------------------------------------


def results(db: Database, day: str = "2026-11-18") -> dict:
    with db.transaction() as conn:
        players = {
            r["user_id"]: (r["points"], r["position"], r["skins"], bool(r["won"]))
            for r in conn.execute("SELECT * FROM daily_results WHERE day = ?", (day,))
        }
        matches = [
            dict(m)
            for m in conn.execute("SELECT * FROM daily_matches WHERE day = ?", (day,))
        ]
    return {"players": players, "matches": matches}


def two_rounds(db: Database, seed: str):
    """dee scores 4s and eve 5s on the day's seed."""
    dee, eve = Player(db, seed, "dee"), Player(db, seed, "eve")
    played = [
        submit_scan(db, p.scan(holes=(HoleRecord(s, 2),) * 18), now=at)
        for p, s, at in (
            (dee, 4, "2026-11-18T12:00:00Z"),
            (eve, 5, "2026-11-18T13:00:00Z"),
        )
    ]
    return dee, eve, played


def test_a_field_of_one_is_placed_but_wins_nothing(db, seeds):
    make_daily(db, "2026-11-18", seeds[2], rules=RULES)
    dee = Player(db, seeds[2], "dee")
    submit_scan(db, dee.scan(holes=FIVES), now="2026-11-18T12:00:00Z")
    scored = results(db)
    points, position, skins, won = scored["players"][dee.user.id]
    assert (position, skins, won) == (1, 0, False)
    assert points == sum(h[2] for h in card(db, "2026-11-18", dee.user.id)["holes"])
    assert scored["matches"] == []


def test_two_players_make_a_standing_a_match_and_skins(db, seeds):
    make_daily(db, "2026-11-18", seeds[2], rules=RULES)
    dee, eve, _ = two_rounds(db, seeds[2])
    scored = results(db)
    # both play at the starting index, so the same strokes: dee's 4s beat eve's 5s
    assert scored["players"][dee.user.id][1:] == (1, 18, True)
    assert scored["players"][eve.user.id][1:] == (2, 0, False)
    (match,) = scored["matches"]
    assert (match["winner_id"], match["margin"]) == (dee.user.id, 18)


def test_a_void_takes_a_round_out_of_the_results_and_a_restore_puts_it_back(db, seeds):
    make_daily(db, "2026-11-18", seeds[2], rules=RULES)
    dee, eve, played = two_rounds(db, seeds[2])
    before = results(db)
    void_round(db, played[0].round.public_id, admin_id=eve.user.id)
    after_void = results(db)
    assert set(after_void["players"]) == {eve.user.id}
    assert after_void["players"][eve.user.id][1:] == (1, 0, False)
    assert after_void["matches"] == []
    restore_round(db, played[0].round.public_id, admin_id=eve.user.id)
    assert results(db) == before


def test_a_withdrawn_day_has_no_results_until_restored(db, seeds):
    make_daily(db, "2026-11-18", seeds[2], rules=RULES)
    _, eve, _ = two_rounds(db, seeds[2])
    before = results(db)
    withdraw_daily(db, date(2026, 11, 18), admin_id=eve.user.id)
    assert results(db) == {"players": {}, "matches": []}
    restore_daily(db, date(2026, 11, 18), admin_id=eve.user.id)
    assert results(db) == before


def test_filling_a_day_scores_it(db, seeds):
    make_daily(db, "2026-11-18", seeds[2])
    dee, _, _ = two_rounds(db, seeds[2])
    assert results(db)["players"] == {}
    fill_days(db, RULES)
    assert results(db)["players"][dee.user.id][3] is True


def test_a_players_matches_list_their_best_result_first(db, seeds):
    make_daily(db, "2026-11-18", seeds[2], rules=RULES)
    field = {}
    # everyone plays at the starting index, so the same strokes: dee's 4s beat amy's
    # 5s, halve bob's 4s and lose to cal's 3s, while the standing runs cal, dee, bob, amy
    for hour, (name, strokes) in enumerate(
        (("amy", 5), ("bob", 4), ("cal", 3), ("dee", 4)), start=10
    ):
        player = Player(db, seeds[2], name)
        submit_scan(
            db,
            player.scan(holes=(HoleRecord(strokes, 2),) * 18),
            now=f"2026-11-18T{hour}:00:00Z",
        )
        field[name] = player.user.id
    day = load_day_results(db, "2026-11-18")
    assert day is not None
    mine = day.matches[field["dee"]]
    assert [(m.opponent_id, m.result) for m in mine] == [
        (field["amy"], "win"),
        (field["bob"], "half"),
        (field["cal"], "loss"),
    ]
    assert [line.user_id for line in day.standing][0] == field["cal"]
