"""Ratings on the site: the refit writes a run once a day and rates every seed, a seed
stored after it is rated in its own transaction, and a player's index counts their daily
rounds."""

import json
from datetime import date

import numpy as np
import pytest

from golf.qr.payload import HoleRecord
from golf.randomizer.catalog import Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Settings
from golf.ratings.params import Parameters
from golf.ratings.rules import DailyRules
from server.db import Database
from server.ratings import (
    Ratings,
    player_index,
    refit,
    seed_rating,
)
from server.rounds import void_round
from server.submissions import submit_scan
from tests.stored_seeds import checked_in_catalog, store_seed
from tests.unit.test_server_submissions import Player

DAY = date(2026, 11, 18)
NOW = "2026-11-18T09:00:00Z"
PARAMETERS = Parameters.load()


@pytest.fixture(scope="module")
def manifests():
    catalog, curation = Catalog.load(), CurationSnapshot.load()
    return [
        generate(catalog, curation, Settings(prng_seed=f"ratings{i}")) for i in range(3)
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


def holes(rng: np.random.Generator, skill: float) -> tuple[HoleRecord, ...]:
    return tuple(
        HoleRecord(int(max(2, round(4 + skill + rng.normal(0, 1)))), 2)
        for _ in range(18)
    )


def play_all(db: Database, seeds: list[str], at: str = "2026-11-17T12:00:00Z") -> None:
    """Three players' own rounds on every seed."""
    rng = np.random.default_rng(0)
    for seed in seeds:
        for name, skill in (("ann", -0.3), ("bo", 0.2), ("cy", 0.8)):
            player = Player(db, seed, name)
            submit_scan(db, player.scan(holes=holes(rng, skill)), now=at)


def count(db: Database, table: str) -> int:
    with db.transaction() as conn:
        return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_a_refit_writes_the_days_run_and_rates_every_seed(db, ratings, manifests):
    seeds = [store_seed(db, m) for m in manifests]
    play_all(db, seeds)
    run_id = refit(db, ratings, DAY, NOW, min_rounds=5)
    assert run_id is not None
    with db.transaction() as conn:
        run = conn.execute("SELECT * FROM ratings_runs").fetchone()
    assert (run["day"], run["n_rounds"]) == ("2026-11-18", 9)
    assert run["parameters_hash"] == PARAMETERS.digest()
    assert Parameters.from_json(json.loads(run["parameters"])) == PARAMETERS
    assert count(db, "ratings_effects") > 0
    assert count(db, "seed_ratings") == 3
    assert count(db, "seed_hole_ratings") == 54
    assert ratings.latest is not None and ratings.latest.id == run_id


def test_a_day_is_refitted_once(db, ratings, manifests):
    seeds = [store_seed(db, m) for m in manifests]
    play_all(db, seeds)
    assert refit(db, ratings, DAY, NOW, min_rounds=5) is not None
    assert refit(db, ratings, DAY, NOW, min_rounds=5) is None
    assert count(db, "ratings_runs") == 1


def test_too_few_rounds_write_no_run(db, ratings, manifests):
    seeds = [store_seed(db, m) for m in manifests]
    play_all(db, seeds)
    assert refit(db, ratings, DAY, NOW) is None
    assert count(db, "ratings_runs") == 0
    assert count(db, "seed_ratings") == 0
    assert ratings.latest is None


def test_a_seed_stored_with_no_run_is_unrated_until_the_next_refit(
    db, ratings, manifests
):
    first, second = (store_seed(db, m, rater=ratings) for m in manifests[:2])
    assert seed_rating(db, first) is None
    play_all(db, [first, second])
    refit(db, ratings, DAY, NOW, min_rounds=5)
    assert seed_rating(db, first) is not None


def test_a_seed_stored_after_a_run_is_rated_as_it_is_stored(db, ratings, manifests):
    seeds = [store_seed(db, m) for m in manifests[:2]]
    play_all(db, seeds)
    refit(db, ratings, DAY, NOW, min_rounds=5)
    new = store_seed(db, manifests[2], rater=ratings)
    rated = seed_rating(db, new)
    assert rated is not None
    assert sorted(h.stroke_index for h in rated.holes) == list(range(1, 19))
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM seed_ratings WHERE seed_id = ?", (new,)
        ).fetchone()
    assert row["course_rating"] == pytest.approx(rated.course_rating)
    assert row["slope"] == pytest.approx(rated.slope)
    assert row["par"] == rated.par


def test_a_stored_run_rates_as_the_run_it_was_fitted_as(db, ratings, manifests):
    """After a restart, the run read back rates a new seed as the fitted one did."""
    seeds = [store_seed(db, m) for m in manifests[:2]]
    play_all(db, seeds)
    refit(db, ratings, DAY, NOW, min_rounds=5)
    reloaded = Ratings(checked_in_catalog(), PARAMETERS)
    reloaded.load_latest(db)
    assert reloaded.latest is not None and ratings.latest is not None
    assert reloaded.latest.id == ratings.latest.id
    new = store_seed(db, manifests[2])
    with db.transaction() as conn:
        assert reloaded.rate_on(conn, new, NOW)
    fresh = seed_rating(db, new)
    with db.transaction() as conn:
        conn.execute("DELETE FROM seed_hole_ratings WHERE seed_id = ?", (new,))
        conn.execute("DELETE FROM seed_ratings WHERE seed_id = ?", (new,))
        assert ratings.rate_on(conn, new, NOW)
    assert seed_rating(db, new) == fresh


def test_a_seed_is_rated_once(db, ratings, manifests):
    seeds = [store_seed(db, m) for m in manifests]
    play_all(db, seeds)
    refit(db, ratings, DAY, NOW, min_rounds=5)
    with db.transaction() as conn:
        assert not ratings.rate_on(conn, seeds[0], NOW)


# -- A player's index ----------------------------------------------------------------------


def make_daily(
    db: Database,
    day: str,
    seed: str,
    withdrawn: bool = False,
    rules: DailyRules | None = None,
) -> None:
    with db.transaction() as conn:
        conn.execute(
            """
            INSERT INTO dailies (day, theme_name, request, opens_at, closes_at,
                                 created_at, withdrawn_at, rules)
            VALUES (?, 'Theme', '{}', ?, ?, ?, ?, ?)
            """,
            (
                day,
                f"{day}T09:00:00Z",
                f"{day}T23:59:59Z",
                f"{day}T09:00:00Z",
                f"{day}T10:00:00Z" if withdrawn else None,
                None if rules is None else json.dumps(rules.to_json()),
            ),
        )
        conn.execute(
            "INSERT INTO daily_seeds (day, attempt, seed_id) VALUES (?, 1, ?)",
            (day, seed),
        )


@pytest.fixture
def rated_daily(db, ratings, manifests):
    """Seeds 0 and 1 played and rated; seed 2 the day's daily, rated too."""
    seeds = [store_seed(db, m) for m in manifests]
    play_all(db, seeds[:2])
    refit(db, ratings, DAY, NOW, min_rounds=5)
    make_daily(db, "2026-11-18", seeds[2])
    return seeds[2]


def test_before_any_counted_round_a_player_holds_the_starting_index(db):
    state = player_index(db, 1, PARAMETERS)
    assert state.index == PARAMETERS.index.start_index
    assert state.rounds_counted == 0


def test_a_daily_round_counts_and_an_ordinary_one_does_not(db, rated_daily):
    ann = Player(db, rated_daily, "ann")
    submit_scan(
        db, ann.scan(holes=(HoleRecord(6, 2),) * 18), now="2026-11-18T12:00:00Z"
    )
    state = player_index(db, ann.user.id, PARAMETERS)
    assert state.rounds_counted == 1
    # 108 on a seed rated near par moves a scratch start up
    assert state.index > PARAMETERS.index.start_index
    # her rounds on the other seeds, from play_all, are not daily rounds
    assert len(state.differentials) == 1


def test_a_voided_daily_round_stops_counting(db, rated_daily):
    ann = Player(db, rated_daily, "ann")
    recorded = submit_scan(db, ann.scan(), now="2026-11-18T12:00:00Z")
    void_round(db, recorded.round.public_id, admin_id=ann.user.id)
    assert player_index(db, ann.user.id, PARAMETERS).rounds_counted == 0


def test_a_withdrawn_days_rounds_do_not_count(db, ratings, manifests):
    seeds = [store_seed(db, m) for m in manifests]
    play_all(db, seeds[:2])
    refit(db, ratings, DAY, NOW, min_rounds=5)
    make_daily(db, "2026-11-18", seeds[2])
    ann = Player(db, seeds[2], "ann")
    submit_scan(db, ann.scan(), now="2026-11-18T12:00:00Z")
    with db.transaction() as conn:
        conn.execute("UPDATE dailies SET withdrawn_at = ?", ("2026-11-18T13:00:00Z",))
    assert player_index(db, ann.user.id, PARAMETERS).rounds_counted == 0
