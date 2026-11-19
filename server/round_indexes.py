"""
Round indexes (docs/competitive/data_model.md, Round indexes; ADR 0034): the only code that
writes `round_indexes` and `index_rules`.

- **At scan** every player's own round (slot 0) stores the index its player holds, on any
  seed (`record_index`). A round scanned into a competition that counts, today a daily,
  also stores its step: adjusted gross, differential, cut, and the index and low mark
  after it, under the site's index rules (`record_step`). Neither is computed again.
- **A player's index** is read from the stored differentials and cuts of their counted
  rounds still recorded, on days not withdrawn (`index_on`). A void, a restore or a
  withdrawn day changes which rows that read takes in, and rewrites none.
- **The backfill** gives every round counted before this table existed its row, once, from
  a replay of the player's counted rounds, checked against what their cards show
  (`backfill`).
"""

import hashlib
import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from golf.ratings.backtest import played_at
from golf.ratings.handicap import daily_handicap
from golf.ratings.index import (
    IndexState,
    RoundStep,
    ScoredRound,
    StoredRound,
    compute_steps,
    replay,
    score,
)
from golf.ratings.params import IndexRules
from golf.ratings.rules import DailyRules
from golf.ratings.seed import HoleRating, SeedRating

from .db import Database

log = logging.getLogger(__name__)


class BackfillMismatchError(RuntimeError):
    """The replay that fills the rows disagrees with what a card shows."""


# -- Reading a player's index ------------------------------------------------------------------

_COUNTED_SQL = """
SELECT ri.user_id, ri.round_public_id, ri.received_at, ri.differential, ri.cut,
       ri.index_before, ri.adjusted_gross, ri.index_after, ri.low_mark
FROM round_indexes ri
JOIN rounds r ON r.public_id = ri.round_public_id
JOIN daily_cards c ON c.round_public_id = ri.round_public_id
JOIN dailies d ON d.day = c.day
WHERE ri.user_id IN ({users}) AND ri.differential IS NOT NULL
  AND d.withdrawn_at IS NULL
ORDER BY ri.user_id, ri.received_at, ri.rowid
"""


@dataclass(frozen=True)
class CountedRow:
    """A counted round's stored row."""

    public_id: str
    received_at: str
    stored: StoredRound
    index_before: float
    adjusted_gross: int
    index_after: float
    low_mark: float | None


def counted_on(
    conn: sqlite3.Connection, user_ids: Sequence[int]
) -> dict[int, list[CountedRow]]:
    """Each player's counted rounds still recorded, on days not withdrawn, in the order
    received; every player asked for has a list."""
    out: dict[int, list[CountedRow]] = {user_id: [] for user_id in user_ids}
    if not out:
        return out
    ids = list(out)
    for row in conn.execute(_COUNTED_SQL.format(users=", ".join("?" * len(ids))), ids):
        out[row["user_id"]].append(
            CountedRow(
                public_id=row["round_public_id"],
                received_at=row["received_at"],
                stored=StoredRound(
                    played_at(row["received_at"]), row["differential"], row["cut"]
                ),
                index_before=row["index_before"],
                adjusted_gross=row["adjusted_gross"],
                index_after=row["index_after"],
                low_mark=row["low_mark"],
            )
        )
    return out


def indexes_on(
    conn: sqlite3.Connection, user_ids: Sequence[int], rules: IndexRules
) -> dict[int, IndexState]:
    """Each player's index now, read from their stored rows under these rules."""
    return {
        user_id: replay([c.stored for c in rows], rules)[0]
        for user_id, rows in counted_on(conn, user_ids).items()
    }


def index_on(conn: sqlite3.Connection, user_id: int, rules: IndexRules) -> IndexState:
    return indexes_on(conn, [user_id], rules)[user_id]


def player_index(db: Database, user_id: int, rules: IndexRules) -> IndexState:
    """The player's handicap index now; the starting index before any counted round."""
    with db.transaction() as conn:
        return index_on(conn, user_id, rules)


def player_indexes(
    db: Database, user_ids: Sequence[int], rules: IndexRules
) -> dict[int, IndexState]:
    """`player_index` for several players in one read."""
    with db.transaction() as conn:
        return indexes_on(conn, user_ids, rules)


# -- A seed a player has played ------------------------------------------------------------------


@dataclass(frozen=True)
class SeedPlay:
    """A player's own round on a seed, as far as their handicap on it goes."""

    public_id: str
    #: the index they held when it was scanned; None for a round from before rounds
    #: stored one
    index_before: float | None
    #: the handicap their filled daily card was scored with, on a day not withdrawn
    card_handicap: int | None


def seed_play(db: Database, seed_id: str, user_id: int) -> SeedPlay | None:
    """The player's own recorded round on the seed, or None when they have none."""
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT r.public_id, ri.index_before,
                   (SELECT c.handicap FROM daily_cards c JOIN dailies d ON d.day = c.day
                    WHERE c.round_public_id = r.public_id AND d.withdrawn_at IS NULL)
                       AS card_handicap
            FROM rounds r
            JOIN entries e ON e.id = r.entry_id
            LEFT JOIN round_indexes ri ON ri.round_public_id = r.public_id
            WHERE e.seed_id = ? AND e.user_id = ? AND r.slot = 0
            """,
            (seed_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return SeedPlay(row["public_id"], row["index_before"], row["card_handicap"])


# -- Writing, inside a scan's transaction --------------------------------------------------------


def rules_hash(rules: IndexRules) -> str:
    return hashlib.sha256(_rules_text(rules).encode()).hexdigest()


def _rules_text(rules: IndexRules) -> str:
    return json.dumps(asdict(rules), sort_keys=True)


def _rules_version(conn: sqlite3.Connection, rules: IndexRules, at: str) -> str:
    """The rules' hash, storing them the first time they are used."""
    digest = rules_hash(rules)
    conn.execute(
        """
        INSERT INTO index_rules (hash, rules, first_used_at) VALUES (?, ?, ?)
        ON CONFLICT (hash) DO NOTHING
        """,
        (digest, _rules_text(rules), at),
    )
    return digest


def record_index(
    conn: sqlite3.Connection,
    public_id: str,
    user_id: int,
    received_at: str,
    rules: IndexRules,
) -> IndexState:
    """Store the index a player's own round was scanned at; returns their index state,
    which `record_step` scores the round from when it counts."""
    state = index_on(conn, user_id, rules)
    conn.execute(
        """
        INSERT INTO round_indexes (round_public_id, user_id, received_at, index_before)
        VALUES (?, ?, ?, ?)
        """,
        (public_id, user_id, received_at, state.index),
    )
    return state


def record_step(
    conn: sqlite3.Connection,
    public_id: str,
    state: IndexState,
    rating: SeedRating,
    rules: IndexRules,
) -> RoundStep:
    """Score a counted round from the index state it was scanned at, and store its step."""
    row = conn.execute(
        "SELECT received_at FROM round_indexes WHERE round_public_id = ?",
        (public_id,),
    ).fetchone()
    strokes = tuple(
        r["strokes"]
        for r in conn.execute(
            """
            SELECT h.strokes FROM round_holes h JOIN rounds r ON r.id = h.round_id
            WHERE r.public_id = ? ORDER BY h.position
            """,
            (public_id,),
        )
    )
    _, step = score(
        state, ScoredRound(played_at(row["received_at"]), rating, strokes), rules
    )
    _store_step(conn, public_id, step, _rules_version(conn, rules, row["received_at"]))
    return step


def _store_step(
    conn: sqlite3.Connection, public_id: str, step: RoundStep, version: str
) -> None:
    conn.execute(
        """
        UPDATE round_indexes
        SET rules_hash = ?, adjusted_gross = ?, differential = ?, cut = ?,
            index_after = ?, low_mark = ?
        WHERE round_public_id = ?
        """,
        (
            version,
            step.adjusted_gross,
            step.differential,
            step.cut,
            step.index_after,
            step.low_mark,
            public_id,
        ),
    )


def stored_index(conn: sqlite3.Connection, public_id: str) -> float | None:
    """The index a round was scanned at, or None for a round that stored none."""
    row = conn.execute(
        "SELECT index_before FROM round_indexes WHERE round_public_id = ?",
        (public_id,),
    ).fetchone()
    return None if row is None else row["index_before"]


# -- The backfill ----------------------------------------------------------------------------

# A round counted before round indexes: its card on a day not withdrawn, its round still
# recorded and its seed rated.
_REPLAY_SQL = """
SELECT c.user_id, c.day, c.index_used, c.handicap, d.rules, r.id, r.public_id,
       r.received_at, e.seed_id
FROM daily_cards c
JOIN dailies d ON d.day = c.day
JOIN rounds r ON r.public_id = c.round_public_id
JOIN entries e ON e.id = r.entry_id
WHERE d.withdrawn_at IS NULL
  AND e.seed_id IN (SELECT seed_id FROM seed_ratings)
ORDER BY c.user_id, r.received_at, r.id
"""


def _seed_ratings(
    conn: sqlite3.Connection, seed_ids: set[str]
) -> dict[str, SeedRating]:
    holes: dict[str, list[HoleRating]] = {}
    for row in conn.execute(
        f"""
        SELECT r.seed_id, h.par, r.rating, r.spread, r.stroke_index
        FROM seed_hole_ratings r
        JOIN seed_holes h ON h.seed_id = r.seed_id AND h.position = r.position
        WHERE r.seed_id IN ({", ".join("?" * len(seed_ids))})
        ORDER BY r.seed_id, r.position
        """,
        sorted(seed_ids),
    ):
        holes.setdefault(row["seed_id"], []).append(
            HoleRating(row["par"], row["rating"], row["spread"], row["stroke_index"])
        )
    return {seed: SeedRating(tuple(h)) for seed, h in holes.items()}


def backfill(db: Database, rules: IndexRules) -> int:
    """Give every round counted before round indexes existed its row, once, from a
    replay of each player's counted rounds under these rules. Runs only while the table
    is empty. Raises BackfillMismatchError, writing nothing, when the replay gives a
    card's round another index or handicap than the card shows. Returns the rows
    written."""
    with db.transaction() as conn:
        if conn.execute("SELECT 1 FROM round_indexes LIMIT 1").fetchone():
            return 0
        rows = conn.execute(_REPLAY_SQL).fetchall()
        if not rows:
            return 0
        ratings = _seed_ratings(conn, {row["seed_id"] for row in rows})
        strokes: dict[int, list[int]] = {}
        for hole in conn.execute(
            f"""
            SELECT round_id, strokes FROM round_holes
            WHERE round_id IN ({", ".join("?" * len(rows))})
            ORDER BY round_id, position
            """,
            [row["id"] for row in rows],
        ):
            strokes.setdefault(hole["round_id"], []).append(hole["strokes"])
        by_user: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            by_user.setdefault(row["user_id"], []).append(row)
        version = _rules_version(conn, rules, rows[0]["received_at"])
        written = 0
        for user_id, cards in by_user.items():
            scored = [
                ScoredRound(
                    played_at(card["received_at"]),
                    ratings[card["seed_id"]],
                    tuple(strokes[card["id"]]),
                )
                for card in cards
            ]
            _, steps = compute_steps(scored, rules)
            for card, step, played in zip(cards, steps, scored, strict=True):
                _check(card, step, played.rating)
                conn.execute(
                    """
                    INSERT INTO round_indexes (round_public_id, user_id, received_at,
                                               index_before)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        card["public_id"],
                        user_id,
                        card["received_at"],
                        step.index_before,
                    ),
                )
                _store_step(conn, card["public_id"], step, version)
                written += 1
    log.info("filled %d round indexes from the counted rounds' replay", written)
    return written


def _check(card: sqlite3.Row, step: RoundStep, rating: SeedRating) -> None:
    """A filled card must show what the replay gives its round."""
    if card["handicap"] is None:
        return
    shown = card["index_used"]
    handicap = (
        None
        if card["rules"] is None
        else daily_handicap(
            step.index_before,
            rating,
            DailyRules.from_json(json.loads(card["rules"])).allowance,
        )
    )
    if round(shown, 6) != round(step.index_before, 6) or (
        handicap is not None and handicap != card["handicap"]
    ):
        raise BackfillMismatchError(
            f"the card of round {card['public_id']} ({card['day']}) shows index "
            f"{shown} and handicap {card['handicap']}; the replay gives "
            f"{step.index_before} and {handicap}"
        )
