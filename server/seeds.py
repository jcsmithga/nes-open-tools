"""Seed identity and the seed tables: the only code that writes `seeds`, `seed_holes` and `hole_data`.

A seed's `qr_seed_id` is drawn uniformly from 1 to 62**10 - 1, and its URL id is that
integer in base62 (`server/ids.py`), so either converts to the other. See
docs/randomizer_devplan.md, "Generating seed ids".

A slot with transforms has its hole, as built, stored in `hole_data` under its content
hash (ADR 0021): a later release's transforms may not give the same hole
(ADR 0024), and the yardage book has to show the one in the ROM.
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
from golf.randomizer.catalog import canonical_json
from golf.randomizer.manifest import Manifest

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
    """A manifest with transforms was stored without the holes its build made."""


def stored_holes(
    manifest: Manifest, holes: Sequence[HoleData] | None
) -> list[tuple[str, bytes] | None]:
    """For each slot, its hole's `hole_data` row if it has transforms, else None.

    `holes` are the manifest's 18 as built, in order. Raises MissingHolesError when a
    slot has transforms and there are none.
    """
    slots = manifest.course.holes
    stored: list[tuple[str, bytes] | None] = []
    for index, slot in enumerate(slots):
        if not slot.transforms:
            stored.append(None)
            continue
        if holes is None or len(holes) != len(slots):
            raise MissingHolesError(
                f"hole {index + 1} ({slot.id}) has transforms, so the seed needs "
                f"the {len(slots)} holes its build made"
            )
        data = canonical_json(holes[index])
        stored.append((hashlib.sha256(data).hexdigest(), zlib.compress(data, 9)))
    return stored


def hole_rows(
    seed_id: str, manifest: Manifest, data_hashes: Sequence[str | None] | None = None
) -> list[tuple]:
    """The seed's `seed_holes` rows: each slot with the pin its wind seed gives and its wind anchors.

    `data_hashes` has, for each slot, the `hole_data` row holding its hole, or None for a
    slot with no transforms.
    """
    rows = []
    for position, slot in enumerate(manifest.course.holes, start=1):
        forecast = predict_hole(slot.wind_seed, swings=0)
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
                None if data_hashes is None else data_hashes[position - 1],
            )
        )
    return rows


def _is_id_collision(problem: sqlite3.IntegrityError) -> bool:
    message = str(problem)
    return "UNIQUE" in message and (
        "seeds.id" in message or "seeds.qr_seed_id" in message
    )


def insert_seed(
    db: Database,
    manifest: Manifest,
    unfinished_ips: bytes,
    creator_id: int | None = None,
    now: str | None = None,
    draw: Callable[[], int] = new_qr_seed_id,
    holes: Sequence[HoleData] | None = None,
) -> str:
    """Store a seed and its holes in one transaction, and return its URL id.

    `holes` are the 18 the build made (`UnfinishedBuild.holes`); the ones with transforms
    are stored, and a manifest with none needs no holes. A drawn id that is already taken
    is drawn again, up to INSERT_ATTEMPTS times.
    """
    created_at = now if now is not None else utc_now()
    text = manifest_text(manifest)
    stored = stored_holes(manifest, holes)
    data_hashes = [None if hole is None else hole[0] for hole in stored]
    for _ in range(INSERT_ATTEMPTS):
        qr_seed_id = draw()
        seed_id = encode_seed_id(qr_seed_id)
        try:
            with db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO seeds (id, qr_seed_id, manifest, generator_version, build_version,
                                       finish_abi_version, catalog_version, curation_stamp,
                                       unfinished_ips, creator_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO hole_data VALUES (?, ?)",
                    [hole for hole in stored if hole is not None],
                )
                conn.executemany(
                    "INSERT INTO seed_holes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    hole_rows(seed_id, manifest, data_hashes),
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
    manifest: Manifest
    build_version: int
    finish_abi_version: int
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
                   creator_id, created_at, withdrawn_at
            FROM seeds WHERE id = ?
            """,
            (seed_id,),
        ).fetchone()
    if row is None:
        return None
    manifest = Manifest.from_json(json.loads(row["manifest"]))
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
    return SeedRow(
        id=row["id"],
        qr_seed_id=row["qr_seed_id"],
        manifest_json=row["manifest"],
        manifest=manifest,
        build_version=row["build_version"],
        finish_abi_version=row["finish_abi_version"],
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
    #: the `hole_data` row holding the hole, None when it is the catalog's
    data_hash: str | None


def load_seed_holes(db: Database, seed_id: str) -> list[SeedHole]:
    """The seed's holes in playing order; none when there is no such seed."""
    with db.transaction() as conn:
        rows = conn.execute(
            """
            SELECT position, hole_id, par, pin_index, data_hash
            FROM seed_holes WHERE seed_id = ? ORDER BY position
            """,
            (seed_id,),
        ).fetchall()
    return [
        SeedHole(
            position=row["position"],
            hole_id=row["hole_id"],
            par=row["par"],
            pin_index=row["pin_index"],
            data_hash=row["data_hash"],
        )
        for row in rows
    ]


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
