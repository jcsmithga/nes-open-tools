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
from golf.ratings.formats import (
    FieldCard,
    day_results,
    net,
    skin_holes,
    stableford,
)
from golf.ratings.handicap import daily_handicap, strokes_received
from golf.ratings.index import IndexState
from golf.ratings.params import IndexRules
from golf.ratings.rules import DailyRules

from . import audit
from .builder import BuilderUnavailableError, SeedBuilder
from .db import Database
from .ratings import Ratings, refit, seed_rating_on
from .round_indexes import record_step, stored_index
from .seeds import (
    SeedAlreadyWithdrawnError,
    SeedRater,
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


def previous_daily(db: Database, now: datetime) -> Daily | None:
    """The day before the Daily Seed's date at `now`, unless it was never made or is
    withdrawn. Open for the first hour of the next day's window, then closed."""
    found = load_daily(db, daily_date(now) - timedelta(days=1))
    return None if found is None or found.withdrawn else found


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
    state: IndexState | None = None,
    index_rules: IndexRules | None = None,
) -> bool:
    """Give a just-recorded round a card when it is a player's own round on a day's seed,
    received while the day was open and not withdrawn. Returns whether it did.

    A daily counts toward an index, so the round's step is stored from `state`, the
    index the player was scanned at, under `index_rules` (`record_step`), unless its
    seed is unrated, which only a database before its first ratings run allows.

    The caller holds the transaction that recorded the round and stored its index. A
    card whose round has since been voided passes to this one, the round now in the
    freed slot.
    """
    if slot != 0:
        return False
    row = conn.execute(
        """
        SELECT dailies.day, dailies.opens_at, dailies.closes_at, dailies.withdrawn_at,
               dailies.rules
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
        _count(conn, public_id, seed_id, state, index_rules)
        _fill_with(conn, row["day"], user_id, row["rules"])
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
    _count(conn, public_id, seed_id, state, index_rules)
    _fill_with(conn, row["day"], user_id, row["rules"])
    return True


def _count(
    conn: sqlite3.Connection,
    public_id: str,
    seed_id: str,
    state: IndexState | None,
    index_rules: IndexRules | None,
) -> None:
    """Store a daily round's step in its player's index."""
    if state is None or index_rules is None:
        return
    rating = seed_rating_on(conn, seed_id)
    if rating is None:
        log.warning(
            "round %s is on unrated seed %s: it does not count", public_id, seed_id
        )
        return
    record_step(conn, public_id, state, rating, index_rules)


def _fill_with(
    conn: sqlite3.Connection, day: str, user_id: int, rules: str | None
) -> None:
    if rules is not None:
        fill_card(conn, day, user_id, DailyRules.from_json(json.loads(rules)))
        score_day(conn, day)


def _day_rules(conn: sqlite3.Connection, day: str) -> DailyRules | None:
    row = conn.execute("SELECT rules FROM dailies WHERE day = ?", (day,)).fetchone()
    if row is None or row["rules"] is None:
        return None
    return DailyRules.from_json(json.loads(row["rules"]))


_FIELD_SQL = """
SELECT c.user_id, c.handicap, r.id AS round_id, s.seed_id
FROM daily_cards c
JOIN dailies d ON d.day = c.day
JOIN rounds r ON r.public_id = c.round_public_id
JOIN daily_seeds s ON s.day = c.day AND s.replaced_at IS NULL
WHERE c.day = ? AND d.withdrawn_at IS NULL AND c.handicap IS NOT NULL
ORDER BY c.user_id
"""


def score_day(conn: sqlite3.Connection, day: str) -> None:
    """Rebuild a day's results from its filled cards whose rounds are recorded: the
    standing, every pair's match and the skins, under the day's rules. A day withdrawn,
    or without rules, has none. The caller holds the transaction."""
    conn.execute("DELETE FROM daily_results WHERE day = ?", (day,))
    conn.execute("DELETE FROM daily_matches WHERE day = ?", (day,))
    rules = _day_rules(conn, day)
    if rules is None:
        return
    field = conn.execute(_FIELD_SQL, (day,)).fetchall()
    if not field:
        return
    cards = []
    for row in field:
        strokes = tuple(
            h["strokes"]
            for h in conn.execute(
                "SELECT strokes FROM round_holes WHERE round_id = ? ORDER BY position",
                (row["round_id"],),
            )
        )
        holes = conn.execute(
            """
            SELECT net, points FROM daily_card_holes
            WHERE day = ? AND user_id = ? ORDER BY position
            """,
            (day, row["user_id"]),
        ).fetchall()
        cards.append(
            FieldCard(
                player=row["user_id"],
                strokes=strokes,
                handicap=row["handicap"],
                nets=tuple(h["net"] for h in holes),
                points=tuple(h["points"] for h in holes),
            )
        )
    stroke_indexes = [
        r["stroke_index"]
        for r in conn.execute(
            """
            SELECT stroke_index FROM seed_hole_ratings WHERE seed_id = ?
            ORDER BY position
            """,
            (field[0]["seed_id"],),
        )
    ]
    results = day_results(cards, stroke_indexes, rules)
    conn.executemany(
        """
        INSERT INTO daily_results (day, user_id, points, position, skins, won)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (day, user, r.points, r.position, r.skins, int(r.won))
            for user, r in results.players.items()
        ],
    )
    conn.executemany(
        """
        INSERT INTO daily_matches (day, user_a, user_b, holes_a, holes_b, winner_id,
                                   margin)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (day, m.a, m.b, m.holes_a, m.holes_b, m.winner, m.margin)
            for m in results.matches
        ],
    )


def rescore_round(conn: sqlite3.Connection, public_id: str) -> None:
    """After a round is voided or restored: fill its card again, if it has one, and
    rescore its day. The caller holds the transaction that voided or restored it."""
    card = conn.execute(
        "SELECT day, user_id FROM daily_cards WHERE round_public_id = ?", (public_id,)
    ).fetchone()
    if card is None:
        return
    rules = _day_rules(conn, card["day"])
    if rules is None:
        return
    fill_card(conn, card["day"], card["user_id"], rules)
    score_day(conn, card["day"])


def fill_card(
    conn: sqlite3.Connection, day: str, user_id: int, rules: DailyRules
) -> bool:
    """Fill a card from its round, as its player is shown it: the index the round was
    scanned at, stored with it (`server/round_indexes.py`), and the daily handicap and
    net card it gives. Returns whether it did: a card whose round is voided or stored no
    index, on a withdrawn day, or on a seed not yet rated stays as it is.

    The caller holds the transaction. A card is a function of its round's stored index,
    its seed's stored ratings and `rules`, so filling it again gives the same card.
    """
    card = conn.execute(
        """
        SELECT c.round_public_id, r.id AS round_id, e.seed_id
        FROM daily_cards c
        JOIN dailies d ON d.day = c.day
        JOIN rounds r ON r.public_id = c.round_public_id
        JOIN entries e ON e.id = r.entry_id
        WHERE c.day = ? AND c.user_id = ? AND d.withdrawn_at IS NULL
        """,
        (day, user_id),
    ).fetchone()
    if card is None:
        return False
    index = stored_index(conn, card["round_public_id"])
    rating = seed_rating_on(conn, card["seed_id"])
    if index is None or rating is None:
        return False
    strokes = [
        row["strokes"]
        for row in conn.execute(
            "SELECT strokes FROM round_holes WHERE round_id = ? ORDER BY position",
            (card["round_id"],),
        )
    ]
    holes = rating.holes
    handicap = daily_handicap(index, rating, rules.allowance)
    received = strokes_received(handicap, [h.stroke_index for h in holes])
    pars = [h.par for h in holes]
    nets = net(strokes, received)
    points = stableford(strokes, pars, received, rules.stableford_par)
    conn.execute(
        """
        UPDATE daily_cards SET index_used = ?, handicap = ?
        WHERE day = ? AND user_id = ?
        """,
        (index, handicap, day, user_id),
    )
    conn.execute(
        "DELETE FROM daily_card_holes WHERE day = ? AND user_id = ?", (day, user_id)
    )
    conn.executemany(
        """
        INSERT INTO daily_card_holes (day, user_id, position, received, net, points)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (day, user_id, position, r, n, p)
            for position, (r, n, p) in enumerate(
                zip(received, nets, points, strict=True), start=1
            )
        ],
    )
    return True


def fill_days(db: Database, rules: DailyRules) -> list[str]:
    """Give each day opened without rules these, once its seed is rated, and fill its
    cards: the days made before handicapped dailies, or while no ratings run existed.
    Returns the days filled."""
    filled = []
    with db.transaction() as conn:
        days = conn.execute(
            """
            SELECT dailies.day, daily_seeds.seed_id
            FROM dailies JOIN daily_seeds ON daily_seeds.day = dailies.day
            WHERE dailies.rules IS NULL AND daily_seeds.replaced_at IS NULL
            ORDER BY dailies.day
            """
        ).fetchall()
        for row in days:
            if seed_rating_on(conn, row["seed_id"]) is None:
                continue
            conn.execute(
                "UPDATE dailies SET rules = ? WHERE day = ?",
                (json.dumps(rules.to_json(), sort_keys=True), row["day"]),
            )
            for card in conn.execute(
                "SELECT user_id FROM daily_cards WHERE day = ? ORDER BY created_at",
                (row["day"],),
            ).fetchall():
                fill_card(conn, row["day"], card["user_id"], rules)
            score_day(conn, row["day"])
            filled.append(row["day"])
    if filled:
        log.info("gave rules to the dailies %s and filled their cards", filled)
    return filled


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


# -- Results ---------------------------------------------------------------------------------

#: the places the home page shows for the previous day
PODIUM = 3


@dataclass(frozen=True)
class StandingLine:
    """One player's line in a day's standing."""

    user_id: int
    name: str
    round_public_id: str
    points: int
    position: int
    #: whether another player shares the position after countback
    shared: bool
    skins: int
    won: bool


@dataclass(frozen=True)
class MatchLine:
    """A match, seen from one player's side."""

    opponent_id: int
    opponent_name: str
    opponent_round: str
    holes_for: int
    holes_against: int
    margin: int

    @property
    def result(self) -> str:
        if self.holes_for == self.holes_against:
            return "half"
        return "win" if self.holes_for > self.holes_against else "loss"


@dataclass(frozen=True)
class SkinHole:
    number: int
    #: None when nobody won the hole outright
    winner_id: int | None
    winner_name: str | None
    #: skins it was worth to its winner, 1 plus any carried to it
    value: int


@dataclass(frozen=True)
class DayResults:
    day: str
    standing: tuple[StandingLine, ...]
    #: each player's matches, their best result first
    matches: dict[int, tuple[MatchLine, ...]]
    #: each hole's skins; empty when the field is too small to win any
    skins: tuple[SkinHole, ...]

    @property
    def podium(self) -> tuple[StandingLine, ...]:
        """The standing's lines in the top places, more of them when a place is shared."""
        return tuple(line for line in self.standing if line.position <= PODIUM)


_STANDING_SQL = """
SELECT res.user_id, coalesce(u.global_name, u.username) AS name, c.round_public_id,
       res.points, res.position, res.skins, res.won
FROM daily_results res
JOIN users u ON u.id = res.user_id
JOIN daily_cards c ON c.day = res.day AND c.user_id = res.user_id
WHERE res.day = ?
ORDER BY res.position, name COLLATE NOCASE
"""


def load_day_results(db: Database, day: str) -> DayResults | None:
    """A day's results with its players' names, or None when it has none."""
    with db.transaction() as conn:
        rows = conn.execute(_STANDING_SQL, (day,)).fetchall()
        if not rows:
            return None
        names = {r["user_id"]: r["name"] for r in rows}
        rounds = {r["user_id"]: r["round_public_id"] for r in rows}
        nets: dict[int, list[int]] = {}
        for h in conn.execute(
            """
            SELECT user_id, net FROM daily_card_holes
            WHERE day = ? ORDER BY user_id, position
            """,
            (day,),
        ):
            if h["user_id"] in names:
                nets.setdefault(h["user_id"], []).append(h["net"])
        matches: dict[int, list[MatchLine]] = {user: [] for user in names}
        for m in conn.execute("SELECT * FROM daily_matches WHERE day = ?", (day,)):
            a, b = m["user_a"], m["user_b"]
            matches[a].append(
                MatchLine(
                    b, names[b], rounds[b], m["holes_a"], m["holes_b"], m["margin"]
                )
            )
            matches[b].append(
                MatchLine(
                    a, names[a], rounds[a], m["holes_b"], m["holes_a"], m["margin"]
                )
            )
        rules = _day_rules(conn, day)
    positions = [r["position"] for r in rows]
    standing = [
        StandingLine(
            user_id=r["user_id"],
            name=r["name"],
            round_public_id=r["round_public_id"],
            points=r["points"],
            position=r["position"],
            shared=positions.count(r["position"]) > 1,
            skins=r["skins"],
            won=bool(r["won"]),
        )
        for r in rows
    ]
    skins: tuple[SkinHole, ...] = ()
    if rules is not None and len(rows) >= rules.min_field:
        skins = tuple(
            SkinHole(number, winner, None if winner is None else names[winner], value)
            for number, (winner, value) in enumerate(
                skin_holes(nets, rules.skins_carryover), start=1
            )
        )
    order = {line.user_id: i for i, line in enumerate(standing)}
    return DayResults(
        day=day,
        standing=tuple(standing),
        # a player's best results first: biggest win to heaviest loss, then the
        # standing's order among equal results
        matches={
            user: tuple(
                sorted(
                    ms,
                    key=lambda m: (m.holes_against - m.holes_for, order[m.opponent_id]),
                )
            )
            for user, ms in matches.items()
        },
        skins=skins,
    )


@dataclass(frozen=True)
class NetCard:
    """A daily round's net card, as its player was shown it."""

    day: str
    #: the index the round was scored with
    index_used: float
    handicap: int
    received: tuple[int, ...]
    nets: tuple[int, ...]
    points: tuple[int, ...]
    #: the seed's stroke index for each hole, which placed the strokes received
    stroke_indexes: tuple[int, ...]
    #: the day's standing, once it has one: position, whether shared, the field's size
    position: int | None
    shared: bool
    field: int


def load_net_card(db: Database, public_id: str) -> NetCard | None:
    """The filled card of a round, or None for a round without one."""
    with db.transaction() as conn:
        card = conn.execute(
            """
            SELECT c.day, c.user_id, c.index_used, c.handicap
            FROM daily_cards c JOIN dailies d ON d.day = c.day
            WHERE c.round_public_id = ? AND c.handicap IS NOT NULL
              AND d.withdrawn_at IS NULL
            """,
            (public_id,),
        ).fetchone()
        if card is None:
            return None
        holes = conn.execute(
            """
            SELECT received, net, points FROM daily_card_holes
            WHERE day = ? AND user_id = ? ORDER BY position
            """,
            (card["day"], card["user_id"]),
        ).fetchall()
        stroke_indexes = tuple(
            r["stroke_index"]
            for r in conn.execute(
                """
                SELECT h.stroke_index FROM seed_hole_ratings h
                JOIN entries e ON e.seed_id = h.seed_id
                JOIN rounds r ON r.entry_id = e.id
                WHERE r.public_id = ? ORDER BY h.position
                """,
                (public_id,),
            )
        )
        placed = conn.execute(
            "SELECT position FROM daily_results WHERE day = ? AND user_id = ?",
            (card["day"], card["user_id"]),
        ).fetchone()
        positions = [
            r["position"]
            for r in conn.execute(
                "SELECT position FROM daily_results WHERE day = ?", (card["day"],)
            )
        ]
    position = None if placed is None else placed["position"]
    return NetCard(
        day=card["day"],
        index_used=card["index_used"],
        handicap=card["handicap"],
        received=tuple(h["received"] for h in holes),
        nets=tuple(h["net"] for h in holes),
        points=tuple(h["points"] for h in holes),
        stroke_indexes=stroke_indexes,
        position=position,
        shared=position is not None and positions.count(position) > 1,
        field=len(positions),
    )


# -- Making a day -----------------------------------------------------------------------------


def ensure_daily(
    db: Database,
    builder: SeedBuilder,
    schedule: Schedule,
    secret: str,
    day: date,
    now: str | None = None,
    rater: SeedRater | None = None,
    rules: DailyRules | None = None,
) -> Daily:
    """The day's daily, generated from the schedule, built and stored if it is not yet,
    rated by `rater` when it has a run, and scored by `rules` when it was. A day stored
    without rules has its cards filled once `fill_days` gives it some.

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
        db,
        manifest,
        built.unfinished_ips,
        built.holes,
        builder.catalog,
        now=created_at,
        rater=rater,
    )
    try:
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO dailies (
                    day, theme_name, theme_description, request,
                    opens_at, closes_at, created_at, rules
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    day.isoformat(),
                    plan.theme.name,
                    plan.theme.description,
                    json.dumps(plan.request, sort_keys=True),
                    utc_text(opens_at(day)),
                    utc_text(closes_at(day)),
                    created_at,
                    # a day takes its rules once its seed is rated, which `fill_days`
                    # waits for when no ratings run existed to rate it now
                    None
                    if rules is None or seed_rating_on(conn, seed_id) is None
                    else json.dumps(rules.to_json(), sort_keys=True),
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
    ratings: Ratings | None = None,
) -> Daily:
    """Make sure the daily open at `now` exists. With `ratings`, first refit the hole
    model for the day if it has no run, so the daily is rated by its own day's run, and
    give any day opened without rules the current ones; a failed refit is logged, and
    the latest run stays in force. The day is stored with the current rules."""
    day = daily_date(now)
    at = utc_text(now)
    rules = None
    if ratings is not None:
        rules = DailyRules.from_parameters(ratings.parameters)
        try:
            refit(db, ratings, day, at)
        except Exception:
            log.exception("the ratings refit for %s failed", day)
        fill_days(db, rules)
    return ensure_daily(
        db, builder, schedule, secret, day, at, rater=ratings, rules=rules
    )


async def roll_over_periodically(
    db: Database,
    builder: SeedBuilder,
    schedule: Schedule,
    secret: str,
    clock: Callable[[], datetime],
    seconds: float = ROLLOVER_SECONDS,
    ratings: Ratings | None = None,
) -> None:
    """The app's rollover: look now, then every `seconds`, until canceled. A failure is
    logged and tried again at the next look."""
    while True:
        try:
            await run_in_threadpool(
                roll_over, db, builder, schedule, secret, clock(), ratings
            )
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
    rater: SeedRater | None = None,
) -> Daily:
    """Replace a day's seed with its rerun, withdrawing the replaced seed's downloads,
    and log it. The rerun is generated from the theme and request the day stored, and
    rated by `rater` when it has a run.

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
        db,
        manifest,
        built.unfinished_ips,
        built.holes,
        builder.catalog,
        now=at,
        rater=rater,
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
        score_day(conn, day.isoformat())
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
        score_day(conn, day.isoformat())
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
