"""The daily seed: the only code that writes `dailies`, `daily_seeds` and `daily_cards`.

One seed a day, open from 09:00 UTC for 25 hours (ADR 0033; the window, schedule, PRNG
seed and magic words are `golf/randomizer/daily.py`). The rollover runs in the app: on its
first look at or after a day's opening, it generates the day from the schedule and stores
it as an ordinary seed with a `dailies` row. Generation is idempotent, so a look that finds
the day stored does nothing, and a restart that missed the opening catches up. Only the
current day is made: a day nobody generated before it closed is skipped, since nobody could
play it.

A day's seed is its latest attempt. A rerun (attempt 2) replaces a seed nobody can
complete, at most once and only while the day has no card; it withdraws the replaced seed's
downloads. A withdrawn day counts toward nothing. Both are admin actions, logged through
`server/audit.py`.

A card records that a player's own round (slot 0) on the day's seed arrived while the day
was open (docs/competitive/data_model.md). It is written in the transaction that records
the round, and is never deleted: it counts while its round is recorded, so a voided round's
card is ignored until the round is restored, and a new round in the freed slot takes the
card over.
"""

import asyncio
import json
import logging
import sqlite3
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from starlette.concurrency import run_in_threadpool

from golf.randomizer.daily import (
    FIRST,
    RERUN,
    DayPlan,
    Schedule,
    Theme,
    closes_at,
    daily_date,
    opens_at,
)

from . import audit
from .builder import BuilderUnavailableError, SeedBuilder
from .db import Database
from .seeds import (
    SeedAlreadyWithdrawnError,
    insert_seed,
    utc_now,
    withdraw_seed_on,
)

#: seconds between the rollover's looks at whether the day's daily exists
ROLLOVER_SECONDS = 60

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

log = logging.getLogger(__name__)


def utc_text(moment: datetime) -> str:
    """An aware datetime as the stored UTC text, which sorts as it compares."""
    return moment.astimezone(UTC).strftime(TIME_FORMAT)


class DailyError(ValueError):
    """An admin action the day's state refuses."""


class DailyClosedError(DailyError):
    """The day has closed, so a rerun would come too late to play."""


class DailyPlayedError(DailyError):
    """A player has a card for the day, so its seed can be completed: withdraw it instead."""


class DailyReplacedError(DailyError):
    """The day's seed has already been replaced once."""


class DailyWithdrawnError(DailyError):
    """The day is withdrawn."""


class DailyNotWithdrawnError(DailyError):
    """The day is not withdrawn, so there is nothing to restore."""


@dataclass(frozen=True)
class Daily:
    day: date
    #: the theme the schedule gave the day when it was made, which names it on the site
    theme: Theme
    opens_at: str
    closes_at: str
    withdrawn_at: str | None
    #: the day's seed: its latest attempt
    seed_id: str
    attempt: int
    #: attempt 1's seed, when a rerun replaced it
    replaced_seed_id: str | None
    #: players with a card whose round is still recorded
    players: int

    @property
    def withdrawn(self) -> bool:
        return self.withdrawn_at is not None

    @property
    def rerun(self) -> bool:
        return self.attempt == RERUN

    def is_open(self, now: datetime) -> bool:
        moment = utc_text(now)
        return self.opens_at <= moment < self.closes_at


_DAILY_SELECT = """
    SELECT dailies.day, dailies.theme_name, dailies.theme_description, dailies.opens_at, dailies.closes_at,
           dailies.withdrawn_at, current.seed_id, current.attempt,
           (SELECT replaced.seed_id FROM daily_seeds AS replaced
            WHERE replaced.day = dailies.day AND replaced.replaced_at IS NOT NULL)
               AS replaced_seed_id,
           (SELECT count(*) FROM daily_cards
            JOIN rounds ON rounds.public_id = daily_cards.round_public_id
            WHERE daily_cards.day = dailies.day) AS players
    FROM dailies
    JOIN daily_seeds AS current
        ON current.day = dailies.day AND current.replaced_at IS NULL
"""


def _daily(row: sqlite3.Row) -> Daily:
    return Daily(
        day=date.fromisoformat(row["day"]),
        theme=Theme(row["theme_name"], row["theme_description"]),
        opens_at=row["opens_at"],
        closes_at=row["closes_at"],
        withdrawn_at=row["withdrawn_at"],
        seed_id=row["seed_id"],
        attempt=row["attempt"],
        replaced_seed_id=row["replaced_seed_id"],
        players=row["players"],
    )


def _load(conn: sqlite3.Connection, day: date) -> Daily | None:
    row = conn.execute(
        f"{_DAILY_SELECT} WHERE dailies.day = ?", (day.isoformat(),)
    ).fetchone()
    return None if row is None else _daily(row)


def load_daily(db: Database, day: date) -> Daily | None:
    with db.transaction() as conn:
        return _load(conn, day)


def daily_seed(db: Database, now: datetime) -> Daily | None:
    """The Daily Seed: the latest daily open at `now` and not withdrawn."""
    moment = utc_text(now)
    with db.transaction() as conn:
        row = conn.execute(
            f"""{_DAILY_SELECT}
            WHERE dailies.opens_at <= ? AND dailies.closes_at > ?
              AND dailies.withdrawn_at IS NULL
            ORDER BY dailies.day DESC LIMIT 1""",
            (moment, moment),
        ).fetchone()
    return None if row is None else _daily(row)


def opened_dailies(db: Database, first: date, last: date, now: datetime) -> list[Daily]:
    """The dailies from `first` to `last` inclusive that had opened by `now`, by date."""
    with db.transaction() as conn:
        rows = conn.execute(
            f"""{_DAILY_SELECT}
            WHERE dailies.day BETWEEN ? AND ? AND dailies.opens_at <= ?
            ORDER BY dailies.day""",
            (first.isoformat(), last.isoformat(), utc_text(now)),
        ).fetchall()
    return [_daily(row) for row in rows]


@dataclass(frozen=True)
class SeedDaily:
    """The daily a seed was made for, as its page shows it."""

    daily: Daily
    attempt: int

    @property
    def replaced(self) -> bool:
        """This seed is the one a rerun replaced, no longer the day's."""
        return self.attempt != self.daily.attempt


def daily_for_seed(db: Database, seed_id: str) -> SeedDaily | None:
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT day, attempt FROM daily_seeds WHERE seed_id = ?", (seed_id,)
        ).fetchone()
        if row is None:
            return None
        daily = _load(conn, date.fromisoformat(row["day"]))
    assert daily is not None
    return SeedDaily(daily, row["attempt"])


def has_played(db: Database, seed_id: str, user_id: int) -> bool:
    """Whether the player's entry on the seed has a recorded round, theirs or a teammate's."""
    with db.transaction() as conn:
        return (
            conn.execute(
                """
                SELECT 1 FROM rounds JOIN entries ON entries.id = rounds.entry_id
                WHERE entries.seed_id = ? AND entries.user_id = ?
                """,
                (seed_id, user_id),
            ).fetchone()
            is not None
        )


def scores_hidden(
    db: Database, seed_id: str, viewer_id: int | None, now: datetime
) -> bool:
    """Whether a seed's scores are hidden from this viewer (None signed out): the seed is
    an open daily's and the viewer has recorded no round on it (design.md, Spoilers)."""
    found = daily_for_seed(db, seed_id)
    if found is None or found.replaced or not found.daily.is_open(now):
        return False
    return viewer_id is None or not has_played(db, seed_id, viewer_id)


# -- Cards ----------------------------------------------------------------------------------


def card_round(
    conn: sqlite3.Connection,
    public_id: str,
    seed_id: str,
    user_id: int,
    slot: int,
    received_at: str,
) -> bool:
    """Give a just-recorded round a card when it is a player's own round on a day's seed,
    received while the day was open and not withdrawn. Returns whether it did.

    The caller holds the transaction that recorded the round. A card whose round has since
    been voided passes to this one, the round now in the freed slot.
    """
    if slot != 0:
        return False
    row = conn.execute(
        """
        SELECT dailies.day, dailies.opens_at, dailies.closes_at, dailies.withdrawn_at
        FROM daily_seeds JOIN dailies ON dailies.day = daily_seeds.day
        WHERE daily_seeds.seed_id = ? AND daily_seeds.replaced_at IS NULL
        """,
        (seed_id,),
    ).fetchone()
    if row is None or row["withdrawn_at"] is not None:
        return False
    if not row["opens_at"] <= received_at < row["closes_at"]:
        return False
    held = conn.execute(
        """
        SELECT daily_cards.round_public_id, rounds.id AS recorded
        FROM daily_cards
        LEFT JOIN rounds ON rounds.public_id = daily_cards.round_public_id
        WHERE daily_cards.day = ? AND daily_cards.user_id = ?
        """,
        (row["day"], user_id),
    ).fetchone()
    if held is None:
        conn.execute(
            """
            INSERT INTO daily_cards (day, user_id, round_public_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (row["day"], user_id, public_id, received_at),
        )
        return True
    if held["recorded"] is not None:  # pragma: no cover - one own round per entry
        return False
    conn.execute(
        """
        UPDATE daily_cards SET round_public_id = ?, created_at = ?
        WHERE day = ? AND user_id = ?
        """,
        (public_id, received_at, row["day"], user_id),
    )
    return True


@dataclass(frozen=True)
class CardRound:
    """A player's card on a day: their round on the day's seed, its total and the par."""

    public_id: str
    strokes: int
    par: int

    @property
    def to_par(self) -> str:
        """The total against par as golf writes it: +2, E, -1."""
        over = self.strokes - self.par
        return "E" if over == 0 else f"{over:+d}"


def card_for(db: Database, day: date, user_id: int) -> CardRound | None:
    """The player's card on the day, while its round is still recorded."""
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT rounds.public_id, rounds.total_strokes,
                   (SELECT sum(seed_holes.par) FROM seed_holes
                    WHERE seed_holes.seed_id = entries.seed_id) AS par
            FROM daily_cards
            JOIN rounds ON rounds.public_id = daily_cards.round_public_id
            JOIN entries ON entries.id = rounds.entry_id
            WHERE daily_cards.day = ? AND daily_cards.user_id = ?
            """,
            (day.isoformat(), user_id),
        ).fetchone()
    if row is None:
        return None
    return CardRound(row["public_id"], row["total_strokes"], row["par"])


# -- Making a day -----------------------------------------------------------------------------


def ensure_daily(
    db: Database,
    builder: SeedBuilder,
    schedule: Schedule,
    secret: str,
    day: date,
    now: str | None = None,
) -> Daily:
    """The day's daily, generated from the schedule, built and stored if it is not yet.

    Building runs outside the database lock. Raises BuilderUnavailableError when the
    server cannot build, and whatever generation raises.
    """
    found = load_daily(db, day)
    if found is not None:
        return found
    created_at = now if now is not None else utc_now()
    plan = schedule.plan(day)
    manifest = builder.generate_daily(plan, secret, day, FIRST)
    built = builder.build(manifest)
    seed_id = insert_seed(
        db, manifest, built.unfinished_ips, built.holes, builder.catalog, now=created_at
    )
    try:
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO dailies (
                    day, theme_name, theme_description, request,
                    opens_at, closes_at, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day.isoformat(),
                    plan.theme.name,
                    plan.theme.description,
                    json.dumps(plan.request, sort_keys=True),
                    utc_text(opens_at(day)),
                    utc_text(closes_at(day)),
                    created_at,
                ),
            )
            conn.execute(
                "INSERT INTO daily_seeds (day, attempt, seed_id) VALUES (?, ?, ?)",
                (day.isoformat(), FIRST, seed_id),
            )
    except sqlite3.IntegrityError:  # pragma: no cover - one rollover runs at a time
        # another look stored the day first; this seed stays an ordinary seed
        log.warning("daily %s was stored twice; seed %s is unused", day, seed_id)
    daily = load_daily(db, day)
    assert daily is not None
    log.info("generated the daily for %s: seed %s", day, daily.seed_id)
    return daily


def roll_over(
    db: Database,
    builder: SeedBuilder,
    schedule: Schedule,
    secret: str,
    now: datetime,
) -> Daily:
    """Make sure the daily open at `now` exists."""
    return ensure_daily(db, builder, schedule, secret, daily_date(now), utc_text(now))


async def roll_over_periodically(
    db: Database,
    builder: SeedBuilder,
    schedule: Schedule,
    secret: str,
    clock: Callable[[], datetime],
    seconds: float = ROLLOVER_SECONDS,
) -> None:
    """The app's rollover: look now, then every `seconds`, until cancelled. A failure is
    logged and tried again at the next look."""
    while True:
        try:
            await run_in_threadpool(roll_over, db, builder, schedule, secret, clock())
        except BuilderUnavailableError as problem:
            log.error("cannot generate the daily until this is fixed: %s", problem)
        except Exception:
            log.exception("generating the daily failed")
        await asyncio.sleep(seconds)


# -- Admin actions ----------------------------------------------------------------------------


def _note(text: str | None) -> str | None:
    text = (text or "").strip()
    return text or None


def _check_replaceable(conn: sqlite3.Connection, day: date, now: str) -> Daily:
    daily = _load(conn, day)
    if daily is None:
        raise KeyError(day)
    if daily.withdrawn:
        raise DailyWithdrawnError(f"{day} is withdrawn")
    if daily.rerun:
        raise DailyReplacedError(f"{day} has been replaced once already")
    if now >= daily.closes_at:
        raise DailyClosedError(f"{day} has closed")
    carded = conn.execute(
        "SELECT 1 FROM daily_cards WHERE day = ?", (day.isoformat(),)
    ).fetchone()
    if carded is not None:
        raise DailyPlayedError(f"{day} has a recorded round on its seed")
    return daily


def replace_daily(
    db: Database,
    builder: SeedBuilder,
    secret: str,
    day: date,
    admin_id: int,
    note: str | None = None,
    now: str | None = None,
) -> Daily:
    """Replace a day's seed with its rerun, withdrawing the replaced seed's downloads,
    and log it. The rerun is generated from the theme and request the day stored.

    Raises KeyError for a missing day, and a DailyError when the day is withdrawn,
    already replaced, closed, or has a card. Checked before building and again when
    storing.
    """
    at = now if now is not None else utc_now()
    note = _note(note)
    with db.transaction() as conn:
        _check_replaceable(conn, day, at)
        stored = conn.execute(
            """
            SELECT theme_name, theme_description, request FROM dailies WHERE day = ?
            """,
            (day.isoformat(),),
        ).fetchone()
    plan = DayPlan(
        Theme(stored["theme_name"], stored["theme_description"]),
        json.loads(stored["request"]),
    )
    manifest = builder.generate_daily(plan, secret, day, RERUN)
    built = builder.build(manifest)
    seed_id = insert_seed(
        db, manifest, built.unfinished_ips, built.holes, builder.catalog, now=at
    )
    with db.transaction() as conn:
        replaced = _check_replaceable(conn, day, at)
        conn.execute(
            "UPDATE daily_seeds SET replaced_at = ? WHERE day = ? AND attempt = ?",
            (at, day.isoformat(), FIRST),
        )
        conn.execute(
            "INSERT INTO daily_seeds (day, attempt, seed_id) VALUES (?, ?, ?)",
            (day.isoformat(), RERUN, seed_id),
        )
        # an admin may have withdrawn it already, by hand
        with suppress(SeedAlreadyWithdrawnError):
            withdraw_seed_on(conn, replaced.seed_id, admin_id, note, at)
        audit.record(
            conn,
            admin_id,
            audit.REPLACE,
            audit.DAILY,
            day.isoformat(),
            at,
            note=note,
            detail={"replaced": replaced.seed_id, "seed": seed_id},
        )
        daily = _load(conn, day)
    assert daily is not None
    return daily


def withdraw_daily(
    db: Database,
    day: date,
    admin_id: int,
    note: str | None = None,
    now: str | None = None,
) -> None:
    """Withdraw a day, which then counts toward nothing, and log it. Its seed stays an
    ordinary seed. Raises KeyError for a missing day and DailyWithdrawnError."""
    at = now if now is not None else utc_now()
    note = _note(note)
    with db.transaction() as conn:
        daily = _load(conn, day)
        if daily is None:
            raise KeyError(day)
        if daily.withdrawn:
            raise DailyWithdrawnError(f"{day} is already withdrawn")
        conn.execute(
            "UPDATE dailies SET withdrawn_at = ? WHERE day = ?", (at, day.isoformat())
        )
        audit.record(
            conn, admin_id, audit.WITHDRAW, audit.DAILY, day.isoformat(), at, note=note
        )


def restore_daily(
    db: Database, day: date, admin_id: int, now: str | None = None
) -> None:
    """Restore a withdrawn day and log it. Rounds that arrived while it was withdrawn got
    no card and stay without one. Raises KeyError and DailyNotWithdrawnError."""
    at = now if now is not None else utc_now()
    with db.transaction() as conn:
        daily = _load(conn, day)
        if daily is None:
            raise KeyError(day)
        if not daily.withdrawn:
            raise DailyNotWithdrawnError(f"{day} is not withdrawn")
        conn.execute(
            "UPDATE dailies SET withdrawn_at = NULL WHERE day = ?", (day.isoformat(),)
        )
        audit.record(conn, admin_id, audit.RESTORE, audit.DAILY, day.isoformat(), at)


def first_daily_day(db: Database) -> date | None:
    """The earliest day with a daily, where the calendar starts."""
    with db.transaction() as conn:
        row = conn.execute("SELECT min(day) AS day FROM dailies").fetchone()
    return None if row["day"] is None else date.fromisoformat(row["day"])


def month_bounds(year: int, month: int) -> tuple[date, date]:
    first = date(year, month, 1)
    following = date(year + month // 12, month % 12 + 1, 1)
    return first, following - timedelta(days=1)
