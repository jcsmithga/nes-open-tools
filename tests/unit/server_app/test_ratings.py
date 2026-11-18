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


def first_run(client) -> None:
    """Two players' rounds on a seed, and a run fitted from them."""
    seed_id = entered_seed(client, "ann", "bo")
    for name, strokes in (("ann", 4), ("bo", 5)):
        client.get(scan_path(client, seed_id, name, strokes=strokes))
    state = app_state(client)
    assert refit(state.db, state.ratings, date(2026, 11, 18), min_rounds=1)


def attribute(page: str, name: str) -> str | None:
    found = re.search(rf'{name}="([^"]*)"', page)
    return None if found is None else found.group(1)


def seed_page(client, seed_id: str) -> str:
    response = client.get(f"/h/{seed_id}")
    assert response.status_code == 200
    return response.text


def test_an_unrated_seed_shows_no_ratings(site):
    page = seed_page(site, generate_seed(site))
    assert 'class="seed-rating"' not in page
    assert "data-stroke-index" not in page


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
