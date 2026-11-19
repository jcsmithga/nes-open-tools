"""Round indexes: every player's own round stores the index they held at scan, a daily
round its step too, and a player's index is read from those stored rows, which a void, a
restore or a withdrawn day include or leave out but never rewrite (ADR 0034)."""

from datetime import date

import pytest

from golf.qr.payload import HoleRecord
from golf.randomizer.catalog import Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Settings
from golf.ratings.handicap import daily_handicap
from golf.ratings.params import IndexRules
from golf.ratings.rules import DailyRules
from server.dailies import withdraw_daily
from server.db import Database
from server.ratings import Ratings, refit, seed_rating
from server.round_indexes import (
    BackfillMismatchError,
    backfill,
    player_index,
    rules_hash,
    seed_play,
)
from server.rounds import restore_round, void_round
from server.submissions import submit_scan
from tests.stored_seeds import checked_in_catalog, store_seed
from tests.unit.test_server_ratings import DAY, NOW, PARAMETERS, make_daily, play_all
from tests.unit.test_server_submissions import Player

RULES = PARAMETERS.index
DAILY = DailyRules.from_parameters(PARAMETERS)


def strokes(n: int) -> tuple[HoleRecord, ...]:
    return (HoleRecord(n, 2),) * 18


@pytest.fixture(scope="module")
def manifests():
    catalog, curation = Catalog.load(), CurationSnapshot.load()
    return [
        generate(catalog, curation, Settings(prng_seed=f"indexes{i}")) for i in range(5)
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
    """Seeds 0 and 1 played and a run fitted; 2 to 4 rated, for dailies and free play."""
    played = [store_seed(db, m) for m in manifests[:2]]
    play_all(db, played)
    refit(db, ratings, DAY, NOW, min_rounds=5)
    return played + [store_seed(db, m, rater=ratings) for m in manifests[2:]]


def scan(db: Database, player: Player, n: int, at: str, slot: int = 0):
    return submit_scan(
        db, player.scan(slot=slot, holes=strokes(n)), now=at, index_rules=RULES
    ).round


def row(db: Database, public_id: str) -> dict | None:
    with db.transaction() as conn:
        found = conn.execute(
            "SELECT * FROM round_indexes WHERE round_public_id = ?", (public_id,)
        ).fetchone()
    return None if found is None else dict(found)


def two_dailies(db: Database, seeds: list[str], name: str = "ann"):
    """ann plays the 18th's daily in 6s, then the 19th's in 4s."""
    make_daily(db, "2026-11-18", seeds[2], rules=DAILY)
    make_daily(db, "2026-11-19", seeds[3], rules=DAILY)
    first = scan(db, Player(db, seeds[2], name), 6, "2026-11-18T12:00:00Z")
    second = scan(db, Player(db, seeds[3], name), 4, "2026-11-19T12:00:00Z")
    return first, second


def test_before_any_counted_round_a_player_holds_the_starting_index(db):
    state = player_index(db, 1, RULES)
    assert state.index == RULES.start_index
    assert state.rounds_counted == 0


def test_a_daily_round_stores_its_step_and_counts(db, seeds):
    make_daily(db, "2026-11-18", seeds[2], rules=DAILY)
    ann = Player(db, seeds[2], "ann")
    played = scan(db, ann, 6, "2026-11-18T12:00:00Z")
    stored = row(db, played.public_id)
    assert stored is not None
    assert stored["index_before"] == RULES.start_index
    assert stored["rules_hash"] == rules_hash(RULES)
    assert stored["adjusted_gross"] <= 6 * 18
    state = player_index(db, ann.user.id, RULES)
    assert state.rounds_counted == 1
    assert state.index == pytest.approx(stored["index_after"])
    with db.transaction() as conn:
        (kept,) = conn.execute("SELECT hash FROM index_rules").fetchall()
    assert kept["hash"] == rules_hash(RULES)


def test_a_round_on_any_seed_stores_the_index_held_and_does_not_count(db, seeds):
    first, _ = two_dailies(db, seeds)
    ann_free = Player(db, seeds[4], "ann")
    free = scan(db, ann_free, 5, "2026-11-20T12:00:00Z")
    stored = row(db, free.public_id)
    assert stored is not None
    assert stored["index_before"] == pytest.approx(
        player_index(db, ann_free.user.id, RULES).index
    )
    assert stored["differential"] is None and stored["rules_hash"] is None
    assert player_index(db, ann_free.user.id, RULES).rounds_counted == 2
    assert row(db, first.public_id)["differential"] is not None  # type: ignore[index]


def test_a_teammates_round_stores_no_index(db, seeds):
    ann = Player(db, seeds[4], "ann")
    teammate = scan(db, ann, 5, "2026-11-20T12:00:00Z", slot=1)
    assert row(db, teammate.public_id) is None


def test_the_next_daily_is_scanned_at_the_index_after_the_last(db, seeds):
    first, second = two_dailies(db, seeds)
    before, after = row(db, first.public_id), row(db, second.public_id)
    assert before is not None and after is not None
    assert after["index_before"] == pytest.approx(before["index_after"])


def test_a_void_leaves_later_rounds_as_stored_and_drops_out_of_the_index(db, seeds):
    first, second = two_dailies(db, seeds)
    kept = row(db, second.public_id)
    assert kept is not None
    user = first.user_id
    void_round(db, first.public_id, admin_id=user)
    assert row(db, second.public_id) == kept
    assert row(db, first.public_id) is not None
    state = player_index(db, user, RULES)
    assert state.rounds_counted == 1
    # read from the round that remains, which was scored from the 6s' index
    assert state.differentials == (pytest.approx(kept["differential"] - kept["cut"]),)
    restore_round(db, first.public_id, admin_id=user)
    assert player_index(db, user, RULES).index == pytest.approx(kept["index_after"])


def test_a_withdrawn_days_rounds_drop_out_of_the_index(db, seeds):
    first, _ = two_dailies(db, seeds)
    withdraw_daily(db, date(2026, 11, 18), admin_id=first.user_id)
    assert player_index(db, first.user_id, RULES).rounds_counted == 1


def test_a_daily_round_on_an_unrated_seed_stores_no_step(db, manifests):
    unrated = store_seed(db, manifests[2])
    make_daily(db, "2026-11-18", unrated)
    ann = Player(db, unrated, "ann")
    played = scan(db, ann, 5, "2026-11-18T12:00:00Z")
    stored = row(db, played.public_id)
    assert stored is not None and stored["differential"] is None
    assert player_index(db, ann.user.id, RULES).rounds_counted == 0


def test_the_index_is_read_under_the_rules_in_force(db, seeds):
    first, second = two_dailies(db, seeds)
    stored = row(db, second.public_id)
    assert stored is not None
    no_start = IndexRules(start_weight=0.0)
    # the stored differentials are kept; only how they combine follows the rules
    differentials = player_index(db, first.user_id, no_start).differentials
    assert differentials[-1] == pytest.approx(stored["differential"] - stored["cut"])


# -- A seed a player has played ------------------------------------------------------------


def test_seed_play_is_none_before_a_player_plays(db, seeds):
    ann = Player(db, seeds[4], "ann")
    assert seed_play(db, seeds[4], ann.user.id) is None


def test_seed_play_gives_a_daily_rounds_index_and_card_handicap(db, seeds):
    first, _ = two_dailies(db, seeds)
    play = seed_play(db, seeds[2], first.user_id)
    rating = seed_rating(db, seeds[2])
    assert play is not None and rating is not None
    assert play.index_before == RULES.start_index
    assert play.card_handicap == daily_handicap(
        RULES.start_index, rating, DAILY.allowance
    )


def test_seed_play_of_a_round_before_round_indexes_has_no_index(db, seeds):
    ann = Player(db, seeds[4], "ann")
    free = scan(db, ann, 5, "2026-11-20T12:00:00Z")
    with db.transaction() as conn:
        conn.execute(
            "DELETE FROM round_indexes WHERE round_public_id = ?", (free.public_id,)
        )
    play = seed_play(db, seeds[4], ann.user.id)
    assert play is not None and play.index_before is None


# -- The backfill --------------------------------------------------------------------------


def stored_rows(db: Database) -> list[dict]:
    with db.transaction() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM round_indexes WHERE differential IS NOT NULL "
                "ORDER BY round_public_id"
            )
        ]


def empty(db: Database) -> None:
    with db.transaction() as conn:
        conn.execute("DELETE FROM round_indexes")
        conn.execute("DELETE FROM index_rules")


def test_the_backfill_writes_the_rows_a_scan_would_have(db, seeds):
    two_dailies(db, seeds)
    scan(db, Player(db, seeds[2], "bob"), 5, "2026-11-18T13:00:00Z")
    scan(db, Player(db, seeds[3], "bob"), 7, "2026-11-19T13:00:00Z")
    scanned = stored_rows(db)
    empty(db)
    assert backfill(db, RULES) == 4
    filled = stored_rows(db)
    assert [r["round_public_id"] for r in filled] == [
        r["round_public_id"] for r in scanned
    ]
    for got, want in zip(filled, scanned, strict=True):
        for key in ("index_before", "differential", "cut", "index_after"):
            assert got[key] == pytest.approx(want[key])
        assert got["rules_hash"] == want["rules_hash"]


def test_the_backfill_runs_only_while_the_table_is_empty(db, seeds):
    two_dailies(db, seeds)
    assert backfill(db, RULES) == 0


def test_the_backfill_refuses_a_card_the_replay_disagrees_with(db, seeds):
    first, _ = two_dailies(db, seeds)
    empty(db)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE daily_cards SET index_used = 3.0 WHERE round_public_id = ?",
            (first.public_id,),
        )
    with pytest.raises(BackfillMismatchError, match=first.public_id):
        backfill(db, RULES)
    assert stored_rows(db) == []
