"""
Reading the ratings' inputs from a randomizer site database: every player's own rounds
(slot 0; a round played as player 2 on someone's ROM belongs to nobody the site knows)
with the holes they were played on, and the slots of every seed.

A hole that is mirrored and nothing else is keyed by its unmirrored twin's content hash,
from the catalog, so the two are rated as one hole (docs/competitive/design.md, Rating
holes and courses).
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from golf.randomizer.catalog import Catalog

from .model import Observation, Slot

MIRROR_PREFIX = "mirror@"

_SLOTS_SQL = """
select seed_id, position, hole_id, transforms, data_hash, pin_index, par,
       distance, wind_speed
from seed_holes order by seed_id, position
"""

_ROUNDS_SQL = """
select r.public_id, e.user_id, coalesce(u.global_name, u.username), e.seed_id,
       r.received_at, rh.position, rh.strokes
from rounds r
join entries e on e.id = r.entry_id
join users u on u.id = e.user_id
join round_holes rh on rh.round_id = r.id
where r.slot = 0
order by r.received_at, r.id, rh.position
"""

_DAILY_SQL = """
select c.round_public_id from daily_cards c join dailies d on d.day = c.day
where d.withdrawn_at is null
"""


@dataclass(frozen=True)
class PlayedRound:
    """One player's own round, with the holes it was played on."""

    id: str
    player: str
    name: str
    seed: str
    received_at: str
    slots: tuple[Slot, ...]
    strokes: tuple[int, ...]
    #: whether it is a daily round: it has a card on a day not withdrawn
    daily: bool = False

    @property
    def par(self) -> int:
        return sum(s.par for s in self.slots)

    def observations(self) -> list[Observation]:
        month = self.received_at[:7]
        return [
            Observation(self.player, month, slot, strokes)
            for slot, strokes in zip(self.slots, self.strokes, strict=True)
        ]


def observations(rounds: Sequence[PlayedRound]) -> list[Observation]:
    return [o for r in rounds for o in r.observations()]


def terrain_key(
    transforms: Sequence[str], data_hash: str, twin_hash: str | None
) -> tuple[str, bool]:
    """The terrain a slot is rated as, and whether it is a pooled mirror-only hole."""
    if (
        len(transforms) == 1
        and transforms[0].startswith(MIRROR_PREFIX)
        and twin_hash is not None
    ):
        return twin_hash, True
    return data_hash, False


@dataclass(frozen=True)
class SiteData:
    rounds: list[PlayedRound]
    #: every seed's slots, by seed id
    seeds: dict[str, tuple[Slot, ...]]
    #: seeds not withdrawn
    live_seeds: frozenset[str]
    #: when each seed was generated, `created_at`
    created: dict[str, str]


def load(db_path: Path | str, catalog: Catalog | None = None) -> SiteData:
    catalog = catalog or Catalog.load()
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        seeds: dict[str, list[Slot]] = {}
        for (
            seed,
            _pos,
            hole_id,
            transforms,
            data_hash,
            pin,
            par,
            dist,
            wind,
        ) in db.execute(_SLOTS_SQL):
            twin = catalog[hole_id].content_hash if hole_id in catalog else None
            terrain, mirrored = terrain_key(json.loads(transforms), data_hash, twin)
            seeds.setdefault(seed, []).append(
                Slot(hole_id, terrain, pin, par, dist or 0, wind, mirrored)
            )
        live = frozenset(
            row[0]
            for row in db.execute("select id from seeds where withdrawn_at is null")
        )
        created = dict(db.execute("select id, created_at from seeds").fetchall())
        tables = {
            row[0]
            for row in db.execute("select name from sqlite_master where type='table'")
        }
        daily = (
            {row[0] for row in db.execute(_DAILY_SQL)}
            if "daily_cards" in tables
            else set()
        )
        holes: dict[str, list] = {}
        for public_id, user, name, seed, received, _pos, strokes in db.execute(
            _ROUNDS_SQL
        ):
            entry = holes.setdefault(public_id, [str(user), name, seed, received, []])
            entry[4].append(strokes)
    finally:
        db.close()
    rounds = [
        PlayedRound(
            id=public_id,
            player=user,
            name=name,
            seed=seed,
            received_at=received,
            slots=tuple(seeds[seed]),
            strokes=tuple(strokes),
            daily=public_id in daily,
        )
        for public_id, (user, name, seed, received, strokes) in holes.items()
        if len(strokes) == len(seeds[seed]) == 18
    ]
    return SiteData(
        rounds=rounds,
        seeds={k: tuple(v) for k, v in seeds.items()},
        live_seeds=live,
        created=created,
    )
