"""Hosted ROMs: the only code that writes `hosted_roms` and `hosted_rom_slots`.

A hosted ROM is a seed's ROM a host builds for the players who claim it, one per pair at an
event (docs/planning/hosted_roms.md). It has two slots, 0 for player 1 and 1 for player 2.
A signed-in player claims one by opening the hosted ROM's claim link, which names it by its
token, and a claim takes the lowest free slot. The host can release either slot and
download the ROM, which builds from the slots as they are then. A download sets
`downloaded_at` and a release clears it, so the hosting page can hide the claim code once a
ROM has left the server.

A hosted ROM expires HOSTED_ROM_HOURS after it is created, and then refuses claims, as a
withdrawn seed's do. Creating, releasing and downloading log themselves through
`server/audit.py`; claims are players' and are kept on the slot instead.

The token is the only way to claim, so it is drawn here and nowhere else, and it stays out
of `repr` and every log.
"""

import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from . import audit
from .db import Database
from .forms import SavedSettings
from .ids import is_id, new_id
from .seeds import utc_now

#: how long after creation a hosted ROM takes claims and downloads
HOSTED_ROM_HOURS = 12
#: the slots a hosted ROM has: player 1 and player 2
SLOTS = (0, 1)
#: draws before creating a hosted ROM gives up on finding an unused public id
ID_ATTEMPTS = 10
#: bytes of randomness in a claim token
TOKEN_BYTES = 16

#: HostingError reasons
#: no hosted ROM has the token, or no seed the id
UNKNOWN = "unknown"
EXPIRED = "expired"
WITHDRAWN = "withdrawn"
#: both slots are claimed by other players
FULL = "full"

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class HostingError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class HostedIdExhaustedError(RuntimeError):
    """Every draw collided with an existing hosted ROM, which only a broken generator makes likely."""


@dataclass(frozen=True)
class HostedSlot:
    slot: int
    #: the claimant's `users.id`, or None while the slot is free
    user_id: int | None
    #: the claimant's display name, or None while the slot is free
    user_name: str | None
    claimed_at: str | None

    @property
    def claimed(self) -> bool:
        return self.user_id is not None


@dataclass(frozen=True)
class HostedRom:
    public_id: str
    seed_id: str
    host_id: int
    #: the claim link's secret; kept out of repr so it never reaches a log
    token: str = field(repr=False)
    #: the hosted options, or None until the host saves them
    options: SavedSettings | None
    created_at: str
    expires_at: str
    downloaded_at: str | None
    #: whether the seed is withdrawn
    withdrawn: bool
    slots: tuple[HostedSlot, HostedSlot]

    def expired(self, now: str | None = None) -> bool:
        return (now if now is not None else utc_now()) >= self.expires_at

    @property
    def claimants(self) -> tuple[int | None, int | None]:
        """Each slot's claimant's `users.id`, None for a free slot."""
        return (self.slots[0].user_id, self.slots[1].user_id)


@dataclass(frozen=True)
class Claim:
    slot: int
    #: whether this claim took the slot, rather than finding the player already in it
    new: bool


def expiry(created_at: str) -> str:
    """When a hosted ROM created at `created_at` expires."""
    created = datetime.strptime(created_at, _TIME_FORMAT).replace(tzinfo=UTC)
    return (created + timedelta(hours=HOSTED_ROM_HOURS)).strftime(_TIME_FORMAT)


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


# -- Writes -------------------------------------------------------------------------------


def create_hosted_rom(
    db: Database, seed_id: str, host_id: int, now: str | None = None
) -> HostedRom:
    """Create a hosted ROM of a seed with both slots free, and log it.

    Raises HostingError: UNKNOWN for a missing seed, WITHDRAWN for a withdrawn one.
    """
    created_at = now if now is not None else utc_now()
    with db.transaction() as conn:
        seed = conn.execute(
            "SELECT withdrawn_at FROM seeds WHERE id = ?", (seed_id,)
        ).fetchone()
        if seed is None:
            raise HostingError(UNKNOWN)
        if seed["withdrawn_at"] is not None:
            raise HostingError(WITHDRAWN)
        public_id = _draw_public_id(conn)
        row_id = conn.execute(
            """
            INSERT INTO hosted_roms (public_id, seed_id, host_id, token, created_at,
                                     expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                public_id,
                seed_id,
                host_id,
                new_token(),
                created_at,
                expiry(created_at),
            ),
        ).lastrowid
        conn.executemany(
            "INSERT INTO hosted_rom_slots (hosted_rom_id, slot) VALUES (?, ?)",
            [(row_id, slot) for slot in SLOTS],
        )
        audit.record(
            conn,
            host_id,
            audit.HOST,
            audit.HOSTED_ROM,
            public_id,
            created_at,
            detail={"seed": seed_id},
        )
        hosted = _load(conn, "hosted_roms.id = ?", (row_id,))
    assert hosted is not None
    return hosted


def claim_slot(db: Database, token: str, user_id: int, now: str | None = None) -> Claim:
    """Claim the lowest free slot of the hosted ROM with this token for a player.

    A player already holding a slot of it gets that slot back, with nothing changed.
    Raises HostingError: UNKNOWN for a token no hosted ROM has, EXPIRED, WITHDRAWN for a
    withdrawn seed, and FULL when both slots are other players'.
    """
    claimed_at = now if now is not None else utc_now()
    with db.transaction() as conn:
        hosted = _load(conn, "hosted_roms.token = ?", (token,))
        if hosted is None:
            raise HostingError(UNKNOWN)
        if hosted.expired(claimed_at):
            raise HostingError(EXPIRED)
        if hosted.withdrawn:
            raise HostingError(WITHDRAWN)
        for held in hosted.slots:
            if held.user_id == user_id:
                return Claim(held.slot, new=False)
        free = next((slot.slot for slot in hosted.slots if not slot.claimed), None)
        if free is None:
            raise HostingError(FULL)
        conn.execute(
            """
            UPDATE hosted_rom_slots SET user_id = ?, claimed_at = ?
            WHERE hosted_rom_id = (SELECT id FROM hosted_roms WHERE token = ?)
              AND slot = ? AND user_id IS NULL
            """,
            (user_id, claimed_at, token, free),
        )
    return Claim(free, new=True)


def release_slot(
    db: Database, public_id: str, slot: int, host_id: int, now: str | None = None
) -> None:
    """Free a slot, show the claim code again, and log it.

    Releasing a free slot only clears `downloaded_at`, which shows the code again. Raises
    KeyError for a missing hosted ROM and ValueError for a slot it does not have.
    """
    if slot not in SLOTS:
        raise ValueError(f"no slot {slot!r}")
    released_at = now if now is not None else utc_now()
    with db.transaction() as conn:
        hosted = _load(conn, "hosted_roms.public_id = ?", (public_id,))
        if hosted is None:
            raise KeyError(public_id)
        conn.execute(
            """
            UPDATE hosted_rom_slots SET user_id = NULL, claimed_at = NULL
            WHERE hosted_rom_id = (SELECT id FROM hosted_roms WHERE public_id = ?)
              AND slot = ?
            """,
            (public_id, slot),
        )
        conn.execute(
            "UPDATE hosted_roms SET downloaded_at = NULL WHERE public_id = ?",
            (public_id,),
        )
        audit.record(
            conn,
            host_id,
            audit.RELEASE,
            audit.HOSTED_ROM,
            public_id,
            released_at,
            detail={"slot": slot, "user_id": hosted.slots[slot].user_id},
        )


def save_hosted_options(db: Database, public_id: str, options: SavedSettings) -> None:
    """Store the options a hosted ROM's download finishes with. Raises KeyError for a missing one."""
    with db.transaction() as conn:
        changed = conn.execute(
            "UPDATE hosted_roms SET options = ? WHERE public_id = ?",
            (json.dumps(options.to_json(), sort_keys=True), public_id),
        ).rowcount
    if not changed:
        raise KeyError(public_id)


def mark_downloaded(
    db: Database,
    public_id: str,
    host_id: int,
    claimants: tuple[int | None, int | None],
    now: str | None = None,
) -> None:
    """Record that a ROM was downloaded for these claimants, and log it.

    `claimants` is each position's `users.id` as the ROM was built, None for a free slot,
    so the log names who the ROM was for. Raises KeyError for a missing hosted ROM.
    """
    downloaded_at = now if now is not None else utc_now()
    with db.transaction() as conn:
        changed = conn.execute(
            "UPDATE hosted_roms SET downloaded_at = ? WHERE public_id = ?",
            (downloaded_at, public_id),
        ).rowcount
        if not changed:
            raise KeyError(public_id)
        audit.record(
            conn,
            host_id,
            audit.HOSTED_DOWNLOAD,
            audit.HOSTED_ROM,
            public_id,
            downloaded_at,
            detail={"claimants": list(claimants)},
        )


# -- Reads --------------------------------------------------------------------------------


def load_hosted_rom(db: Database, public_id: str) -> HostedRom | None:
    """The hosted ROM with this public id, or None for a missing or malformed one."""
    if not is_id(public_id):
        return None
    with db.transaction() as conn:
        return _load(conn, "hosted_roms.public_id = ?", (public_id,))


def load_by_token(db: Database, token: str) -> HostedRom | None:
    """The hosted ROM whose claim link carries this token, or None."""
    with db.transaction() as conn:
        return _load(conn, "hosted_roms.token = ?", (token,))


def hosted_roms_for_host(db: Database, host_id: int) -> list[HostedRom]:
    """A host's hosted ROMs, newest first."""
    with db.transaction() as conn:
        ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM hosted_roms WHERE host_id = ? ORDER BY id DESC",
                (host_id,),
            )
        ]
        loaded = [_load(conn, "hosted_roms.id = ?", (row_id,)) for row_id in ids]
    return [hosted for hosted in loaded if hosted is not None]


def _draw_public_id(conn: sqlite3.Connection) -> str:
    """A public id no hosted ROM holds; the database lock keeps it free until the insert."""
    for _ in range(ID_ATTEMPTS):
        public_id = new_id()
        taken = conn.execute(
            "SELECT 1 FROM hosted_roms WHERE public_id = ?", (public_id,)
        ).fetchone()
        if taken is None:
            return public_id
    raise HostedIdExhaustedError(f"no unused hosted ROM id in {ID_ATTEMPTS} draws")


def _load(conn: sqlite3.Connection, where: str, params: tuple) -> HostedRom | None:
    row = conn.execute(
        f"""
        SELECT hosted_roms.id, hosted_roms.public_id, hosted_roms.seed_id,
               hosted_roms.host_id, hosted_roms.token, hosted_roms.options,
               hosted_roms.created_at, hosted_roms.expires_at, hosted_roms.downloaded_at,
               seeds.withdrawn_at
        FROM hosted_roms JOIN seeds ON seeds.id = hosted_roms.seed_id
        WHERE {where}
        """,
        params,
    ).fetchone()
    if row is None:
        return None
    slots = conn.execute(
        """
        SELECT hosted_rom_slots.slot, hosted_rom_slots.user_id, hosted_rom_slots.claimed_at,
               coalesce(users.global_name, users.username) AS user_name
        FROM hosted_rom_slots LEFT JOIN users ON users.id = hosted_rom_slots.user_id
        WHERE hosted_rom_slots.hosted_rom_id = ?
        ORDER BY hosted_rom_slots.slot
        """,
        (row["id"],),
    ).fetchall()
    first, second = (
        HostedSlot(
            slot=slot["slot"],
            user_id=slot["user_id"],
            user_name=slot["user_name"],
            claimed_at=slot["claimed_at"],
        )
        for slot in slots
    )
    return HostedRom(
        public_id=row["public_id"],
        seed_id=row["seed_id"],
        host_id=row["host_id"],
        token=row["token"],
        options=None
        if row["options"] is None
        else SavedSettings.from_json(json.loads(row["options"])),
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        downloaded_at=row["downloaded_at"],
        withdrawn=row["withdrawn_at"] is not None,
        slots=(first, second),
    )
