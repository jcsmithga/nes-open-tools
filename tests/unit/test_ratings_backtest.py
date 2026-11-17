"""Reading a site database and the backtest's pairings and rosters."""

import json
import sqlite3
from types import SimpleNamespace
from typing import cast

from golf.randomizer.catalog import Catalog
from golf.ratings import backtest, sitedb


class FakeCatalog:
    def __init__(self, hashes: dict[str, str]):
        self.hashes = hashes

    def __contains__(self, hole_id):
        return hole_id in self.hashes

    def __getitem__(self, hole_id):
        return SimpleNamespace(content_hash=self.hashes[hole_id])


def site_db(path):
    db = sqlite3.connect(path)
    db.executescript(
        """
        create table users (id integer primary key, username text, global_name text);
        create table seeds (id text primary key, withdrawn_at text, created_at text);
        create table seed_holes (seed_id text, position integer, hole_id text,
            transforms text, data_hash text, pin_index integer, par integer,
            distance integer, wind_speed integer);
        create table entries (id integer primary key, seed_id text, user_id integer);
        create table rounds (id integer primary key, public_id text, entry_id integer,
            slot integer, received_at text);
        create table round_holes (round_id integer, position integer, strokes integer);
        """
    )
    db.execute("insert into users values (1, 'ann', null), (2, 'bo', 'Bo')")
    db.execute("insert into seeds values ('s1', null, '2026-11-01T10:00:00Z')")
    for pos in range(1, 19):
        transforms = json.dumps(["mirror@1"] if pos == 1 else [])
        db.execute(
            "insert into seed_holes values ('s1', ?, ?, ?, ?, 0, 4, 400, 3)",
            (
                pos,
                f"nes_us/{pos:02}",
                transforms,
                f"hash{pos}" + ("m" if pos == 1 else ""),
            ),
        )
    db.execute("insert into entries values (1, 's1', 1), (2, 's1', 2)")
    db.execute(
        "insert into rounds values (1, 'r1', 1, 0, '2026-11-02T12:00:00Z'),"
        " (2, 'r2', 1, 1, '2026-11-02T12:00:00Z'), (3, 'r3', 2, 0, '2026-11-03T12:00:00Z')"
    )
    for rid in (1, 2, 3):
        for pos in range(1, 19):
            db.execute("insert into round_holes values (?, ?, 4)", (rid, pos))
    db.commit()
    db.close()


def test_load_reads_own_rounds_and_pools_mirrors(tmp_path):
    path = tmp_path / "site.db"
    site_db(path)
    catalog = cast(
        Catalog, FakeCatalog({f"nes_us/{p:02}": f"hash{p}" for p in range(1, 19)})
    )
    data = sitedb.load(path, catalog)
    assert sorted(r.id for r in data.rounds) == ["r1", "r3"]
    first = data.seeds["s1"][0]
    assert (first.terrain, first.mirrored) == ("hash1", True)
    assert data.rounds[1].name == "Bo"
    assert data.created == {"s1": "2026-11-01T10:00:00Z"}


def card(player: str, seed: str, handicap: int, strokes: list[int]) -> backtest.Card:
    return backtest.Card(
        player=player,
        seed=seed,
        prior_rounds=5,
        index=float(handicap),
        handicap=handicap,
        strokes=tuple(strokes),
        pars=(4,) * 18,
        stroke_indexes=tuple(range(1, 19)),
        expected=72.0 + handicap,
    )


def test_pairs_score_the_stronger_player():
    strong = card("s", "x", 0, [4] * 18)
    weak = card("w", "x", 18, [5] * 18)
    (pair,) = backtest.pairs([strong, weak], {"s": 0.0, "w": 18.0}, 3)
    assert (pair.stronger, pair.match, pair.stroke) == ("s", 0.5, 0.5)
    worse = card("w", "x", 18, [6] * 18)
    (pair,) = backtest.pairs([strong, worse], {"s": 0.0, "w": 18.0}, 3)
    assert pair.match == 1.0


def test_rosters_draw_every_player_count():
    draws = backtest.rosters(["a", "b", "c"], 50)
    assert all(sum(w.values()) == 3 for w in draws)
    assert backtest.day_of("2026-11-17T08:59:00Z").isoformat() == "2026-11-16"
