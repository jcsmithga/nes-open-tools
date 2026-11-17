"""The daily seed's library half: its window, date, PRNG seed, magic words and schedule."""

import json
from datetime import UTC, date, datetime

import pytest

from golf.randomizer.catalog import Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.daily import (
    FIRST,
    RERUN,
    WEEKDAYS,
    DayPlan,
    Schedule,
    ScheduleError,
    Theme,
    closes_at,
    daily_date,
    generate_daily,
    is_open,
    magic_words,
    opens_at,
    ordinal,
    prng_seed,
)
from golf.randomizer.request import resolve
from golf.randomizer.words import check_magic_words

MONDAY = date(2026, 11, 16)


def at(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_a_daily_opens_at_nine_utc_for_25_hours():
    assert opens_at(MONDAY) == at("2026-11-16T09:00")
    assert closes_at(MONDAY) == at("2026-11-17T10:00")


@pytest.mark.parametrize(
    "now, day",
    [
        ("2026-11-16T08:59:59", date(2026, 11, 15)),
        ("2026-11-16T09:00:00", MONDAY),
        ("2026-11-17T08:59:59", MONDAY),
        # the overlap hour: Tuesday's is the Daily Seed while Monday's still takes rounds
        ("2026-11-17T09:30:00", date(2026, 11, 17)),
    ],
)
def test_the_daily_date_is_the_latest_daily_to_open(now, day):
    assert daily_date(at(now)) == day


def test_two_dailies_are_open_for_an_hour():
    now = at("2026-11-17T09:30")
    assert is_open(MONDAY, now)
    assert is_open(date(2026, 11, 17), now)
    assert not is_open(MONDAY, at("2026-11-17T10:00"))


def test_the_daily_date_reads_an_aware_time_in_any_zone():
    eastern = datetime.fromisoformat("2026-11-16T04:30:00-05:00")
    assert daily_date(eastern) == MONDAY


@pytest.mark.parametrize(
    "number, text",
    [
        (1, "1ST"),
        (2, "2ND"),
        (3, "3RD"),
        (4, "4TH"),
        (11, "11TH"),
        (12, "12TH"),
        (13, "13TH"),
        (21, "21ST"),
        (22, "22ND"),
        (23, "23RD"),
        (30, "30TH"),
        (31, "31ST"),
    ],
)
def test_ordinals(number, text):
    assert ordinal(number) == text


def test_magic_words_are_the_date_and_a_rerun_drops_the_weekday():
    assert magic_words(MONDAY) == ("MON", "NOV", "16TH")
    assert magic_words(MONDAY, RERUN) == ("NOV", "16TH", "RERUN")
    assert magic_words(date(2026, 11, 19)) == ("THURS", "NOV", "19TH")


def test_every_day_of_a_year_makes_drawable_magic_words():
    day = date(2027, 1, 1)
    while day.year == 2027:
        for attempt in (FIRST, RERUN):
            check_magic_words(magic_words(day, attempt))
        day = date.fromordinal(day.toordinal() + 1)


def test_the_prng_seed_is_keyed_by_the_secret_the_date_and_the_attempt():
    seed = prng_seed("secret", MONDAY)
    assert len(seed) == 16 and int(seed, 16) >= 0
    assert prng_seed("secret", MONDAY) == seed
    assert prng_seed("other", MONDAY) != seed
    assert prng_seed("secret", date(2026, 11, 17)) != seed
    assert prng_seed("secret", MONDAY, RERUN) != seed


def test_the_prng_seed_needs_a_secret_and_a_known_attempt():
    with pytest.raises(ValueError):
        prng_seed("", MONDAY)
    with pytest.raises(ValueError):
        prng_seed("secret", MONDAY, 3)


def test_the_checked_in_schedule_resolves_every_weekday():
    schedule = Schedule.load()
    assert set(schedule.weekdays) == set(WEEKDAYS)
    for plan in schedule.weekdays.values():
        assert plan.theme.name
        resolve(dict(plan.request), "0123456789abcdef")


def test_sunday_needs_only_the_us_rom():
    sunday = Schedule.load().plan(date(2026, 11, 22))
    assert sunday.request["sources"] == ["nes_open_us"]


def schedule_json(**changes) -> dict:
    data = {
        "weekdays": {
            name: {"theme": {"name": name.title()}, "request": {}} for name in WEEKDAYS
        },
        "overrides": {},
    }
    data.update(changes)
    return data


def test_an_override_replaces_its_weekday_for_one_date():
    data = schedule_json(
        overrides={
            "2026-11-16": {
                "theme": {"name": "Spring Major", "description": "The first major"},
                "request": {},
            }
        }
    )
    schedule = Schedule.from_json(data)
    assert schedule.plan(MONDAY).theme == Theme("Spring Major", "The first major")
    assert schedule.plan(date(2026, 11, 23)).theme == Theme("Monday")


@pytest.mark.parametrize(
    "data, problem",
    [
        ([], "weekdays and overrides"),
        (schedule_json(weekdays={}), "each of"),
        (
            schedule_json(
                overrides={"next week": {"theme": {"name": "x"}, "request": {}}}
            ),
            "YYYY-MM-DD",
        ),
        (
            schedule_json(overrides={"2026-11-16": {"theme": "x", "request": {}}}),
            "a name and an optional description",
        ),
        (
            schedule_json(
                overrides={"2026-11-16": {"theme": {"description": "x"}, "request": {}}}
            ),
            "a name and an optional description",
        ),
        (
            schedule_json(
                overrides={
                    "2026-11-16": {
                        "theme": {"name": "x", "color": "red"},
                        "request": {},
                    }
                }
            ),
            "a name and an optional description",
        ),
        (
            schedule_json(
                overrides={"2026-11-16": {"theme": {"name": ""}, "request": {}}}
            ),
            "theme name",
        ),
        (
            schedule_json(
                overrides={
                    "2026-11-16": {
                        "theme": {"name": "x", "description": " x "},
                        "request": {},
                    }
                }
            ),
            "theme description",
        ),
        (
            schedule_json(
                overrides={
                    "2026-11-16": {"theme": {"name": "x"}, "request": {"bogus": 1}}
                }
            ),
            "override 2026-11-16",
        ),
        (
            schedule_json(overrides={"2026-11-16": {"theme": {"name": "x"}}}),
            "theme and a request",
        ),
    ],
)
def test_a_bad_schedule_is_refused(data, problem):
    with pytest.raises(ScheduleError, match=problem):
        Schedule.from_json(data)


def test_a_schedule_file_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text("{")
    with pytest.raises(ScheduleError, match="not JSON"):
        Schedule.load(path)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load()


@pytest.fixture(scope="module")
def curation() -> CurationSnapshot:
    return CurationSnapshot.load()


def test_a_daily_is_generated_again_the_same_with_the_date_as_its_words(
    catalog, curation
):
    plan = Schedule.load().plan(MONDAY)
    first = generate_daily(catalog, curation, plan, "secret", MONDAY)
    again = generate_daily(catalog, curation, plan, "secret", MONDAY)
    assert first == again
    assert first.course.magic_words == ("MON", "NOV", "16TH")
    assert first.settings.prng_seed == prng_seed("secret", MONDAY)
    assert first.request == plan.request


def test_a_rerun_is_a_different_seed(catalog, curation):
    plan = DayPlan(Theme("Monday"), {})
    first = generate_daily(catalog, curation, plan, "secret", MONDAY)
    rerun = generate_daily(catalog, curation, plan, "secret", MONDAY, RERUN)
    assert rerun.course.magic_words == ("NOV", "16TH", "RERUN")
    assert rerun.course.holes != first.course.holes


def test_the_manifest_carries_the_date_through_json(catalog, curation):
    manifest = generate_daily(
        catalog, curation, DayPlan(Theme("Monday"), {}), "secret", MONDAY
    )
    assert json.loads(json.dumps(manifest.to_json()))["course"]["magic_words"] == [
        "MON",
        "NOV",
        "16TH",
    ]
