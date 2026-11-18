"""What the admin pages show of the ratings (docs/competitive/data_model.md): the runs the
daily refit wrote, how each moved the hole effects, the counts that say the pipeline is
keeping up, a seed's stored ratings, and a player's index round by round.

Reads only, as `server/admin.py` is, kept apart from it by area. A player's index is
recomputed here with the same `counted_rounds_for` and `compute_steps` that fill a card,
under the current index rules, beside what each card stored when it was filled; a
difference between the two is shown, not resolved.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from golf.randomizer.catalog import Catalog
from golf.randomizer.daily import daily_date
from golf.ratings.index import (
    IndexState,
    compute,
    compute_steps,
    counting,
    start_weight,
)
from golf.ratings.params import Parameters
from golf.ratings.seed import HoleRating

from .db import Database
from .ratings import counted_rounds, counted_rounds_for

#: days of runs the ratings page lists, as the dailies page does
RATINGS_DAYS = 60

#: effects per group a run's page compares with the run before
MOVERS = 15

#: counted rounds a player's admin page lists, newest first: 10 cover the index's
#: window, 20 the low mark's minimum
INDEX_ROWS = 20


# -- Health ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RatingsHealth:
    unrated_seeds: int
    #: days not withdrawn that have no rules, so no card on them is filled
    dailies_without_rules: int
    #: cards with a recorded round on a day not withdrawn that have not been filled
    unfilled_cards: int
    latest_run_day: str | None
    #: the open daily's date, which the latest run should be for
    today: str
    #: whether this site runs the rollover that refits
    rollover: bool

    @property
    def run_current(self) -> bool:
        return self.latest_run_day == self.today


def ratings_health(db: Database, now: datetime, rollover: bool) -> RatingsHealth:
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT (SELECT count(*) FROM seeds
                    WHERE id NOT IN (SELECT seed_id FROM seed_ratings)) AS unrated_seeds,
                   (SELECT count(*) FROM dailies
                    WHERE rules IS NULL AND withdrawn_at IS NULL) AS dailies_without_rules,
                   (SELECT count(*) FROM daily_cards c
                    JOIN dailies d ON d.day = c.day
                    JOIN rounds r ON r.public_id = c.round_public_id
                    WHERE c.index_before IS NULL AND d.withdrawn_at IS NULL) AS unfilled_cards,
                   (SELECT max(day) FROM ratings_runs) AS latest_run_day
            """
        ).fetchone()
    return RatingsHealth(
        unrated_seeds=row["unrated_seeds"],
        dailies_without_rules=row["dailies_without_rules"],
        unfilled_cards=row["unfilled_cards"],
        latest_run_day=row["latest_run_day"],
        today=daily_date(now).isoformat(),
        rollover=rollover,
    )


# -- Runs -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RunListing:
    id: int
    day: str
    created_at: str
    n_rounds: int
    n_scores: int
    latest_received_at: str
    model_version: int
    parameters_hash: str
    #: whether the parameters differ from the run before's; False for the first run
    parameters_changed: bool
    seeds_rated: int


@dataclass(frozen=True)
class RunRow:
    """A day on the ratings page: its run, or None for a daily that got none."""

    day: str
    run: RunListing | None


_RUN_SELECT = """
    SELECT r.id, r.day, r.created_at, r.n_rounds, r.model, r.latest_received_at,
           r.model_version, r.parameters_hash,
           (SELECT count(*) FROM seed_ratings s WHERE s.run_id = r.id) AS seeds_rated
    FROM ratings_runs r
"""


def _runs(db: Database) -> list[RunListing]:
    """Every run, oldest first."""
    with db.transaction() as conn:
        rows = conn.execute(f"{_RUN_SELECT} ORDER BY r.day").fetchall()
    runs = []
    previous = None
    for row in rows:
        runs.append(
            RunListing(
                id=row["id"],
                day=row["day"],
                created_at=row["created_at"],
                n_rounds=row["n_rounds"],
                n_scores=json.loads(row["model"])["n_scores"],
                latest_received_at=row["latest_received_at"],
                model_version=row["model_version"],
                parameters_hash=row["parameters_hash"],
                parameters_changed=previous is not None
                and previous != row["parameters_hash"],
                seeds_rated=row["seeds_rated"],
            )
        )
        previous = row["parameters_hash"]
    return runs


def ratings_runs_view(db: Database, now: datetime) -> list[RunRow]:
    """The last RATINGS_DAYS days, newest first: each run, and each day from the first
    run on that has a daily but no run."""
    today = daily_date(now)
    first = (today - timedelta(days=RATINGS_DAYS - 1)).isoformat()
    runs = _runs(db)
    by_day = {run.day: run for run in runs if run.day >= first}
    days = set(by_day)
    if runs:
        with db.transaction() as conn:
            days |= {
                row["day"]
                for row in conn.execute(
                    "SELECT day FROM dailies WHERE day BETWEEN ? AND ?",
                    (max(first, runs[0].day), today.isoformat()),
                )
            }
    return [RunRow(day, by_day.get(day)) for day in sorted(days, reverse=True)]


@dataclass(frozen=True)
class EffectChange:
    level: str
    #: the hole a terrain or pin level is, as `hole_labels` names it; None for a layout
    #: level, which is a hole id already, or a level no stored seed holds
    label: str | None
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass(frozen=True)
class NewEffect:
    level: str
    label: str | None
    value: float


@dataclass(frozen=True)
class GroupChanges:
    group: str
    #: the MOVERS levels that moved most since the run before, largest first
    movers: list[EffectChange]
    #: the MOVERS largest levels this run fitted that the run before did not
    new: list[NewEffect]
    #: how many levels are new, shown or not
    new_count: int
    levels: int


@dataclass(frozen=True)
class RunDetail:
    run: RunListing
    previous: RunListing | None
    fixed: dict[str, float]
    centers: dict[str, float]
    reference_par: float
    field: float
    sigma: float
    iterations: int
    groups: list[GroupChanges]


def _effects(db: Database, run_id: int) -> dict[str, dict[str, float]]:
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT grp, level, value FROM ratings_effects WHERE run_id = ?", (run_id,)
        ).fetchall()
    out: dict[str, dict[str, float]] = {"layout": {}, "terrain": {}, "pin": {}}
    for row in rows:
        out[row["grp"]][row["level"]] = row["value"]
    return out


def hole_labels(db: Database, catalog: Catalog) -> dict[str, str]:
    """A terrain level's hole: each stored hole's data hash, and the content hash of
    each catalog hole a stored seed drew, which a mirror-only hole is rated as, to its
    hole id, marked `(transformed)` where the hash is not the catalog hole's own."""
    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT DISTINCT hole_id, data_hash FROM seed_holes ORDER BY hole_id"
        ).fetchall()
    labels: dict[str, str] = {}
    for row in rows:
        hole_id = row["hole_id"]
        own = catalog[hole_id].content_hash if hole_id in catalog else None
        if own is not None:
            labels[own] = hole_id
        if row["data_hash"] != own:
            labels.setdefault(row["data_hash"], f"{hole_id} (transformed)")
    return labels


def _label(group: str, level: str, labels: dict[str, str]) -> str | None:
    if group == "terrain":
        return labels.get(level)
    if group == "pin":
        terrain, _, pin = level.rpartition(":")
        hole = labels.get(terrain)
        return None if hole is None else f"{hole}, pin {pin}"
    return None


def run_detail(db: Database, run_id: int, catalog: Catalog) -> RunDetail | None:
    runs = _runs(db)
    at = next((i for i, run in enumerate(runs) if run.id == run_id), None)
    if at is None:
        return None
    run = runs[at]
    previous = runs[at - 1] if at > 0 else None
    with db.transaction() as conn:
        header = json.loads(
            conn.execute(
                "SELECT model FROM ratings_runs WHERE id = ?", (run_id,)
            ).fetchone()["model"]
        )
    now = _effects(db, run_id)
    before = _effects(db, previous.id) if previous is not None else {g: {} for g in now}
    labels = hole_labels(db, catalog)
    groups = []
    for group, levels in now.items():
        old = before[group]
        moved = sorted(
            (
                EffectChange(level, _label(group, level, labels), old[level], value)
                for level, value in levels.items()
                if level in old
            ),
            key=lambda c: (-abs(c.delta), c.level),
        )
        new = sorted(
            (
                NewEffect(level, _label(group, level, labels), value)
                for level, value in levels.items()
                if level not in old
            ),
            key=lambda item: (-abs(item.value), item.level),
        )
        groups.append(
            GroupChanges(group, moved[:MOVERS], new[:MOVERS], len(new), len(levels))
        )
    return RunDetail(
        run=run,
        previous=previous,
        fixed=header["fixed"],
        centers=header["centers"],
        reference_par=header["reference_par"],
        field=header["field"],
        sigma=header["sigma"],
        iterations=header["iterations"],
        groups=groups,
    )


# -- A seed's ratings -----------------------------------------------------------------------


@dataclass(frozen=True)
class SeedRatingDetail:
    run_id: int
    run_day: str
    rated_at: str
    par: int
    course_rating: float
    #: on the model's scale; pages show it on golf's
    slope: float
    #: by position
    holes: tuple[HoleRating, ...]


def seed_rating_detail(db: Database, seed_id: str) -> SeedRatingDetail | None:
    """A seed's stored ratings with the run that made them; None while it is unrated."""
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT s.run_id, r.day AS run_day, s.rated_at, s.par, s.course_rating, s.slope
            FROM seed_ratings s JOIN ratings_runs r ON r.id = s.run_id
            WHERE s.seed_id = ?
            """,
            (seed_id,),
        ).fetchone()
        if row is None:
            return None
        holes = conn.execute(
            """
            SELECT h.par, r.rating, r.spread, r.stroke_index
            FROM seed_hole_ratings r
            JOIN seed_holes h ON h.seed_id = r.seed_id AND h.position = r.position
            WHERE r.seed_id = ? ORDER BY r.position
            """,
            (seed_id,),
        ).fetchall()
    return SeedRatingDetail(
        run_id=row["run_id"],
        run_day=row["run_day"],
        rated_at=row["rated_at"],
        par=row["par"],
        course_rating=row["course_rating"],
        slope=row["slope"],
        holes=tuple(HoleRating(*hole) for hole in holes),
    )


# -- A player's index -----------------------------------------------------------------------


def player_indexes(
    db: Database, user_ids: Sequence[int], parameters: Parameters
) -> dict[int, IndexState]:
    """Each player's index now, as `/me` shows it, from one batched read."""
    with db.transaction() as conn:
        counted = counted_rounds_for(conn, user_ids)
    return {
        user_id: compute([c.round for c in rounds], parameters.index)
        for user_id, rounds in counted.items()
    }


@dataclass(frozen=True)
class IndexRow:
    day: str
    public_id: str
    gross: int
    adjusted_gross: int
    course_rating: float
    #: on the model's scale
    slope: float
    #: what the card stored when it was filled; None on a card not filled
    card_index_before: float | None
    card_handicap: int | None
    card_index_after: float | None
    #: what the current rules give
    index_before: float
    #: the differential as the round was scored, and now, after any later cut
    differential: float
    differential_now: float
    cut: float
    counts: bool
    low_mark: float | None
    index_after: float

    @property
    def mismatch(self) -> bool:
        """Whether the card and the current rules disagree as the page shows them."""
        if self.card_index_before is None or self.card_index_after is None:
            return True
        return round(self.card_index_before, 1) != round(self.index_before, 1) or (
            round(self.card_index_after, 1) != round(self.index_after, 1)
        )


@dataclass(frozen=True)
class UncountedCard:
    day: str
    public_id: str
    #: `withdrawn` (the day), `voided` (the round) or `unrated` (the seed)
    reason: str


@dataclass(frozen=True)
class UserIndex:
    state: IndexState
    #: the starting index's weight left, in rounds
    start_weight: float
    #: counted rounds still needed before a low mark; 0 once one can be set
    rounds_to_low_mark: int
    parameters_hash: str
    #: the last INDEX_ROWS counted rounds, newest first
    rows: list[IndexRow]
    uncounted: list[UncountedCard]


def user_index(db: Database, user_id: int, parameters: Parameters) -> UserIndex:
    rules = parameters.index
    with db.transaction() as conn:
        counted = counted_rounds(conn, user_id)
        cards = {
            row["round_public_id"]: row
            for row in conn.execute(
                """
                SELECT day, round_public_id, index_before, handicap, index_after
                FROM daily_cards WHERE user_id = ?
                """,
                (user_id,),
            )
        }
        uncounted_rows = conn.execute(
            """
            SELECT c.day, c.round_public_id, d.withdrawn_at IS NOT NULL AS withdrawn,
                   r.id IS NULL AS voided,
                   e.seed_id IS NOT NULL
                       AND e.seed_id NOT IN (SELECT seed_id FROM seed_ratings) AS unrated
            FROM daily_cards c
            JOIN dailies d ON d.day = c.day
            LEFT JOIN rounds r ON r.public_id = c.round_public_id
            LEFT JOIN entries e ON e.id = r.entry_id
            WHERE c.user_id = ?
            ORDER BY c.day DESC
            """,
            (user_id,),
        ).fetchall()
    state, steps = compute_steps([c.round for c in counted], rules)
    counts = set(counting(state.differentials, rules))
    rows = []
    for i, (played, step) in enumerate(zip(counted, steps, strict=True)):
        card = cards[played.public_id]
        rows.append(
            IndexRow(
                day=card["day"],
                public_id=played.public_id,
                gross=step.gross,
                adjusted_gross=step.adjusted_gross,
                course_rating=played.round.rating.course_rating,
                slope=played.round.rating.slope,
                card_index_before=card["index_before"],
                card_handicap=card["handicap"],
                card_index_after=card["index_after"],
                index_before=step.index_before,
                differential=step.differential,
                differential_now=state.differentials[i],
                cut=step.cut,
                counts=i in counts,
                low_mark=step.low_mark,
                index_after=step.index_after,
            )
        )
    counted_ids = {c.public_id for c in counted}
    uncounted = [
        UncountedCard(
            row["day"],
            row["round_public_id"],
            "withdrawn"
            if row["withdrawn"]
            else "voided"
            if row["voided"]
            else "unrated",
        )
        for row in uncounted_rows
        if row["round_public_id"] not in counted_ids
    ]
    return UserIndex(
        state=state,
        start_weight=start_weight(rules, state.rounds_counted),
        rounds_to_low_mark=max(0, rules.low_min_rounds - state.rounds_counted),
        parameters_hash=parameters.digest(),
        rows=list(reversed(rows))[:INDEX_ROWS],
        uncounted=uncounted,
    )
