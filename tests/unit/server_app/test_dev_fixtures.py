"""The placeholder data `golf-site-screenshot --populate` fills a site with: a rated seed with
rounds, and an open daily whose field of twelve has results."""

import re
from datetime import date

import pytest

from server import dev_fixtures
from server.ratings import seed_rating
from tests.app_state import app_state
from tests.unit.server_app.helpers import dev_client

DAY = date(2026, 11, 18)


def site(fake_builder, moment):
    return dev_client(fake_builder, clock=lambda: moment, rollover_seconds=None)


def standing(page: str) -> list[str]:
    found = re.search(r'<article class="daily-results">(.*?)</table>', page, re.S)
    assert found is not None
    return re.findall(r'data-user="(\d+)"', found.group(1))


@pytest.fixture
def open_site(fake_builder):
    with site(fake_builder, dev_fixtures.open_moment(DAY)) as test_client:
        populated = dev_fixtures.populate(app_state(test_client), DAY)
        yield test_client, populated


def test_the_viewer_sees_the_whole_field_and_their_matches(open_site):
    test_client, populated = open_site
    test_client.get("/auth/login", params={"as": dev_fixtures.VIEWER})
    page = test_client.get(f"/h/{populated.daily}").text
    assert len(standing(page)) == len(dev_fixtures.FIELD)
    assert len(re.findall(r'data-result="(\w+)"', page)) == len(dev_fixtures.FIELD) - 1


def test_the_seed_is_rated_and_has_its_rounds(open_site):
    test_client, populated = open_site
    db = app_state(test_client).db
    assert seed_rating(db, populated.seed) is not None
    with db.transaction() as conn:
        rounds = conn.execute(
            "SELECT count(*) FROM rounds JOIN entries ON entries.id = rounds.entry_id"
            " WHERE entries.seed_id = ?",
            (populated.seed,),
        ).fetchone()[0]
    assert rounds == dev_fixtures.SEED_ROUNDS


def test_the_viewers_round_has_a_net_card(open_site):
    test_client, populated = open_site
    test_client.get("/auth/login", params={"as": dev_fixtures.VIEWER})
    page = test_client.get(f"/r/{populated.round}").text
    assert "data-received" in page


def test_an_outsider_sees_no_results_while_the_day_is_open(open_site):
    test_client, populated = open_site
    test_client.get("/auth/login", params={"as": dev_fixtures.OUTSIDER})
    page = test_client.get(f"/h/{populated.daily}").text
    assert 'class="daily-results"' not in page


def test_once_closed_a_signed_out_viewer_sees_the_standing(fake_builder):
    with site(fake_builder, dev_fixtures.closed_moment(DAY)) as test_client:
        populated = dev_fixtures.populate(app_state(test_client), DAY)
        page = test_client.get(f"/h/{populated.daily}").text
    assert len(standing(page)) == len(dev_fixtures.FIELD)
    assert "data-result" not in page
