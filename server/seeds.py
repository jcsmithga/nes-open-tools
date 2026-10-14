"""Seed identity and the seed tables: the only code that writes `seeds`, `seed_holes` and `hole_data`.

A seed's `qr_seed_id` is drawn uniformly from 1 to 62**10 - 1, and its URL id is that
integer in base62 (`server/ids.py`), so either converts to the other. See
docs/randomizer_devplan.md, "Generating seed ids".

Every slot has its hole, as built, stored in `hole_data` under its content hash, and its
`seed_holes` row keeps what the seed's pages would otherwise ask the catalog: the hole's
distance, author and source. The seed row keeps the ROMs a download needs. A seed is what
was stored when it was made (ADR 0024), so nothing a page shows depends on a later
release's transforms or catalog (ADR 0021). A stored manifest is read as a
`StoredManifest`, whatever schema it was issued in.
"""

import hashlib
import json
import secrets
import sqlite3
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from golf.core.rng import predict_hole
from golf.formats.hole_data import HoleData
from golf.randomizer.catalog import (
    Catalog,
    CatalogEntry,
    RomSource,
    canonical_dict,
    canonical_json,
)
from golf.randomizer.manifest import Manifest, StoredManifest, required_roms

from . import audit
from .db import Database
from .ids import ALPHABET, ID_LENGTH, MAX_VALUE, decode_base62, encode_base62, is_id

__all__ = ["ALPHABET", "ID_LENGTH"]  # re-exported: a seed id is a base62 id

#: the largest qr_seed_id, and the version 1.0 baseline's CHECK holds the column to
MAX_QR_SEED_ID = MAX_VALUE
#: draws before an insert gives up on finding an unused id
INSERT_ATTEMPTS = 10


class SeedIdError(ValueError):
    """Text that is not a seed's URL id, or an integer outside the qr_seed_id range."""


class SeedIdExhaustedError(RuntimeError):
    """Every draw collided with an existing seed, which only a broken generator makes likely."""


class SeedAlreadyWithdrawnError(ValueError):
    """A seed cannot be withdrawn while it is already withdrawn."""


class SeedNotWithdrawnError(ValueError):
    """A seed cannot be restored while it is active."""


def encode_seed_id(value: int) -> str:
    """A qr_seed_id as its 10-character base62 URL id."""
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_QR_SEED_ID
    ):
        raise SeedIdError(f"a qr_seed_id is 1-{MAX_QR_SEED_ID}, got {value!r}")
    return encode_base62(value)


def decode_seed_id(text: str) -> int:
    """A URL id as its qr_seed_id. Raises SeedIdError for anything else."""
    if not is_id(text):
        raise SeedIdError(
            f"a seed id is {ID_LENGTH} characters of 0-9, A-Z and a-z, got {text!r}"
        )
    value = decode_base62(text)
    if value == 0:
        raise SeedIdError("no seed has the id 0000000000")
    return value


def new_qr_seed_id() -> int:
    return secrets.randbelow(MAX_QR_SEED_ID) + 1


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def manifest_text(manifest: Manifest) -> str:
    """The manifest as stored on the seed row, and as `/h/<id>.json` serves it."""
    return json.dumps(manifest.to_json(), indent=2) + "\n"


class MissingHolesError(ValueError):
    """A seed was stored without the 18 holes its build made."""


@dataclass(frozen=True)
class StoredHole:
    """What a seed keeps of one slot's hole beyond its manifest: the hole as built, and
    the catalog's facts about it."""

    content_hash: str
    #: the zlib-compressed canonical JSON
    data: bytes
    distance: int
    author: str
    #: where a vanilla hole comes from; None for any other kind
    source: RomSource | None


def stored_hole(entry: CatalogEntry, hole: HoleData) -> StoredHole:
    """A slot's hole as built, to store."""
    text = canonical_json(hole)
    return StoredHole(
        hashlib.sha256(text).hexdigest(),
        zlib.compress(text, 9),
        canonical_dict(hole)["distance"],
        entry.author,
        entry.source if isinstance(entry.source, RomSource) else None,
    )


def stored_holes(
    manifest: StoredManifest, holes: Sequence[HoleData], catalog: Catalog
) -> list[StoredHole]:
    """Each slot's hole as built, with its catalog facts.

    `holes` are the manifest's 18 as built, in order. Raises MissingHolesError when there
    are not 18.
    """
    slots = manifest.course.holes
    if len(holes) != len(slots):
        raise MissingHolesError(
            f"a seed needs the {len(slots)} holes its build made, got {len(holes)}"
        )
    return [
        stored_hole(catalog[slot.id], hole)
        for slot, hole in zip(slots, holes, strict=True)
    ]


def hole_rows(
    seed_id: str, manifest: StoredManifest, holes: Sequence[StoredHole]
) -> list[tuple]:
    """The seed's `seed_holes` rows, in `SEED_HOLE_COLUMNS` order: each slot with the pin
    its wind seed gives, its wind anchors and its stored hole."""
    rows = []
    for position, (slot, hole) in enumerate(
        zip(manifest.course.holes, holes, strict=True), start=1
    ):
        forecast = predict_hole(slot.wind_seed, swings=0)
        source = hole.source
        rows.append(
            (
                seed_id,
                position,
                str(slot.id),
                json.dumps(list(slot.transforms)),
                slot.par,
                slot.wind_seed,
                forecast.pin_index,
                slot.wind_direction,
                slot.wind_speed,
                hole.content_hash,
                hole.distance,
                hole.author,
                None if source is None else source.rom,
                None if source is None else source.course,
                None if source is None else source.hole,
            )
        )
    return rows


SEED_HOLE_COLUMNS = (
    "seed_id",
    "position",
    "hole_id",
    "transforms",
    "par",
    "wind_seed",
    "pin_index",
    "wind_direction",
    "wind_speed",
    "data_hash",
    "distance",
    "author",
    "source_rom",
    "source_course",
    "source_hole",
)
_INSERT_SEED_HOLE = (
    f"INSERT INTO seed_holes ({', '.join(SEED_HOLE_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in SEED_HOLE_COLUMNS)})"
)


def _hole_data_rows(holes: Sequence[StoredHole]) -> list[tuple[str, bytes]]:
    return [(hole.content_hash, hole.data) for hole in holes]


def _is_id_collision(problem: sqlite3.IntegrityError) -> bool:
    message = str(problem)
    return "UNIQUE" in message and (
        "seeds.id" in message or "seeds.qr_seed_id" in message
    )


def insert_seed(
    db: Database,
    manifest: Manifest,
    unfinished_ips: bytes,
    holes: Sequence[HoleData],
    catalog: Catalog,
    creator_id: int | None = None,
    now: str | None = None,
    draw: Callable[[], int] = new_qr_seed_id,
) -> str:
    """Store a seed and its holes in one transaction, and return its URL id.

    `holes` are the 18 the build made (`UnfinishedBuild.holes`), and `catalog` the one it
    built from, which gives each hole's author and source and the seed's required ROMs. A
    drawn id that is already taken is drawn again, up to INSERT_ATTEMPTS times.
    """
    return insert_issued_seed(
        db,
        manifest_text(manifest),
        unfinished_ips,
        holes,
        catalog,
        creator_id=creator_id,
        now=now,
        draw=draw,
    )


def insert_issued_seed(
    db: Database,
    text: str,
    unfinished_ips: bytes,
    holes: Sequence[HoleData],
    catalog: Catalog,
    creator_id: int | None = None,
    now: str | None = None,
    draw: Callable[[], int] = new_qr_seed_id,
) -> str:
    """`insert_seed` for a manifest's text as issued, in any schema.

    The seed's rows are read from the text as `load_seed` reads it back, so a test can
    store a seed as an older release made it.
    """
    created_at = now if now is not None else utc_now()
    manifest = StoredManifest.from_json(json.loads(text))
    stored = stored_holes(manifest, holes, catalog)
    roms = json.dumps(list(required_roms(manifest, catalog)))
    for _ in range(INSERT_ATTEMPTS):
        qr_seed_id = draw()
        seed_id = encode_seed_id(qr_seed_id)
        try:
            with db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO seeds (id, qr_seed_id, manifest, generator_version, build_version,
                                       finish_abi_version, catalog_version, curation_stamp,
                                       unfinished_ips, creator_id, created_at, required_roms)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        seed_id,
                        qr_seed_id,
                        text,
                        manifest.generator_version,
                        manifest.build_version,
                        manifest.finish_abi_version,
                        manifest.catalog_version,
                        manifest.curation_stamp,
                        unfinished_ips,
                        creator_id,
                        created_at,
                        roms,
                    ),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO hole_data VALUES (?, ?)",
                    _hole_data_rows(stored),
                )
                conn.executemany(
                    _INSERT_SEED_HOLE, hole_rows(seed_id, manifest, stored)
                )
        except sqlite3.IntegrityError as problem:
            if _is_id_collision(problem):
                continue
            raise
        return seed_id
    raise SeedIdExhaustedError(f"no unused seed id in {INSERT_ATTEMPTS} draws")


@dataclass(frozen=True)
class SeedRow:
    id: str
    qr_seed_id: int
    #: the manifest exactly as stored
    manifest_json: str
    #: the manifest as read for showing and finishing the seed, whatever its schema
    manifest: StoredManifest
    build_version: int
    finish_abi_version: int
    #: the vanilla ROMs a download needs, as they were when the seed was made
    required_roms: tuple[str, ...]
    creator_id: int | None
    created_at: str
    withdrawn_at: str | None

    @property
    def withdrawn(self) -> bool:
        return self.withdrawn_at is not None


def load_seed(db: Database, seed_id: str) -> SeedRow | None:
    """The seed with this URL id, or None when there is none or the text is not a seed id."""
    try:
        decode_seed_id(seed_id)
    except SeedIdError:
        return None
    with db.transaction() as conn:
        row = conn.execute(
            """
            SELECT id, qr_seed_id, manifest, build_version, finish_abi_version,
                   required_roms, creator_id, created_at, withdrawn_at
            FROM seeds WHERE id = ?
            """,
            (seed_id,),
        ).fetchone()
    if row is None:
        return None
    manifest = StoredManifest.from_json(json.loads(row["manifest"]))
    if row["build_version"] != manifest.build_version:
        raise RuntimeError(
            f"seed {row['id']} stores build version {row['build_version']} but its "
            f"manifest requires {manifest.build_version}"
        )
    if row["finish_abi_version"] != manifest.finish_abi_version:
        raise RuntimeError(
            f"seed {row['id']} stores finish ABI {row['finish_abi_version']} but its "
            f"manifest requires {manifest.finish_abi_version}"
        )
    if row["required_roms"] is None:
        raise RuntimeError(f"seed {row['id']} {NOT_BACKFILLED}")
    return SeedRow(
        id=row["id"],
        qr_seed_id=row["qr_seed_id"],
        manifest_json=row["manifest"],
        manifest=manifest,
        build_version=row["build_version"],
        finish_abi_version=row["finish_abi_version"],
        required_roms=tuple(json.loads(row["required_roms"])),
        creator_id=row["creator_id"],
        created_at=row["created_at"],
        withdrawn_at=row["withdrawn_at"],
    )


@dataclass(frozen=True)
class SeedHole:
    """A seed's hole as `seed_holes` has it, for showing rather than building."""

    position: int
    hole_id: str
    par: int
    pin_index: int
    #: the `hole_data` row holding the hole as built
    data_hash: str
    distance: int
    author: str
    #: where a vanilla hole comes from, all None for any other kind
    source_rom: str | None
    source_course: str | None
    source_hole: int | None


def load_seed_holes(db: Database, seed_id: str) -> list[SeedHole]:
    """The seed's holes in playing order; none when there is no such seed."""
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT position, hole_id, par, pin_index, data_hash, distance, author,
                   source_rom, source_course, source_hole
            FROM seed_holes WHERE seed_id = ? ORDER BY position
            """,
            (seed_id,),
        ).fetchall()
    holes = []
    for row in rows:
        if row["data_hash"] is None or row["distance"] is None or row["author"] is None:
            raise RuntimeError(f"seed {seed_id} {NOT_BACKFILLED}")
        holes.append(SeedHole(**dict(row)))
    return holes


def load_hole_data(db: Database, content_hash: str) -> dict[str, Any]:
    """A stored hole, as the dict its canonical JSON holds, checked against its hash."""
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT data FROM hole_data WHERE content_hash = ?", (content_hash,)
        ).fetchone()
    if row is None:
        raise LookupError(f"no stored hole {content_hash}")
    data = zlib.decompress(row["data"])
    actual = hashlib.sha256(data).hexdigest()
    if actual != content_hash:
        raise RuntimeError(f"stored hole {content_hash} has content hash {actual}")
    return json.loads(data)


def load_unfinished_ips(db: Database, seed_id: str) -> bytes | None:
    """The seed's stored unfinished IPS, or None when there is no such seed.

    A query of its own, so pages that only show a seed never read the blob.
    """
    try:
        decode_seed_id(seed_id)
    except SeedIdError:
        return None
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT unfinished_ips FROM seeds WHERE id = ?", (seed_id,)
        ).fetchone()
    return None if row is None else bytes(row["unfinished_ips"])


# -- Seeds stored before migration 7 ----------------------------------------------------------

#: what a seed stored before its holes were kept lacks, and how to fill it in
NOT_BACKFILLED = (
    "predates stored holes; fill it in with golf-site-backfill from server-v4.1.3 "
    "(docs/deployment.md)"
)


def unfilled_seeds(db: Database) -> list[str]:
    """The seeds stored before their holes and catalog facts were kept, in id order."""
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT id FROM seeds WHERE required_roms IS NULL
            UNION
            SELECT seed_id FROM seed_holes
            WHERE data_hash IS NULL OR distance IS NULL OR author IS NULL
            ORDER BY 1
            """
        ).fetchall()
    return [row[0] for row in rows]


def _note(text: str | None) -> str | None:
    text = (text or "").strip()
    return text or None


def withdraw_seed(
    db: Database,
    seed_id: str,
    admin_id: int,
    note: str | None = None,
    now: str | None = None,
) -> None:
    """Withdraw a seed from downloads and log it atomically.

    Raises KeyError for a missing seed and SeedAlreadyWithdrawnError when it is
    already withdrawn.
    """
    withdrawn_at = now if now is not None else utc_now()
    note = _note(note)
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT withdrawn_at FROM seeds WHERE id = ?", (seed_id,)
        ).fetchone()
        if row is None:
            raise KeyError(seed_id)
        if row["withdrawn_at"] is not None:
            raise SeedAlreadyWithdrawnError(seed_id)
        conn.execute(
            "UPDATE seeds SET withdrawn_at = ? WHERE id = ?", (withdrawn_at, seed_id)
        )
        audit.record(
            conn,
            admin_id,
            audit.WITHDRAW,
            audit.SEED,
            seed_id,
            withdrawn_at,
            note=note,
        )


def restore_seed(
    db: Database, seed_id: str, admin_id: int, now: str | None = None
) -> None:
    """Restore a withdrawn seed's downloads and log it atomically.

    Raises KeyError for a missing seed and SeedNotWithdrawnError when it is active.
    """
    restored_at = now if now is not None else utc_now()
    with db.transaction() as conn:
        row = conn.execute(
            "SELECT withdrawn_at FROM seeds WHERE id = ?", (seed_id,)
        ).fetchone()
        if row is None:
            raise KeyError(seed_id)
        if row["withdrawn_at"] is None:
            raise SeedNotWithdrawnError(seed_id)
        conn.execute("UPDATE seeds SET withdrawn_at = NULL WHERE id = ?", (seed_id,))
        audit.record(conn, admin_id, audit.RESTORE, audit.SEED, seed_id, restored_at)
