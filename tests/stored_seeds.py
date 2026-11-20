"""Storing a seed without building it, for tests of what the site does with stored seeds."""

import json
from functools import cache

from golf.randomizer.catalog import Catalog
from golf.randomizer.manifest import Manifest, StoredManifest
from server.db import Database
from server.seeds import insert_issued_seed, insert_seed
from tests.legacy_manifest import as_schema
from tests.synthetic_holes import built_holes


@cache
def checked_in_catalog() -> Catalog:
    return Catalog.load()


def store_seed(
    db: Database,
    manifest: Manifest,
    ips: bytes = b"PATCHEOF",
    catalog: Catalog | None = None,
    schema: int | None = None,
    **kwargs,
) -> str:
    """Insert `manifest` as a seed whose build made synthetic holes (`built_holes`).

    With `schema`, the manifest is stored as a release of that schema issued it
    (`as_schema`). The catalog defaults to the checked-in one; `kwargs` go to
    `insert_seed`.
    """
    catalog = catalog if catalog is not None else checked_in_catalog()
    if schema is not None:
        issued = as_schema(manifest.to_json(), schema)
        return store_issued(db, issued, ips, catalog, **kwargs)
    return insert_seed(
        db, manifest, ips, built_holes(manifest, catalog), catalog, **kwargs
    )


def store_issued(
    db: Database,
    issued: dict,
    ips: bytes = b"PATCHEOF",
    catalog: Catalog | None = None,
    **kwargs,
) -> str:
    """Insert a manifest's JSON as issued, in any schema, as a seed whose build made
    synthetic holes. `kwargs` go to `insert_issued_seed`."""
    catalog = catalog if catalog is not None else checked_in_catalog()
    stored = StoredManifest.from_json(issued)
    text = json.dumps(issued, indent=2) + "\n"
    return insert_issued_seed(
        db, text, ips, built_holes(stored, catalog), catalog, **kwargs
    )


def unfill(db: Database, seed_id: str, keep: tuple[int, ...] = ()) -> None:
    """Make a stored seed look like one stored before migration 7: no required ROMs,
    no catalog facts, and only the holes at the positions in `keep` stored, as only a
    transformed hole was."""
    with db.transaction() as conn:
        conn.execute("UPDATE seeds SET required_roms = NULL WHERE id = ?", (seed_id,))
        conn.execute(
            f"""
            UPDATE seed_holes SET distance = NULL, author = NULL, source_rom = NULL,
                source_course = NULL, source_hole = NULL,
                data_hash = CASE WHEN position IN ({",".join(map(str, keep)) or "0"})
                            THEN data_hash END
            WHERE seed_id = ?
            """,
            (seed_id,),
        )
        conn.execute(
            "DELETE FROM hole_data WHERE content_hash NOT IN "
            "(SELECT data_hash FROM seed_holes WHERE data_hash IS NOT NULL)"
        )


#: a ranking of 18 holes that is neither hole order nor its reverse
STROKE_INDEXES = (7, 13, 1, 17, 5, 11, 3, 15, 9, 8, 14, 2, 18, 6, 12, 4, 16, 10)


def store_rating(
    db: Database, seed_id: str, stroke_indexes: tuple[int, ...] = STROKE_INDEXES
) -> None:
    """Rate a stored seed with `stroke_indexes` under a stand-in run, without fitting one.

    For tests of what the site does with a seed's stored ratings; the other numbers are
    placeholders.
    """
    with db.transaction() as conn:
        run = conn.execute("SELECT id FROM ratings_runs LIMIT 1").fetchone()
        if run is None:
            run_id = conn.execute(
                """
                INSERT INTO ratings_runs (day, created_at, model_version,
                    parameters_hash, parameters, model, latest_received_at, n_rounds)
                VALUES ('2026-11-18', '2026-11-18T09:00:00Z', 1, ?, '{}', '{}',
                    '2026-11-18T09:00:00Z', 1)
                """,
                ("0" * 64,),
            ).lastrowid
        else:
            run_id = run["id"]
        conn.execute(
            """
            INSERT INTO seed_ratings (seed_id, run_id, rated_at, par, course_rating, slope)
            VALUES (?, ?, '2026-11-18T09:00:00Z', 72, 72.0, 1.0)
            """,
            (seed_id, run_id),
        )
        conn.executemany(
            """
            INSERT INTO seed_hole_ratings (seed_id, position, rating, spread, stroke_index)
            VALUES (?, ?, 4.0, 1.0, ?)
            """,
            [
                (seed_id, position, index)
                for position, index in enumerate(stroke_indexes, start=1)
            ],
        )
