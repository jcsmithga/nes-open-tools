"""
The daily seed: the day's window, its schedule, its PRNG seed and its magic words.

One seed a day, open from 09:00 UTC for 25 hours (ADR 0033). A daily's date is its own:
the day it opens at 09:00 UTC, whatever the calendar says anywhere else when it opens. For
the hour two dailies overlap, the latest is the Daily Seed.

Each weekday has a request (ADR 0016) in `data/daily_schedule.json`, with a theme that
names it on the site, and a dated override replaces a weekday's for one date. A theme is
data, stored with the day when it is made, so renaming a weekday's theme leaves past days
alone, and an event's override names itself. The reasons for the settings are in
`docs/competitive/schedule.md`.

The PRNG seed is a keyed hash of the date and the attempt under the server's secret, so
generation is idempotent, while the public requests and the CLI cannot make a daily ahead
of time. Attempt 2 is the rerun that replaces a seed nobody can complete. The magic words
are the date (`MON NOV 16TH`), and a rerun's drop the weekday for `RERUN`
(`NOV 16TH RERUN`). Magic words have their own stream of the PRNG seed, so setting them
moves no other draw.

Nothing here touches a database; the site's `server/dailies.py` stores dailies.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from .catalog import Catalog
from .curation import CurationSnapshot
from .generate import PRNG_SEED_BYTES, generate
from .manifest import Manifest
from .request import RequestError, check_request, resolve

SCHEDULE = Path(__file__).resolve().parent / "data" / "daily_schedule.json"

#: when a daily opens, every day, and how long it stays open
OPENS = time(9, 0)
WINDOW = timedelta(hours=25)

#: attempt 1 is the day's seed; attempt 2 the rerun that replaces it, at most once
FIRST = 1
RERUN = 2
ATTEMPTS = (FIRST, RERUN)

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
#: `date.weekday()` order, as the magic words write them
WEEKDAY_WORDS = ("MON", "TUES", "WED", "THURS", "FRI", "SAT", "SUN")
MONTH_WORDS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)
RERUN_WORD = "RERUN"


class ScheduleError(ValueError):
    """A schedule file that is not one: a missing weekday, a bad theme or override date,
    or a request that cannot resolve."""


def opens_at(day: date) -> datetime:
    return datetime.combine(day, OPENS, tzinfo=UTC)


def closes_at(day: date) -> datetime:
    return opens_at(day) + WINDOW


def daily_date(now: datetime) -> date:
    """The date of the latest daily open at `now`, an aware datetime: the Daily Seed's."""
    now = now.astimezone(UTC)
    today = now.date()
    return today if now >= opens_at(today) else today - timedelta(days=1)


def is_open(day: date, now: datetime) -> bool:
    return opens_at(day) <= now < closes_at(day)


def ordinal(number: int) -> str:
    """1ST, 2ND, 3RD, 4TH ... 11TH, 12TH, 13TH ... 21ST."""
    if 11 <= number % 100 <= 13:
        suffix = "TH"
    else:
        suffix = {1: "ST", 2: "ND", 3: "RD"}.get(number % 10, "TH")
    return f"{number}{suffix}"


def magic_words(day: date, attempt: int = FIRST) -> tuple[str, str, str]:
    """`MON NOV 16TH` for the day's seed, `NOV 16TH RERUN` for its rerun."""
    month = MONTH_WORDS[day.month - 1]
    if attempt == FIRST:
        return WEEKDAY_WORDS[day.weekday()], month, ordinal(day.day)
    if attempt == RERUN:
        return month, ordinal(day.day), RERUN_WORD
    raise ValueError(f"a daily's attempt is one of {ATTEMPTS}, got {attempt!r}")


def prng_seed(secret: str, day: date, attempt: int = FIRST) -> str:
    """The day's PRNG seed: a keyed hash of the date and attempt, cut to the 16 hex
    characters a drawn seed has. The manifest publishes it, so it must reveal neither
    the secret nor another day's seed."""
    if not secret:
        raise ValueError("a daily's PRNG seed needs the server's secret")
    if attempt not in ATTEMPTS:
        raise ValueError(f"a daily's attempt is one of {ATTEMPTS}, got {attempt!r}")
    message = f"daily\0{day.isoformat()}\0{attempt}".encode()
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return digest[: PRNG_SEED_BYTES * 2]


@dataclass(frozen=True)
class Theme:
    """What names a day on the site: a name, and an optional one-line description, as
    for a major. Player-facing text, written in the schedule."""

    name: str
    description: str | None = None


@dataclass(frozen=True)
class DayPlan:
    """What a day is generated from: the theme that names it, and its request."""

    theme: Theme
    request: Mapping


@dataclass(frozen=True)
class Schedule:
    weekdays: Mapping[str, DayPlan]
    overrides: Mapping[date, DayPlan]

    @classmethod
    def load(cls, path: Path = SCHEDULE) -> "Schedule":
        try:
            data = json.loads(Path(path).read_text())
        except json.JSONDecodeError as problem:
            raise ScheduleError(f"{path}: not JSON: {problem}") from None
        return cls.from_json(data, str(path))

    @classmethod
    def from_json(cls, data: object, where: str = "schedule") -> "Schedule":
        if not isinstance(data, dict) or set(data) != {"weekdays", "overrides"}:
            raise ScheduleError(f"{where}: expected weekdays and overrides")
        weekdays = data["weekdays"]
        if not isinstance(weekdays, dict) or set(weekdays) != set(WEEKDAYS):
            raise ScheduleError(f"{where}: weekdays must name each of {WEEKDAYS}")
        overrides = data["overrides"]
        if not isinstance(overrides, dict):
            raise ScheduleError(f"{where}: overrides must map dates to days")
        dated: dict[date, DayPlan] = {}
        for text, plan in overrides.items():
            try:
                day = date.fromisoformat(text)
            except (TypeError, ValueError):
                raise ScheduleError(
                    f"{where}: override {text!r} is not a YYYY-MM-DD date"
                ) from None
            dated[day] = _plan(plan, f"{where}: override {text}")
        return cls(
            weekdays={
                name: _plan(weekdays[name], f"{where}: {name}") for name in WEEKDAYS
            },
            overrides=dated,
        )

    def plan(self, day: date) -> DayPlan:
        """The override for `day`, or its weekday's plan."""
        return self.overrides.get(day) or self.weekdays[WEEKDAYS[day.weekday()]]


def _plan(data: object, where: str) -> DayPlan:
    if not isinstance(data, dict) or set(data) != {"theme", "request"}:
        raise ScheduleError(f"{where}: expected a theme and a request")
    theme = _theme(data["theme"], where)
    try:
        check_request(data["request"])
    except RequestError as problem:
        raise ScheduleError(f"{where}: {problem}") from None
    return DayPlan(theme, data["request"])


def _theme(data: object, where: str) -> Theme:
    if (
        not isinstance(data, dict)
        or "name" not in data
        or not set(data) <= {"name", "description"}
    ):
        raise ScheduleError(f"{where}: a theme is a name and an optional description")
    for field, text in data.items():
        if not isinstance(text, str) or not text.strip() or text != text.strip():
            raise ScheduleError(
                f"{where}: theme {field} {text!r} is not text without outer spaces"
            )
    return Theme(data["name"], data.get("description"))


def generate_daily(
    catalog: Catalog,
    curation: CurationSnapshot,
    plan: DayPlan,
    secret: str,
    day: date,
    attempt: int = FIRST,
) -> Manifest:
    """The day's manifest: its request resolved and generated from the day's PRNG seed,
    with the date as its magic words."""
    seed = prng_seed(secret, day, attempt)
    request = dict(plan.request)
    manifest = generate(catalog, curation, resolve(request, seed), request)
    course = replace(manifest.course, magic_words=magic_words(day, attempt))
    return replace(manifest, course=course)
