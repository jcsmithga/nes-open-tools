"""Submissions: decoding and verifying a scan, and handing the round it carries to `server/rounds.py`.

A scan of a finished ROM's scorecard QR code carries the round's payload
(`golf/qr/payload.py`): 39 bytes in protocol version 2, which adds fairways hit and penalty
strokes, or 36 in version 1, which ROMs built before it still send and which is accepted for
good. `submit_scan` resolves it to the entry it belongs to: the seed ID
names the seed, the player ID the user, and the pair their entry.

The key that verifies the MAC names the round slot: whose round it is on the entry. Slot 0
is the entry owner's own round, slot 1 a teammate's who played player 2 on the owner's ROM.
A ROM a player downloads carries their player ID in both positions, their slot 0 key in
position 0 and their slot 1 key in position 1. A hosted ROM carries each claimant's slot 0
key in whichever position they hold, so a position 1 payload verifying under the slot 0 key
is the owner's own round (docs/planning/hosted_roms.md). The payload's position byte
(`player_slot`) is only the controller the round was played from: what a round is to its
entry is the `rounds.slot` column, never the payload.

A payload that verifies is recorded as the round for (entry, round slot), and the first one
recorded stays: a later scan for the same entry and round slot that also verifies,
identical or not, stores nothing and answers with the round already recorded. A voided
round's payload is refused. See docs/randomizer_devplan.md, "Data model", and
docs/scorecard_qr.md, "Server contract".

A rejected scan is stored nowhere. It logs its exact cause, which the page never shows.
"""

import logging
from dataclasses import dataclass

from golf.qr import payload

from .db import Database
from .rounds import Round, is_voided, record_round, round_in_slot
from .seeds import MAX_QR_SEED_ID, encode_seed_id, utc_now

log = logging.getLogger(__name__)

#: ScanError reasons, each shown by its own strings key in scan_rejected.html
#: not a payload any protocol version sends: length, alphabet, version, flags or slot
MALFORMED = "malformed"
#: an unfinished ROM's all-zero seed or player ID
UNFINISHED = "unfinished"
#: no entry for the seed and player, or a MAC that does not verify with its key
UNRECOGNIZED = "unrecognized"

#: the ROM slots a payload can name: player 1 and player 2
SLOTS = (0, 1)

#: how much of a malformed scan's text a log line quotes
LOGGED_TEXT_LENGTH = 64


class ScanError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _rejected(reason: str, cause: str, **details: object) -> ScanError:
    """Log a rejection's exact cause, and the error that shows the player only its reason."""
    fields = "".join(f" {name}={value}" for name, value in details.items())
    log.warning("scan rejected as %s: %s%s", reason, cause, fields)
    return ScanError(reason)


@dataclass(frozen=True)
class ScanResult:
    round: Round
    #: whether this scan recorded the round, rather than finding it recorded
    new: bool


def _parse(text: str) -> tuple[bytes, payload.RoundPayload]:
    quoted = repr(text[:LOGGED_TEXT_LENGTH])
    version = payload.VERSION_BY_BASE64_LEN.get(len(text))
    if version is None:
        raise _rejected(MALFORMED, "length", length=len(text), text=quoted)
    try:
        data = payload.base64url_decode(text)
    except ValueError:
        raise _rejected(MALFORMED, "alphabet", text=quoted) from None
    # each version has its own length, so the version byte has to match the one it implies
    if data[0] != version:
        raise _rejected(MALFORMED, "protocol version", version=data[0], text=quoted)
    round_payload, _mac = payload.RoundPayload.from_bytes(data)
    if round_payload.reserved_flags != 0:
        raise _rejected(
            MALFORMED, "reserved flags", flags=round_payload.reserved_flags, text=quoted
        )
    if round_payload.player_slot not in SLOTS:
        raise _rejected(MALFORMED, "slot", slot=round_payload.player_slot, text=quoted)
    if not any(round_payload.seed_id):
        raise _rejected(UNFINISHED, "zero seed id")
    if not any(round_payload.player_id):
        raise _rejected(UNFINISHED, "zero player id")
    return data, round_payload


def _missing_entry_cause(conn, qr_seed_id: int, player_id: int) -> str:
    """Which lookup found nothing, for the log."""
    if (
        conn.execute(
            "SELECT 1 FROM seeds WHERE qr_seed_id = ?", (qr_seed_id,)
        ).fetchone()
        is None
    ):
        return "unknown seed"
    if (
        conn.execute("SELECT 1 FROM users WHERE player_id = ?", (player_id,)).fetchone()
        is None
    ):
        return "unknown player"
    return "no entry for the seed and player"


def submit_scan(db: Database, text: str, now: str | None = None) -> ScanResult:
    """Record the round a scan's base64url text carries, or find it already recorded.

    Raises ScanError for a payload that is malformed, from an unfinished ROM, or matches
    no entry's key. Unknown seeds, unknown players, missing entries and bad MACs are one
    reason, so a rejection says nothing about which part failed.
    """
    data, round_payload = _parse(text)
    qr_seed_id = int.from_bytes(round_payload.seed_id, "big")
    player_id = int.from_bytes(round_payload.player_id, "big")
    slot = round_payload.player_slot
    if qr_seed_id > MAX_QR_SEED_ID:  # no seed has it, and SQLite could not bind it
        raise _rejected(
            UNRECOGNIZED,
            "seed id past the range",
            qr_seed_id=qr_seed_id,
            player_id=player_id,
            slot=slot,
        )
    ids = {"seed": encode_seed_id(qr_seed_id), "player_id": player_id, "slot": slot}
    received_at = now if now is not None else utc_now()

    # One transaction from the key lookup to the insert, so two scans of one round at once
    # still record it once.
    with db.transaction() as conn:
        entry = conn.execute(
            """
            SELECT entries.id, entries.key_slot0, entries.key_slot1
            FROM entries
            JOIN seeds ON seeds.id = entries.seed_id
            JOIN users ON users.id = entries.user_id
            WHERE seeds.qr_seed_id = ? AND users.player_id = ?
            """,
            (qr_seed_id, player_id),
        ).fetchone()
        if entry is None:
            raise _rejected(
                UNRECOGNIZED, _missing_entry_cause(conn, qr_seed_id, player_id), **ids
            )
        round_slot = _round_slot(
            data, slot, bytes(entry["key_slot0"]), bytes(entry["key_slot1"])
        )
        if round_slot is None:
            raise _rejected(UNRECOGNIZED, "MAC does not verify", **ids)
        if is_voided(conn, data):
            raise _rejected(UNRECOGNIZED, "voided round", **ids)

        recorded = round_in_slot(conn, entry["id"], round_slot)
        if recorded is not None:
            return ScanResult(recorded, new=False)
        return ScanResult(
            record_round(conn, entry["id"], round_slot, data, received_at), new=True
        )


def _round_slot(data: bytes, position: int, key0: bytes, key1: bytes) -> int | None:
    """The entry's round slot a payload played from `position` records into, or None.

    A position 0 payload verifies under the slot 0 key only. A position 1 payload under
    the slot 1 key is a teammate's round, and under the slot 0 key the owner's own round
    from a hosted ROM's player 2.
    """
    if position == 0:
        return 0 if payload.verify(data, key0) else None
    if payload.verify(data, key1):
        return 1
    if payload.verify(data, key0):
        return 0
    return None
