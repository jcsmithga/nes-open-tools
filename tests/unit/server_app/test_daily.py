"""The daily seed on the site: making a day, its cards, its pages, its hidden scores and its
admin actions (`server/dailies.py`, `server/routes/daily_pages.py`)."""

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from golf.randomizer.daily import DayPlan, Theme, daily_date, magic_words, opens_at
from server import dailies
from server.app import create_app
from server.config import Config
from server.dailies import (
    CardRound,
    Daily,
    DailyClosedError,
    DailyPlayedError,
    DailyReplacedError,
    ensure_daily,
    load_daily,
    replace_daily,
    roll_over,
    utc_text,
    withdraw_daily,
)
from server.rounds import void_round
from server.seeds import SeedRow, load_seed
from server.submissions import submit_scan
from tests.app_state import app_state
from tests.unit.server_app.helpers import generate_seed, post_download, scan_path

SECRET = "test secret"


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    # Scans are stamped with the real time, so the day under test is the real one.
    return Clock(datetime.now(UTC))


@pytest.fixture
def today(clock) -> date:
    return daily_date(clock.now)


def daily_app(fake_builder, clock, secret: str | None = SECRET) -> TestClient:
    config = Config(
        database=":memory:",
        dev_login=True,
        admin_users=frozenset({"dev:admin"}),
        daily_secret=secret,
    )
    return TestClient(
        create_app(config, builder=fake_builder, clock=clock, rollover_seconds=None)
    )


@pytest.fixture
def site(fake_builder, clock):
    with daily_app(fake_builder, clock) as test_client:
        yield test_client


def make_daily(client: TestClient, day: date):
    state = app_state(client)
    return ensure_daily(state.db, state.builder, state.schedule, SECRET, day)


def sign_in(client: TestClient, name: str) -> None:
    client.get("/auth/login", params={"as": name})


def play(client: TestClient, seed_id: str, name: str) -> str:
    """`name` downloads the seed signed in and scans a round; returns the round page."""
    sign_in(client, name)
    assert post_download(client, seed_id).status_code == 200
    response = client.get(scan_path(client, seed_id, name), follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].split("?")[0]


def admin_id(client: TestClient) -> int:
    """The dev admin's users.id, signing them in."""
    sign_in(client, "admin")
    with app_state(client).db.transaction() as conn:
        return conn.execute(
            "SELECT id FROM users WHERE discord_id = 'dev:admin'"
        ).fetchone()["id"]


def stored(client: TestClient, day: date) -> Daily:
    found = load_daily(app_state(client).db, day)
    assert found is not None
    return found


def seed_row(client: TestClient, seed_id: str) -> SeedRow:
    found = load_seed(app_state(client).db, seed_id)
    assert found is not None
    return found


def cards(client: TestClient) -> list[dict]:
    with app_state(client).db.transaction() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM daily_cards")]


# -- Making a day ---------------------------------------------------------------------------


def test_a_day_is_made_once_with_its_window_theme_and_date_words(site, today):
    daily = make_daily(site, today)
    assert make_daily(site, today) == daily
    assert daily.theme == app_state(site).schedule.plan(today).theme
    assert daily.opens_at == f"{today.isoformat()}T09:00:00Z"
    assert daily.closes_at == f"{(today + timedelta(days=1)).isoformat()}T10:00:00Z"
    seed = seed_row(site, daily.seed_id)
    assert seed.manifest.course.magic_words == magic_words(today)
    with app_state(site).db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM dailies").fetchone()[0] == 1


def test_the_rollover_makes_the_day_open_at_the_clock(site, clock):
    state = app_state(site)
    daily = roll_over(state.db, state.builder, state.schedule, SECRET, clock.now)
    assert daily.day == daily_date(clock.now)


def test_the_rollover_refits_the_ratings_before_it_makes_the_day(
    site, clock, monkeypatch
):
    state = app_state(site)
    seen = []

    def refit(db, ratings, day, now):
        seen.append((ratings, day, load_daily(db, day)))

    monkeypatch.setattr(dailies, "refit", refit)
    daily = roll_over(
        state.db, state.builder, state.schedule, SECRET, clock.now, state.ratings
    )
    assert seen == [(state.ratings, daily.day, None)]


def test_a_failed_refit_still_makes_the_day(site, clock, monkeypatch, caplog):
    state = app_state(site)

    def refit(*_args):
        raise RuntimeError("the spreads did not settle")

    monkeypatch.setattr(dailies, "refit", refit)
    daily = roll_over(
        state.db, state.builder, state.schedule, SECRET, clock.now, state.ratings
    )
    assert load_daily(state.db, daily.day) is not None
    assert "the ratings refit" in caplog.text


def test_the_rollover_runs_in_the_app_when_it_has_a_secret(fake_builder, clock):
    config = Config(database=":memory:", daily_secret=SECRET)
    app = create_app(config, builder=fake_builder, clock=clock, rollover_seconds=3600)
    with TestClient(app) as test_client:
        deadline = datetime.now(UTC) + timedelta(seconds=30)
        while load_daily(app_state(test_client).db, daily_date(clock.now)) is None:
            assert datetime.now(UTC) < deadline
            test_client.get("/healthz")


def test_no_secret_no_rollover(fake_builder, clock):
    config = Config(database=":memory:")
    with TestClient(create_app(config, builder=fake_builder, clock=clock)) as client:
        client.get("/healthz")
        assert load_daily(app_state(client).db, daily_date(clock.now)) is None


# -- Pages ----------------------------------------------------------------------------------


def test_the_daily_redirects_to_the_daily_seed(site, today):
    daily = make_daily(site, today)
    response = site.get("/daily", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/h/{daily.seed_id}"
    assert response.headers["cache-control"] == "no-store"


def test_with_no_daily_the_daily_goes_to_the_calendar(site):
    response = site.get("/daily", follow_redirects=False)
    assert response.headers["location"] == "/daily/calendar"


def test_a_day_redirects_to_its_seed(site, today):
    daily = make_daily(site, today)
    response = site.get(f"/daily/{today.isoformat()}", follow_redirects=False)
    assert response.headers["location"] == f"/h/{daily.seed_id}"


@pytest.mark.parametrize("day", ["2001-01-01", "yesterday", "2026-13-40"])
def test_a_day_without_a_daily_is_not_found(site, day):
    assert site.get(f"/daily/{day}").status_code == 404


def test_a_day_not_yet_open_is_not_found(site, clock, today):
    make_daily(site, today)
    clock.now = datetime.fromisoformat(f"{today.isoformat()}T08:00:00+00:00")
    assert site.get(f"/daily/{today.isoformat()}").status_code == 404


def test_the_home_page_shows_the_daily_seed(site, today):
    assert 'class="daily-seed"' not in site.get("/").text
    daily = make_daily(site, today)
    page = site.get("/").text
    assert f'data-day="{today.isoformat()}"' in page
    assert f'href="/h/{daily.seed_id}"' in page


def test_the_daily_seed_box_names_its_theme_and_counts_down_to_its_close(site, today):
    daily = make_daily(site, today)
    page = site.get("/").text
    assert f"<h2>{daily.theme.name}</h2>" in page
    assert f'<time datetime="{daily.closes_at}" data-countdown>' in page
    assert 'data-players="0"' in page
    assert "daily-seed-card" not in page


def test_the_daily_seed_box_shows_a_player_their_own_round(site, today):
    daily = make_daily(site, today)
    round_path = play(site, daily.seed_id, "alice")
    page = site.get("/").text
    assert 'data-players="1"' in page
    card = re.search(r'<p class="daily-seed-card"[^>]*>', page)
    assert card is not None
    assert 'data-strokes="' in card.group(0) and 'data-to-par="' in card.group(0)
    assert f'href="{round_path}"' in page
    sign_in(site, "bob")
    assert "daily-seed-card" not in site.get("/").text


@pytest.mark.parametrize("strokes, text", [(74, "+2"), (72, "E"), (70, "-2")])
def test_a_card_reads_its_total_against_par(strokes, text):
    assert CardRound("abcdefghij", strokes, 72).to_par == text


def test_a_withdrawn_day_is_not_the_daily_seed(site, today):
    make_daily(site, today)
    withdraw_daily(app_state(site).db, today, admin_id(site))
    assert 'class="daily-seed"' not in site.get("/").text


def previous_box(page: str) -> str | None:
    found = re.search(
        r'<article class="daily-seed previous-daily"(.*?)</article>', page, re.S
    )
    return None if found is None else found.group(1)


def test_yesterdays_box_is_a_last_call_while_it_is_open(site, clock, today):
    yesterday = make_daily(site, today - timedelta(days=1))
    make_daily(site, today)
    clock.now = opens_at(today) + timedelta(minutes=30)
    box = previous_box(site.get("/").text)
    assert box is not None
    assert f'data-day="{yesterday.day.isoformat()}"' in box
    assert 'data-state="open"' in box
    assert f'<time datetime="{yesterday.closes_at}" data-countdown>' in box
    assert f'href="/h/{yesterday.seed_id}" role="button"' in box
    assert "daily-podium" not in box and "daily-seed-results" not in box


def test_yesterdays_box_links_its_results_once_it_closes(site, clock, today):
    yesterday = make_daily(site, today - timedelta(days=1))
    make_daily(site, today)
    clock.now = opens_at(today) + timedelta(hours=2)
    box = previous_box(site.get("/").text)
    assert box is not None
    assert 'data-state="closed"' in box
    assert 'data-players="0"' in box
    assert "data-countdown" not in box and 'role="button"' not in box
    # nobody played, so there is no standing to show
    assert "daily-podium" not in box
    assert f'href="/h/{yesterday.seed_id}"' in box


def test_yesterdays_box_shows_with_no_daily_seed_today(site, clock, today):
    make_daily(site, today - timedelta(days=1))
    withdraw_daily(app_state(site).db, make_daily(site, today).day, admin_id(site))
    clock.now = opens_at(today) + timedelta(hours=2)
    page = site.get("/").text
    assert 'class="daily-seed"' not in page
    assert previous_box(page) is not None


def test_a_withdrawn_yesterday_has_no_box(site, clock, today):
    make_daily(site, today - timedelta(days=1))
    make_daily(site, today)
    withdraw_daily(app_state(site).db, today - timedelta(days=1), admin_id(site))
    clock.now = opens_at(today) + timedelta(hours=2)
    assert previous_box(site.get("/").text) is None


def test_yesterday_still_the_daily_seed_has_no_second_box(site, clock, today):
    # today's is not made yet, so yesterday's, open for another half hour, is the Daily Seed
    yesterday = make_daily(site, today - timedelta(days=1))
    clock.now = opens_at(today) + timedelta(minutes=30)
    page = site.get("/").text
    assert f'data-day="{yesterday.day.isoformat()}"' in page
    assert previous_box(page) is None


def test_a_dailys_seed_page_says_it_is_one(site, today):
    daily = make_daily(site, today)
    page = site.get(f"/h/{daily.seed_id}").text
    assert 'class="seed-daily"' in page
    assert 'data-state="daily"' in page
    assert f'href="/daily/calendar?month={today.strftime("%Y-%m")}"' in page


def test_an_ordinary_seed_page_has_no_daily_mark(site, today):
    make_daily(site, today)
    ordinary = generate_seed(site)
    assert 'class="seed-daily"' not in site.get(f"/h/{ordinary}").text


def test_the_calendar_shows_each_day_with_its_players(site, today):
    daily = make_daily(site, today)
    play(site, daily.seed_id, "alice")
    page = site.get("/daily/calendar").text
    cell = re.search(rf'<td data-day="{today.isoformat()}"[^>]*>', page)
    assert cell is not None
    assert 'data-players="1"' in cell.group(0)
    assert f'href="/daily/{today.isoformat()}"' in page


@pytest.mark.parametrize("month", ["2026-13", "soon", "2999-01"])
def test_the_calendar_refuses_a_bad_or_future_month(site, month):
    assert site.get("/daily/calendar", params={"month": month}).status_code == 404


def test_the_calendar_links_back_to_the_first_daily(site, today):
    earlier = today - timedelta(days=40)
    make_daily(site, earlier)
    make_daily(site, today)
    page = site.get("/daily/calendar").text
    assert 'rel="prev"' in page
    assert 'rel="next"' not in page
    back = site.get("/daily/calendar", params={"month": earlier.strftime("%Y-%m")})
    assert f'data-day="{earlier.isoformat()}"' in back.text
    assert 'rel="next"' in back.text


# -- Cards ----------------------------------------------------------------------------------


def test_an_own_round_on_an_open_daily_gets_a_card(site, today):
    daily = make_daily(site, today)
    round_page = play(site, daily.seed_id, "alice")
    [card] = cards(site)
    assert card["day"] == today.isoformat()
    assert f"/r/{card['round_public_id']}" == round_page
    assert stored(site, today).players == 1


def test_a_teammates_round_gets_no_card(site, today):
    daily = make_daily(site, today)
    sign_in(site, "alice")
    post_download(site, daily.seed_id)
    site.get(scan_path(site, daily.seed_id, "alice", slot=1))
    assert cards(site) == []


def test_a_round_after_the_day_closes_gets_no_card(site, today):
    daily = make_daily(site, today)
    sign_in(site, "alice")
    post_download(site, daily.seed_id)
    path = scan_path(site, daily.seed_id, "alice")
    late = utc_text(datetime.now(UTC) + timedelta(days=2))
    submit_scan(app_state(site).db, path.removeprefix("/s/"), now=late)
    assert cards(site) == []


def test_a_round_on_a_withdrawn_day_gets_no_card(site, today):
    daily = make_daily(site, today)
    withdraw_daily(app_state(site).db, today, admin_id(site))
    play(site, daily.seed_id, "alice")
    assert cards(site) == []


def test_a_voided_rounds_card_stops_counting_and_passes_to_the_next_round(site, today):
    daily = make_daily(site, today)
    first = play(site, daily.seed_id, "alice").removeprefix("/r/")
    void_round(app_state(site).db, first, admin_id(site))
    assert stored(site, today).players == 0
    response = site.get(
        scan_path(site, daily.seed_id, "alice", strokes=5), follow_redirects=False
    )
    second = response.headers["location"].split("?")[0].removeprefix("/r/")
    [card] = cards(site)
    assert card["round_public_id"] == second != first
    assert stored(site, today).players == 1


# -- Hidden scores --------------------------------------------------------------------------


def test_an_open_dailys_scores_are_hidden_until_the_viewer_plays(site, today):
    daily = make_daily(site, today)
    round_page = play(site, daily.seed_id, "alice")

    sign_in(site, "bob")
    seed_page = site.get(f"/h/{daily.seed_id}").text
    assert 'data-state="scores-hidden"' in seed_page
    assert round_page not in seed_page
    assert 'class="nines"' not in site.get(round_page).text
    assert 'data-state="scores-hidden"' in site.get(round_page).text

    play(site, daily.seed_id, "bob")
    seed_page = site.get(f"/h/{daily.seed_id}").text
    assert 'data-state="scores-hidden"' not in seed_page
    assert round_page in seed_page
    assert 'class="nines"' in site.get(round_page).text


def test_signed_out_viewers_see_no_open_daily_scores(site, today):
    daily = make_daily(site, today)
    round_page = play(site, daily.seed_id, "alice")
    site.post("/auth/logout")
    assert 'data-state="scores-hidden"' in site.get(f"/h/{daily.seed_id}").text
    assert 'class="nines"' not in site.get(round_page).text


def test_the_scan_shows_the_round_to_a_phone_not_signed_in(site, today):
    daily = make_daily(site, today)
    sign_in(site, "alice")
    post_download(site, daily.seed_id)
    path = scan_path(site, daily.seed_id, "alice")
    site.post("/auth/logout")
    scanned = site.get(path)
    assert "recorded" in str(scanned.url)
    assert 'class="nines"' in scanned.text
    # the plain permalink stays hidden from a viewer who has not played
    assert 'class="nines"' not in site.get(scanned.url.path).text


def test_a_player_sees_their_own_round(site, today):
    daily = make_daily(site, today)
    round_page = play(site, daily.seed_id, "alice")
    assert 'class="nines"' in site.get(round_page).text


def test_scores_show_once_the_day_closes(site, clock, today):
    daily = make_daily(site, today)
    round_page = play(site, daily.seed_id, "alice")
    sign_in(site, "bob")
    clock.now = clock.now + timedelta(days=2)
    assert 'data-state="scores-hidden"' not in site.get(f"/h/{daily.seed_id}").text
    assert 'class="nines"' in site.get(round_page).text


# -- Admin ----------------------------------------------------------------------------------


def admin_post(client: TestClient, path: str, **data):
    sign_in(client, "admin")
    return client.post(path, data=data, follow_redirects=False)


def test_the_admin_dailies_page_lists_the_days(site, today):
    daily = make_daily(site, today)
    sign_in(site, "admin")
    page = site.get("/admin/dailies").text
    assert f'id="daily-{today.isoformat()}"' in page
    assert daily.seed_id in page
    sign_in(site, "alice")
    assert site.get("/admin/dailies").status_code == 404


def test_a_rerun_replaces_the_seed_and_withdraws_the_old_one(site, today):
    daily = make_daily(site, today)
    response = admin_post(site, f"/admin/dailies/{today.isoformat()}/replace", note="x")
    assert response.status_code == 303
    replaced = stored(site, today)
    assert replaced.rerun and replaced.replaced_seed_id == daily.seed_id
    db = app_state(site).db
    rerun_seed = seed_row(site, replaced.seed_id)
    old_seed = seed_row(site, daily.seed_id)
    assert rerun_seed.manifest.course.magic_words[2] == "RERUN"
    assert old_seed.withdrawn
    assert 'data-state="replaced"' in site.get(f"/h/{daily.seed_id}").text
    assert 'data-state="rerun"' in site.get(f"/h/{replaced.seed_id}").text
    assert (
        site.get(f"/daily/{today.isoformat()}", follow_redirects=False).headers[
            "location"
        ]
        == f"/h/{replaced.seed_id}"
    )
    with db.transaction() as conn:
        actions = [
            row["action"]
            for row in conn.execute("SELECT action FROM admin_actions ORDER BY id")
        ]
    assert actions == ["withdraw", "replace"]


def test_a_round_on_the_replaced_seed_gets_no_card(site, today):
    daily = make_daily(site, today)
    sign_in(site, "alice")
    post_download(site, daily.seed_id)
    path = scan_path(site, daily.seed_id, "alice")
    admin_post(site, f"/admin/dailies/{today.isoformat()}/replace")
    site.get(path)
    assert cards(site) == []


def test_a_day_is_replaced_once(site, today):
    make_daily(site, today)
    admin_post(site, f"/admin/dailies/{today.isoformat()}/replace")
    again = admin_post(site, f"/admin/dailies/{today.isoformat()}/replace")
    assert again.status_code == 409
    state = app_state(site)
    with pytest.raises(DailyReplacedError):
        replace_daily(state.db, state.builder, SECRET, today, admin_id=1)


def test_a_day_with_a_card_is_not_replaced(site, today):
    daily = make_daily(site, today)
    play(site, daily.seed_id, "alice")
    assert (
        admin_post(site, f"/admin/dailies/{today.isoformat()}/replace").status_code
        == 409
    )
    state = app_state(site)
    with pytest.raises(DailyPlayedError):
        replace_daily(state.db, state.builder, SECRET, today, admin_id=1)


def test_a_closed_day_is_not_replaced(site, today):
    make_daily(site, today)
    state = app_state(site)
    late = utc_text(datetime.now(UTC) + timedelta(days=2))
    with pytest.raises(DailyClosedError):
        replace_daily(state.db, state.builder, SECRET, today, admin_id=1, now=late)


def test_a_rerun_needs_the_secret(fake_builder, clock, today):
    with daily_app(fake_builder, clock, secret=None) as client:
        make_daily_without_secret = app_state(client)
        ensure_daily(
            make_daily_without_secret.db,
            make_daily_without_secret.builder,
            make_daily_without_secret.schedule,
            SECRET,
            today,
        )
        response = admin_post(client, f"/admin/dailies/{today.isoformat()}/replace")
        assert response.status_code == 409


def test_withdrawing_and_restoring_a_day(site, today):
    make_daily(site, today)
    day = today.isoformat()
    assert admin_post(site, f"/admin/dailies/{day}/withdraw").status_code == 303
    assert stored(site, today).withdrawn
    assert admin_post(site, f"/admin/dailies/{day}/withdraw").status_code == 409
    assert admin_post(site, f"/admin/dailies/{day}/restore").status_code == 303
    assert not stored(site, today).withdrawn
    assert admin_post(site, f"/admin/dailies/{day}/restore").status_code == 409


@pytest.mark.parametrize("action", ["replace", "withdraw", "restore"])
def test_admin_actions_on_a_missing_day_are_not_found(site, action):
    assert admin_post(site, f"/admin/dailies/2001-01-01/{action}").status_code == 404
    assert admin_post(site, f"/admin/dailies/someday/{action}").status_code == 404


def test_the_activity_log_links_a_daily_action(site, today):
    make_daily(site, today)
    admin_post(site, f"/admin/dailies/{today.isoformat()}/withdraw", note="broken")
    page = site.get("/admin/activity").text
    assert f'href="/admin/dailies#daily-{today.isoformat()}"' in page


def themed(client: TestClient, day: date, name: str, description: str | None) -> None:
    """Gives `day` its own theme in the app's schedule."""
    state = app_state(client)
    plan = state.schedule.plan(day)
    state.schedule = replace(
        state.schedule,
        overrides={day: DayPlan(Theme(name, description), plan.request)},
    )


def test_a_day_keeps_the_theme_it_was_made_with(site, today):
    themed(site, today, "First", "As made")
    make_daily(site, today)
    themed(site, today, "Second", None)
    assert stored(site, today).theme == Theme("First", "As made")
    admin_post(site, f"/admin/dailies/{today.isoformat()}/replace", note="x")
    rerun = stored(site, today)
    assert rerun.rerun and rerun.theme == Theme("First", "As made")


def test_a_themes_description_shows_where_the_day_is_named(site, today):
    themed(site, today, "Spring Major", "One line about the major")
    daily = make_daily(site, today)
    paragraph = '<p class="daily-theme-description">One line about the major</p>'
    for path in ("/", f"/h/{daily.seed_id}"):
        page = site.get(path).text
        assert "Spring Major" in page
        assert paragraph in page
    assert "Spring Major" in site.get("/daily/calendar").text


def test_a_theme_without_a_description_has_no_paragraph(site, today):
    daily = make_daily(site, today)
    assert daily.theme.description is None
    for path in ("/", f"/h/{daily.seed_id}"):
        assert "daily-theme-description" not in site.get(path).text
