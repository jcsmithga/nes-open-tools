"""
Ratings on the site (docs/competitive/data_model.md, Ratings runs; ADR 0034): the daily
refit of the hole model, each seed's stored ratings, and a player's handicap index.

This is the only code that writes `ratings_runs`, `ratings_effects`, `seed_ratings` and
`seed_hole_ratings`. The model is `golf/ratings/`.

- **A run** is the hole model fitted for one rollover day, from every recorded round a
  player played on their own entry (slot 0), on any seed. It keeps the model's parts, so
  a new seed is rated exactly as the fit would rate it. Below `MIN_ROUNDS` no run is
  written.
- **A seed is rated once**, under the latest run: when it is stored (`Ratings.rate_on`,
  in `insert_issued_seed`'s transaction), or, when no run existed then, by the next
  refit, which rates every seed that is not. The first refit after the launch's deploy
  rates the whole site.

A player's index is stored with each round, in `server/round_indexes.py`.
"""

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date

from golf.randomizer.catalog import Catalog
from golf.ratings import sitedb
from golf.ratings.model import Model, fit
from golf.ratings.params import Parameters
from golf.ratings.reference import reference_slots
from golf.ratings.run import MODEL_VERSION, StoredModel, rebuilt, stored
from golf.ratings.seed import HoleRating, SeedRating, rate_seed

from .db import Database
from .seeds import utc_now

log = logging.getLogger(__name__)

#: a refit with fewer rounds than this writes no run, as the backtest's first fit
MIN_ROUNDS = 20

_SEED_SLOTS_SQL = """
SELECT hole_id, transforms, data_hash, pin_index, par, distance, wind_speed
FROM seed_holes WHERE seed_id = ? ORDER BY position
"""


@dataclass(frozen=True)
class Run:
    id: int
    day: str
    model: Model


class Ratings:
    """The latest run, rebuilt into the model it was fitted as, which rates each new
    seed. One lives on `app.state.ratings`; a refit replaces its run."""

    def __init__(self, catalog: Catalog, parameters: Parameters):
        self.catalog = catalog
        self.parameters = parameters
        self.latest: Run | None = None

    def load_latest(self, db: Database) -> None:
        """Take the latest stored run, if any."""
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT id FROM ratings_runs ORDER BY day DESC LIMIT 1"
            ).fetchone()
            self.latest = None if row is None else _load_run(conn, row["id"])

    def rate_on(self, conn: sqlite3.Connection, seed_id: str, now: str) -> bool:
        """Rate a stored seed under the latest run, in the caller's transaction. False,
        writing nothing, when there is no run yet or the seed is already rated."""
        run = self.latest
        if run is None:
            return False
        if conn.execute(
            "SELECT 1 FROM seed_ratings WHERE seed_id = ?", (seed_id,)
        ).fetchone():
            return False
        slots = [
            sitedb.slot_from(self.catalog, *row)
            for row in conn.execute(_SEED_SLOTS_SQL, (seed_id,))
        ]
        if len(slots) != 18:
            log.warning("seed %s has %d holes; not rated", seed_id, len(slots))
            return False
        rating = rate_seed(run.model, slots)
        conn.execute(
            """
            INSERT INTO seed_ratings (seed_id, run_id, rated_at, par, course_rating, slope)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (seed_id, run.id, now, rating.par, rating.course_rating, rating.slope),
        )
        conn.executemany(
            """
            INSERT INTO seed_hole_ratings (seed_id, position, rating, spread, stroke_index)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (seed_id, position, h.rating, h.spread, h.stroke_index)
                for position, h in enumerate(rating.holes, start=1)
            ],
        )
        return True


def _load_run(conn: sqlite3.Connection, run_id: int) -> Run:
    row = conn.execute(
        "SELECT id, day, parameters, model FROM ratings_runs WHERE id = ?", (run_id,)
    ).fetchone()
    effects = [
        (r["grp"], r["level"], r["value"])
        for r in conn.execute(
            "SELECT grp, level, value FROM ratings_effects WHERE run_id = ?", (run_id,)
        )
    ]
    params = Parameters.from_json(json.loads(row["parameters"])).model
    kept = StoredModel.from_parts(json.loads(row["model"]), effects)
    return Run(row["id"], row["day"], rebuilt(kept, params))


def has_run(db: Database, day: date) -> bool:
    with db.transaction() as conn:
        return (
            conn.execute(
                "SELECT 1 FROM ratings_runs WHERE day = ?", (day.isoformat(),)
            ).fetchone()
            is not None
        )


def refit(
    db: Database,
    ratings: Ratings,
    day: date,
    now: str | None = None,
    min_rounds: int = MIN_ROUNDS,
) -> int | None:
    """Fit the day's run if it has none, then rate every seed not yet rated. Returns the
    new run's id, or None when the day has a run or there are too few rounds.

    The fit runs outside the database lock; run it in the threadpool.
    """
    created_at = now if now is not None else utc_now()
    if has_run(db, day):
        return None
    with db.transaction() as conn:
        data = sitedb.read(conn, ratings.catalog)
    if len(data.rounds) < min_rounds:
        log.info(
            "%d rounds, fewer than %d: no ratings run for %s",
            len(data.rounds),
            min_rounds,
            day,
        )
        return None
    params = ratings.parameters
    model = fit(
        sitedb.observations(data.rounds), params.model, reference_slots(ratings.catalog)
    )
    kept = stored(model)
    with db.transaction() as conn:
        cursor = conn.execute(
            """
            INSERT INTO ratings_runs (day, created_at, model_version, parameters_hash,
                                      parameters, model, latest_received_at, n_rounds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                day.isoformat(),
                created_at,
                MODEL_VERSION,
                params.digest(),
                json.dumps(params.to_json(), sort_keys=True),
                json.dumps(kept.header(), sort_keys=True),
                max(r.received_at for r in data.rounds),
                len(data.rounds),
            ),
        )
        run_id = cursor.lastrowid
        assert run_id is not None
        conn.executemany(
            "INSERT INTO ratings_effects (run_id, grp, level, value) VALUES (?, ?, ?, ?)",
            [(run_id, *row) for row in kept.effect_rows()],
        )
        ratings.latest = Run(run_id, day.isoformat(), model)
        rated = rate_unrated(conn, ratings, created_at)
    log.info(
        "ratings run %d for %s: %d rounds, %d hole scores, %d seeds rated",
        run_id,
        day,
        len(data.rounds),
        model.n_scores,
        rated,
    )
    return run_id


def rate_unrated(conn: sqlite3.Connection, ratings: Ratings, now: str) -> int:
    """Rate every stored seed without ratings under the latest run; how many were."""
    unrated = [
        row["id"]
        for row in conn.execute(
            """
            SELECT id FROM seeds WHERE id NOT IN (SELECT seed_id FROM seed_ratings)
            ORDER BY created_at, id
            """
        )
    ]
    return sum(ratings.rate_on(conn, seed_id, now) for seed_id in unrated)


# -- Reading -----------------------------------------------------------------------------------


def seed_rating_on(conn: sqlite3.Connection, seed_id: str) -> SeedRating | None:
    """A seed's stored ratings, in the caller's transaction; None while it is unrated."""
    rows = conn.execute(
        """
        SELECT h.par, r.rating, r.spread, r.stroke_index
        FROM seed_hole_ratings r
        JOIN seed_holes h ON h.seed_id = r.seed_id AND h.position = r.position
        WHERE r.seed_id = ? ORDER BY r.position
        """,
        (seed_id,),
    ).fetchall()
    if not rows:
        return None
    return SeedRating(tuple(HoleRating(*row) for row in rows))


def seed_rating(db: Database, seed_id: str) -> SeedRating | None:
    """A seed's stored ratings, or None while it is unrated."""
    with db.transaction() as conn:
        return seed_rating_on(conn, seed_id)
