"""Ratings on the pages: a rated seed's course rating, slope and stroke indexes, a
signed-in player's handicap and strokes on it, and their index on /me."""

import json
import re
from datetime import UTC, date, datetime

import pytest

from golf.ratings.rules import DailyRules
from server.dailies import fill_days, roll_over
from server.ratings import refit
from tests.app_state import app_state
from tests.unit.server_app.helpers import (
    dev_client,
    entered_seed,
    generate_seed,
    scan_path,
)


@pytest.fixture
def site(fake_builder):
    with dev_client(fake_builder) as test_client:
        yield test_client


def first_run(client) -> str:
    """ann's and bo's rounds on a seed, and a run fitted from them, which rates it."""
    seed_id = entered_seed(client, "ann", "bo")
    for name, strokes in (("ann", 4), ("bo", 5)):
        client.get(scan_path(client, seed_id, name, strokes=strokes))
    state = app_state(client)
    assert refit(state.db, state.ratings, date(2026, 11, 18), min_rounds=1)
    return seed_id


def attribute(page: str, name: str) -> str | None:
    found = re.search(rf'{name}="([^"]*)"', page)
    return None if found is None else found.group(1)


def seed_page(client, seed_id: str) -> str:
    response = client.get(f"/h/{seed_id}")
    assert response.status_code == 200
    return response.text


def test_an_unrated_seed_shows_no_ratings(site):
    page = seed_page(site, generate_seed(site))
    assert "data-course-rating" not in page
    assert "data-stroke-index" not in page


def test_every_seed_shows_its_par_and_distance(site):
    seed_id = generate_seed(site)
    page = seed_page(site, seed_id)
    holes = app_state(site).db
    with holes.transaction() as conn:
        par, distance = conn.execute(
            "SELECT sum(par), sum(distance) FROM seed_holes WHERE seed_id = ?",
            (seed_id,),
        ).fetchone()
    assert attribute(page, "data-par") == str(par)
    assert attribute(page, "data-distance") == str(distance)


def test_a_rated_seed_shows_its_course_rating_slope_and_stroke_indexes(site):
    first_run(site)
    site.post("/auth/logout")
    page = seed_page(site, generate_seed(site))
    course_rating = attribute(page, "data-course-rating")
    slope = attribute(page, "data-slope")
    assert course_rating is not None and re.fullmatch(r"\d+\.\d", course_rating)
    assert slope is not None
    # golf's scale, where 113 is a seed as hard to separate players on as the reference
    assert 50 < int(slope) < 200
    indexes = re.findall(r'data-stroke-index="(\d+)"', page)
    assert sorted(map(int, indexes)) == list(range(1, 19))
    # signed out, no handicap
    assert "data-handicap" not in page
    assert "data-received" not in page


def test_a_signed_in_player_sees_their_handicap_and_strokes(site):
    first_run(site)
    site.get("/auth/login", params={"as": "cy"})
    page = seed_page(site, generate_seed(site))
    handicap = attribute(page, "data-handicap")
    assert handicap is not None
    # cy has no counted rounds: the starting index
    assert attribute(page, "data-index") == "0.0"
    received = [int(r) for r in re.findall(r'data-received="(-?\d+)"', page)]
    assert len(received) == 18
    total = int(handicap.replace("+", "-"))
    assert sum(received) == total


def set_stored_index(client, seed_id: str, name: str, index: float | None) -> None:
    """Rewrite the index ann's or bo's round stored, or delete it, as a round from
    before round indexes has none."""
    with app_state(client).db.transaction() as conn:
        public_id = conn.execute(
            """
            SELECT r.public_id FROM rounds r JOIN entries e ON e.id = r.entry_id
            JOIN users u ON u.id = e.user_id
            WHERE e.seed_id = ? AND u.username = ? AND r.slot = 0
            """,
            (seed_id, name),
        ).fetchone()["public_id"]
        if index is None:
            conn.execute(
                "DELETE FROM round_indexes WHERE round_public_id = ?", (public_id,)
            )
        else:
            conn.execute(
                "UPDATE round_indexes SET index_before = ? WHERE round_public_id = ?",
                (index, public_id),
            )


def test_a_player_who_played_sees_the_index_their_round_stored(site):
    seed_id = first_run(site)
    # not the index ann holds now, the starting index
    set_stored_index(site, seed_id, "ann", 5.0)
    site.get("/auth/login", params={"as": "ann"})
    page = seed_page(site, seed_id)
    assert attribute(page, "data-played") == "true"
    assert attribute(page, "data-index") == "5.0"
    handicap = attribute(page, "data-handicap")
    assert handicap is not None
    received = [int(r) for r in re.findall(r'data-received="(-?\d+)"', page)]
    assert sum(received) == int(handicap.replace("+", "-"))


def test_a_player_who_has_not_played_sees_their_index_now(site):
    seed_id = first_run(site)
    site.get("/auth/login", params={"as": "cy"})
    page = seed_page(site, seed_id)
    assert attribute(page, "data-played") == "false"
    assert attribute(page, "data-index") == "0.0"


def test_a_round_from_before_round_indexes_shows_no_handicap(site):
    seed_id = first_run(site)
    set_stored_index(site, seed_id, "ann", None)
    site.get("/auth/login", params={"as": "ann"})
    page = seed_page(site, seed_id)
    assert attribute(page, "data-course-rating") is not None
    assert "data-handicap" not in page
    assert "data-received" not in page


def test_me_shows_the_starting_index_before_any_counted_round(site):
    site.get("/auth/login", params={"as": "ann"})
    page = site.get("/me").text
    assert attribute(page, "data-index") == "0.0"
    assert attribute(page, "data-rounds") == "0"


def test_the_rollover_stores_the_days_rules_on_a_rated_daily(site):
    first_run(site)
    state = app_state(site)
    now = datetime.now(UTC)
    daily = roll_over(
        state.db, state.builder, state.schedule, "secret", now, state.ratings
    )
    with state.db.transaction() as conn:
        stored = conn.execute(
            "SELECT rules FROM dailies WHERE day = ?", (daily.day.isoformat(),)
        ).fetchone()["rules"]
    assert DailyRules.from_json(json.loads(stored)) == DailyRules.from_parameters(
        state.ratings.parameters
    )


def test_a_daily_made_with_no_run_takes_its_rules_once_rated(site):
    state = app_state(site)
    now = datetime.now(UTC)
    daily = roll_over(
        state.db, state.builder, state.schedule, "secret", now, state.ratings
    )
    with state.db.transaction() as conn:
        assert conn.execute("SELECT rules FROM dailies").fetchone()["rules"] is None
    first_run(site)  # the refit rates the daily's seed
    assert fill_days(
        state.db, DailyRules.from_parameters(state.ratings.parameters)
    ) == [daily.day.isoformat()]
