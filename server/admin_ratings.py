"""What the admin pages show of the ratings (docs/competitive/data_model.md): the runs the
daily refit wrote, how each moved the hole effects, the counts that say the pipeline is
keeping up, a seed's stored ratings, and a player's index round by round.

Reads only, as `server/admin.py` is, kept apart from it by area. A player's index is
shown round by round as each round stored it at scan (`server/round_indexes.py`), beside
what reading the stored rows under the current index rules gives now; a difference
between the two, after an earlier round's void or a change of rules, is shown, not
resolved.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from golf.randomizer.catalog import Catalog
from golf.randomizer.daily import daily_date
from golf.ratings.index import (
    IndexState,
    counting,
    replay,
    start_weight,
)
from golf.ratings.params import Parameters
from golf.ratings.seed import HoleRating

from . import round_indexes
from .db import Database
from .round_indexes import counted_on

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
    #: what the parameters file was estimated on (its `provenance`): the rounds, the
    #: date, and the slot-0 rounds received since its latest round. None when the file
    #: does not say
    estimated_rounds: int | None = None
    estimated_on: str | None = None
    rounds_since: int | None = None

    @property
    def run_current(self) -> bool:
        return self.latest_run_day == self.today

    @property
    def estimate_doubled(self) -> bool:
        """Whether the rounds since the estimate match the rounds it used: the data has
        doubled, the cue to re-estimate (docs/competitive/design.md, Fitting)."""
        return (
            self.estimated_rounds is not None
            and self.rounds_since is not None
            and self.rounds_since >= self.estimated_rounds
        )


def ratings_health(
    db: Database, now: datetime, rollover: bool, parameters: Parameters
) -> RatingsHealth:
    provenance = parameters.provenance
    latest_round = provenance.get("latest_round")
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
                    WHERE c.handicap IS NULL AND d.withdrawn_at IS NULL) AS unfilled_cards,
                   (SELECT max(day) FROM ratings_runs) AS latest_run_day
            """
        ).fetchone()
        # the rounds a fit uses: every player's own round (slot 0), as `sitedb.read`
        rounds_since = (
            None
            if latest_round is None
            else conn.execute(
                "SELECT count(*) FROM rounds WHERE slot = 0 AND received_at > ?",
                (latest_round,),
            ).fetchone()[0]
        )
    return RatingsHealth(
        unrated_seeds=row["unrated_seeds"],
        dailies_without_rules=row["dailies_without_rules"],
        unfilled_cards=row["unfilled_cards"],
        latest_run_day=row["latest_run_day"],
        today=daily_date(now).isoformat(),
        rollover=rollover,
        estimated_rounds=provenance.get("rounds"),
        estimated_on=provenance.get("estimated"),
        rounds_since=rounds_since,
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
    return round_indexes.player_indexes(db, user_ids, parameters.index)


@dataclass(frozen=True)
class IndexRow:
    day: str
    public_id: str
    gross: int
    adjusted_gross: int
    course_rating: float
    #: on the model's scale
    slope: float
    #: the card's handicap; None on a card not filled
    handicap: int | None
    #: as the round stored them at scan
    index_before: float
    differential: float
    cut: float
    low_mark: float | None
    index_after: float
    #: what the stored rows give now under the current rules: the differential after any
    #: later cut, and the index after this round
    differential_now: float
    index_after_now: float
    counts: bool

    @property
    def mismatch(self) -> bool:
        """Whether the stored index after this round and a read of the rows now disagree
        as the page shows them."""
        return round(self.index_after, 1) != round(self.index_after_now, 1)


@dataclass(frozen=True)
class UncountedCard:
    day: str
    public_id: str
    #: `withdrawn` (the day), `voided` (the round) or `unrated` (the seed was unrated at
    #: scan, so the round stored no step)
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
        counted = counted_on(conn, [user_id])[user_id]
        details = {
            row["public_id"]: row
            for row in conn.execute(
                """
                SELECT r.public_id, c.day, c.handicap, r.total_strokes,
                       s.course_rating, s.slope
                FROM daily_cards c
                JOIN rounds r ON r.public_id = c.round_public_id
                JOIN entries e ON e.id = r.entry_id
                JOIN seed_ratings s ON s.seed_id = e.seed_id
                WHERE c.user_id = ?
                """,
                (user_id,),
            )
        }
        uncounted_rows = conn.execute(
            """
            SELECT c.day, c.round_public_id, d.withdrawn_at IS NOT NULL AS withdrawn,
                   r.id IS NULL AS voided
            FROM daily_cards c
            JOIN dailies d ON d.day = c.day
            LEFT JOIN rounds r ON r.public_id = c.round_public_id
            WHERE c.user_id = ?
            ORDER BY c.day DESC
            """,
            (user_id,),
        ).fetchall()
    state, states = replay([c.stored for c in counted], rules)
    counts = set(counting(state.differentials, rules))
    rows = []
    for i, (played, now) in enumerate(zip(counted, states, strict=True)):
        detail = details[played.public_id]
        rows.append(
            IndexRow(
                day=detail["day"],
                public_id=played.public_id,
                gross=detail["total_strokes"],
                adjusted_gross=played.adjusted_gross,
                course_rating=detail["course_rating"],
                slope=detail["slope"],
                handicap=detail["handicap"],
                index_before=played.index_before,
                differential=played.stored.differential,
                cut=played.stored.cut,
                low_mark=played.low_mark,
                index_after=played.index_after,
                differential_now=state.differentials[i],
                index_after_now=now.index,
                counts=i in counts,
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
