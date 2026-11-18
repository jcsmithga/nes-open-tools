"""The admin pages' ratings reads: the health counts, the runs and how each moved the
effects, a seed's stored ratings, and a player's index round by round beside their
cards."""

from dataclasses import replace
from datetime import date, datetime

import pytest

from golf.qr.payload import HoleRecord
from golf.randomizer.catalog import Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Settings
from golf.ratings.rules import DailyRules
from server.admin_ratings import (
    MOVERS,
    player_indexes,
    ratings_health,
    ratings_runs_view,
    run_detail,
    seed_rating_detail,
    user_index,
)
from server.db import Database
from server.ratings import Ratings, player_index, refit
from server.rounds import void_round
from server.submissions import submit_scan
from tests.stored_seeds import checked_in_catalog, store_seed
from tests.unit.test_server_ratings import PARAMETERS, make_daily, play_all
from tests.unit.test_server_submissions import Player

RULES = DailyRules.from_parameters(PARAMETERS)
NOW = datetime.fromisoformat("2026-11-20T12:00:00+00:00")


@pytest.fixture(scope="module")
def manifests():
    catalog, curation = Catalog.load(), CurationSnapshot.load()
    return [
        generate(catalog, curation, Settings(prng_seed=f"admin{i}")) for i in range(6)
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
    """Six seeds, the first two played by three players and every one rated by a run
    for 2026-11-18."""
    stored = [store_seed(db, m) for m in manifests]
    play_all(db, stored[:2])
    assert refit(db, ratings, date(2026, 11, 18), "2026-11-18T09:00:00Z", min_rounds=5)
    return stored


def daily_round(db: Database, seed: str, day: str, name: str, strokes: int) -> str:
    """A day with rules on `seed`, made if it is not yet, and `name`'s round of all
    `strokes` on it; its id."""
    with db.transaction() as conn:
        made = conn.execute("SELECT 1 FROM dailies WHERE day = ?", (day,)).fetchone()
    if made is None:
        make_daily(db, day, seed, rules=RULES)
    player = Player(db, seed, name)
    recorded = submit_scan(
        db, player.scan(holes=(HoleRecord(strokes, 2),) * 18), now=f"{day}T12:00:00Z"
    )
    return recorded.round.public_id


def user_id(db: Database, name: str) -> int:
    with db.transaction() as conn:
        return conn.execute(
            "SELECT id FROM users WHERE discord_id = ?", (f"dev:{name}",)
        ).fetchone()["id"]


# -- Health ---------------------------------------------------------------------------------


def test_a_fresh_site_is_healthy_but_has_no_run(db):
    health = ratings_health(db, NOW, rollover=True, parameters=PARAMETERS)
    assert (
        health.unrated_seeds,
        health.dailies_without_rules,
        health.unfilled_cards,
    ) == (0, 0, 0)
    assert health.latest_run_day is None
    assert not health.run_current


def test_health_counts_what_the_rollover_left_behind(db, ratings, manifests):
    stored = [store_seed(db, m) for m in manifests[:3]]
    assert (
        ratings_health(db, NOW, rollover=True, parameters=PARAMETERS).unrated_seeds == 3
    )
    play_all(db, stored[:2])
    refit(db, ratings, date(2026, 11, 20), "2026-11-20T09:00:00Z", min_rounds=5)
    # a day stored without rules: its card is not filled either
    make_daily(db, "2026-11-20", stored[2])
    submit_scan(db, Player(db, stored[2], "ann").scan(), now="2026-11-20T12:00:00Z")
    health = ratings_health(db, NOW, rollover=True, parameters=PARAMETERS)
    assert health.unrated_seeds == 0
    assert health.dailies_without_rules == 1
    assert health.unfilled_cards == 1
    assert health.latest_run_day == health.today == "2026-11-20"
    assert health.run_current


def test_health_counts_own_rounds_since_the_estimate(db, manifests):
    seed = store_seed(db, manifests[0])
    for name, when in (
        ("ann", "2026-11-18T12:00:00Z"),
        ("bob", "2026-11-19T12:00:00Z"),
        ("cat", "2026-11-19T13:00:00Z"),
    ):
        submit_scan(db, Player(db, seed, name).scan(), now=when)
    estimated = replace(
        PARAMETERS,
        provenance={
            "rounds": 3,
            "latest_round": "2026-11-18T12:00:00Z",
            "estimated": "2026-11-18",
        },
    )
    health = ratings_health(db, NOW, rollover=True, parameters=estimated)
    assert (health.estimated_rounds, health.estimated_on, health.rounds_since) == (
        3,
        "2026-11-18",
        2,
    )
    assert not health.estimate_doubled
    doubled = replace(estimated, provenance=estimated.provenance | {"rounds": 2})
    assert ratings_health(db, NOW, rollover=True, parameters=doubled).estimate_doubled


def test_parameters_without_provenance_never_ask_for_an_estimate(db):
    bare = replace(PARAMETERS, provenance={})
    health = ratings_health(db, NOW, rollover=True, parameters=bare)
    assert health.rounds_since is None
    assert not health.estimate_doubled


def test_a_withdrawn_day_without_rules_needs_nothing(db, seeds):
    make_daily(db, "2026-11-19", seeds[2], withdrawn=True)
    assert (
        ratings_health(
            db, NOW, rollover=True, parameters=PARAMETERS
        ).dailies_without_rules
        == 0
    )


# -- Runs -----------------------------------------------------------------------------------


def test_a_daily_without_a_run_is_listed_as_missing(db, seeds, ratings):
    make_daily(db, "2026-11-19", seeds[2], rules=RULES)
    make_daily(db, "2026-11-20", seeds[3], rules=RULES)
    refit(db, ratings, date(2026, 11, 20), "2026-11-20T09:00:00Z", min_rounds=5)
    rows = ratings_runs_view(db, NOW)
    assert [(row.day, row.run is not None) for row in rows] == [
        ("2026-11-20", True),
        ("2026-11-19", False),
        ("2026-11-18", True),
    ]
    newest, _, first = rows
    assert newest.run is not None and first.run is not None
    assert first.run.seeds_rated == 6
    assert newest.run.seeds_rated == 0
    assert newest.run.n_rounds == first.run.n_rounds
    assert not newest.run.parameters_changed


def test_a_daily_before_the_first_run_is_not_missing(db, seeds):
    make_daily(db, "2026-11-17", seeds[2])
    assert [row.day for row in ratings_runs_view(db, NOW)] == ["2026-11-18"]


def test_a_change_of_parameters_is_marked(db, seeds, ratings):
    refit(db, ratings, date(2026, 11, 19), "2026-11-19T09:00:00Z", min_rounds=5)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE ratings_runs SET parameters_hash = ? WHERE day = '2026-11-19'",
            ("f" * 64,),
        )
    changed = {
        row.day: row.run.parameters_changed
        for row in ratings_runs_view(db, NOW)
        if row.run
    }
    assert changed == {"2026-11-19": True, "2026-11-18": False}


def test_a_run_compares_its_effects_with_the_run_before(db, seeds, ratings):
    second = refit(
        db, ratings, date(2026, 11, 19), "2026-11-19T09:00:00Z", min_rounds=5
    )
    assert second is not None
    with db.transaction() as conn:
        level = conn.execute(
            "SELECT level FROM ratings_effects WHERE run_id = ? AND grp = 'layout' "
            "ORDER BY level LIMIT 1",
            (second,),
        ).fetchone()["level"]
        conn.execute(
            "UPDATE ratings_effects SET value = value + 5 WHERE run_id = ? AND level = ?",
            (second, level),
        )
        conn.execute(
            "INSERT INTO ratings_effects (run_id, grp, level, value) "
            "VALUES (?, 'pin', 'new-pin', 0.25)",
            (second,),
        )
    detail = run_detail(db, second, Catalog.load())
    assert detail is not None and detail.previous is not None
    assert detail.previous.day == "2026-11-18"
    groups = {group.group: group for group in detail.groups}
    top = groups["layout"].movers[0]
    assert top.level == level
    assert top.delta == pytest.approx(5.0)
    assert len(groups["layout"].movers) <= MOVERS
    assert [(e.level, e.value) for e in groups["pin"].new] == [("new-pin", 0.25)]
    assert groups["pin"].new_count == 1
    assert groups["layout"].new == []


def test_the_first_run_has_nothing_to_compare(db, seeds):
    first = ratings_runs_view(db, NOW)[-1].run
    assert first is not None
    detail = run_detail(db, first.id, Catalog.load())
    assert detail is not None and detail.previous is None
    assert all(group.movers == [] for group in detail.groups)
    layout = next(group for group in detail.groups if group.group == "layout")
    assert layout.new_count == layout.levels > 0
    assert len(layout.new) == min(MOVERS, layout.levels)


def test_an_unknown_run_has_no_detail(db):
    assert run_detail(db, 99, Catalog.load()) is None


# -- A seed's ratings -----------------------------------------------------------------------


def test_a_seeds_ratings_name_their_run(db, seeds, ratings, manifests):
    detail = seed_rating_detail(db, seeds[0])
    assert detail is not None
    assert detail.run_day == "2026-11-18"
    assert len(detail.holes) == 18
    assert detail.course_rating == pytest.approx(sum(h.rating for h in detail.holes))
    assert sorted(h.stroke_index for h in detail.holes) == list(range(1, 19))


def test_an_unrated_seed_has_no_rating_detail(db, manifests):
    assert seed_rating_detail(db, store_seed(db, manifests[0])) is None


# -- A player's index -----------------------------------------------------------------------


def test_the_batched_indexes_agree_with_each_players(db, seeds):
    daily_round(db, seeds[2], "2026-11-18", "ann", 5)
    daily_round(db, seeds[3], "2026-11-19", "ann", 4)
    daily_round(db, seeds[3], "2026-11-19", "bo", 6)
    ids = [user_id(db, name) for name in ("ann", "bo", "cy")]
    batched = player_indexes(db, ids, PARAMETERS)
    for each in ids:
        assert batched[each] == player_index(db, each, PARAMETERS)
    assert [batched[each].rounds_counted for each in ids] == [2, 1, 0]


def test_the_index_rows_match_the_cards_while_nothing_changes(db, seeds):
    for i, (day, strokes) in enumerate(
        (("2026-11-18", 5), ("2026-11-19", 4), ("2026-11-20", 6))
    ):
        daily_round(db, seeds[2 + i], day, "ann", strokes)
    ann = user_id(db, "ann")
    shown = user_index(db, ann, PARAMETERS)
    assert shown.state == player_index(db, ann, PARAMETERS)
    assert [row.day for row in shown.rows] == ["2026-11-20", "2026-11-19", "2026-11-18"]
    assert [row.gross for row in shown.rows] == [108, 72, 90]
    assert not any(row.mismatch for row in shown.rows)
    # best 8 of 10 keeps 2 of 3: the 108 is dropped
    assert [row.counts for row in shown.rows] == [False, True, True]
    assert shown.rows[0].index_after == shown.state.index
    assert shown.rows[-1].index_before == PARAMETERS.index.start_index
    assert shown.rounds_to_low_mark == PARAMETERS.index.low_min_rounds - 3
    assert shown.parameters_hash == PARAMETERS.digest()
    assert shown.uncounted == []


def test_a_void_shows_where_cards_and_rules_now_disagree(db, seeds):
    first = daily_round(db, seeds[2], "2026-11-18", "ann", 8)
    daily_round(db, seeds[3], "2026-11-19", "ann", 4)
    daily_round(db, seeds[4], "2026-11-20", "ann", 4)
    ann = user_id(db, "ann")
    void_round(db, first, admin_id=ann)
    shown = user_index(db, ann, PARAMETERS)
    assert [row.day for row in shown.rows] == ["2026-11-20", "2026-11-19"]
    # the later cards were scored with the voided round's index
    assert all(row.mismatch for row in shown.rows)
    assert [(c.day, c.reason) for c in shown.uncounted] == [("2026-11-18", "voided")]


def test_a_withdrawn_days_card_is_listed_as_not_counting(db, seeds):
    daily_round(db, seeds[2], "2026-11-18", "ann", 5)
    with db.transaction() as conn:
        conn.execute("UPDATE dailies SET withdrawn_at = '2026-11-18T13:00:00Z'")
    shown = user_index(db, user_id(db, "ann"), PARAMETERS)
    assert shown.rows == []
    assert [c.reason for c in shown.uncounted] == ["withdrawn"]


def test_terrain_and_pin_levels_are_named_by_their_hole(db, seeds):
    first = ratings_runs_view(db, NOW)[-1].run
    assert first is not None
    detail = run_detail(db, first.id, Catalog.load())
    assert detail is not None
    with db.transaction() as conn:
        stored = {
            row["hole_id"] for row in conn.execute("SELECT hole_id FROM seed_holes")
        }
    groups = {group.group: group for group in detail.groups}
    terrain = [e.label for e in groups["terrain"].new]
    pins = [e.label for e in groups["pin"].new]
    assert terrain and all(label is not None for label in terrain)
    assert all(label.split(" ")[0] in stored for label in terrain if label)
    assert all(label is not None and ", pin " in label for label in pins)
    assert all(e.label is None for e in groups["layout"].new)
