"""Seed ids and the seed tables: base62 ids, inserting a seed with its holes, loading it back."""

import hashlib
import json
import zlib
from dataclasses import replace

import pytest

from golf.core.rng import predict_hole
from golf.randomizer.catalog import (
    Catalog,
    CatalogEntry,
    DerivedSource,
    HoleId,
    RomSource,
    canonical_json,
    content_hash,
)
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Manifest, Settings, StoredManifest, required_roms
from server.db import Database
from server.seeds import (
    ID_LENGTH,
    INSERT_ATTEMPTS,
    MAX_QR_SEED_ID,
    MissingHolesError,
    SeedAlreadyWithdrawnError,
    SeedIdError,
    SeedIdExhaustedError,
    SeedNotWithdrawnError,
    SeedRow,
    decode_seed_id,
    encode_seed_id,
    insert_seed,
    load_hole_data,
    load_seed,
    load_seed_holes,
    load_unfinished_ips,
    manifest_text,
    new_qr_seed_id,
    restore_seed,
    unfilled_seeds,
    withdraw_seed,
)
from server.users import sign_in
from tests.stored_seeds import store_seed, unfill
from tests.synthetic_holes import synthetic_hole

IPS = b"PATCH\x00\x00\x10\x00\x01\xeaEOF"


def seed_row(db: Database, seed_id: str) -> SeedRow:
    """The stored seed with this id, which must exist."""
    row = load_seed(db, seed_id)
    assert row is not None
    return row


@pytest.fixture(scope="module")
def manifest() -> Manifest:
    return generate(
        Catalog.load(), CurationSnapshot.load(), Settings(prng_seed="server-seeds")
    )


@pytest.fixture
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


def counts(db: Database) -> tuple[int, int]:
    with db.transaction() as conn:
        seeds = conn.execute("SELECT count(*) FROM seeds").fetchone()[0]
        holes = conn.execute("SELECT count(*) FROM seed_holes").fetchone()[0]
    return seeds, holes


# -- Ids --------------------------------------------------------------------------------------


def test_the_max_id_is_the_migrations_bound():
    assert MAX_QR_SEED_ID == 62**10 - 1 == 839299365868340223
    assert MAX_QR_SEED_ID < 2**60


@pytest.mark.parametrize(
    "value, text",
    [
        (1, "0000000001"),
        (61, "000000000z"),
        (62, "0000000010"),
        (MAX_QR_SEED_ID, "zzzzzzzzzz"),
    ],
)
def test_ids_encode_most_significant_digit_first_padded_to_ten(value, text):
    assert encode_seed_id(value) == text
    assert decode_seed_id(text) == value


def test_ids_round_trip():
    for value in (new_qr_seed_id() for _ in range(200)):
        text = encode_seed_id(value)
        assert len(text) == ID_LENGTH
        assert decode_seed_id(text) == value


@pytest.mark.parametrize("value", [0, -1, MAX_QR_SEED_ID + 1, True, "1"])
def test_encoding_refuses_values_outside_the_range(value):
    with pytest.raises(SeedIdError):
        encode_seed_id(value)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "000000001",
        "00000000001",
        "0000000000",
        "abc-def-gh",
        "00000000é1",
        "nope.json",
    ],
)
def test_decoding_refuses_anything_but_an_id(text):
    with pytest.raises(SeedIdError):
        decode_seed_id(text)


def test_drawn_ids_are_in_range():
    assert all(1 <= new_qr_seed_id() <= MAX_QR_SEED_ID for _ in range(1000))


# -- Inserting and loading ------------------------------------------------------------------


def test_a_seed_is_stored_with_its_eighteen_holes(db, manifest):
    seed_id = store_seed(
        db, manifest, IPS, now="2026-09-15T12:00:00Z", draw=lambda: 12345
    )
    assert seed_id == encode_seed_id(12345)
    assert counts(db) == (1, 18)
    with db.transaction() as conn:
        seed = conn.execute("SELECT * FROM seeds").fetchone()
        holes = conn.execute("SELECT * FROM seed_holes ORDER BY position").fetchall()
    assert seed["qr_seed_id"] == 12345
    assert seed["unfinished_ips"] == IPS
    assert seed["generator_version"] == manifest.generator_version
    assert seed["build_version"] == manifest.build_version
    assert seed["finish_abi_version"] == manifest.finish_abi_version
    assert seed["catalog_version"] == manifest.catalog_version
    assert seed["curation_stamp"] == manifest.curation_stamp
    assert seed["creator_id"] is None
    assert seed["created_at"] == "2026-09-15T12:00:00Z"
    assert Manifest.from_json(json.loads(seed["manifest"])) == manifest

    for hole, slot in zip(holes, manifest.course.holes, strict=True):
        forecast = predict_hole(slot.wind_seed)
        assert hole["seed_id"] == seed_id
        assert hole["hole_id"] == str(slot.id)
        assert json.loads(hole["transforms"]) == []
        assert hole["par"] == slot.par
        assert hole["wind_seed"] == slot.wind_seed
        assert hole["pin_index"] == forecast.pin_index
        assert hole["wind_direction"] == forecast.direction_anchor
        assert hole["wind_speed"] == forecast.speed_anchor
    assert [hole["position"] for hole in holes] == list(range(1, 19))


def test_created_at_defaults_to_utc_now(db, manifest):
    seed_id = store_seed(db, manifest, IPS)
    assert seed_row(db, seed_id).created_at.endswith("Z")


def test_a_taken_id_is_drawn_again(db, manifest):
    store_seed(db, manifest, IPS, draw=lambda: 7)
    draws = iter([7, 7, 8])
    assert store_seed(db, manifest, IPS, draw=lambda: next(draws)) == encode_seed_id(8)
    assert counts(db) == (2, 36)


def test_insert_gives_up_after_its_attempts(db, manifest):
    store_seed(db, manifest, IPS, draw=lambda: 7)
    calls = []

    def draw():
        calls.append(1)
        return 7

    with pytest.raises(SeedIdExhaustedError):
        store_seed(db, manifest, IPS, draw=draw)
    assert len(calls) == INSERT_ATTEMPTS
    assert counts(db) == (1, 18)


def test_load_seed_returns_the_stored_manifest(db, manifest):
    seed_id = store_seed(db, manifest, IPS, creator_id=None, draw=lambda: 99)
    row = seed_row(db, seed_id)
    assert row.id == seed_id
    assert row.qr_seed_id == 99
    assert row.manifest == StoredManifest.from_json(manifest.to_json())
    assert row.manifest_json == manifest_text(manifest)
    assert row.build_version == manifest.build_version
    assert row.finish_abi_version == manifest.finish_abi_version
    assert row.withdrawn_at is None
    assert not row.withdrawn


@pytest.mark.parametrize("text", ["0000000001", "not-an-id", "nope.json"])
def test_load_seed_is_none_for_a_missing_or_malformed_id(db, text):
    assert load_seed(db, text) is None


def test_load_unfinished_ips_returns_the_stored_blob(db, manifest):
    seed_id = store_seed(db, manifest, IPS)
    assert load_unfinished_ips(db, seed_id) == IPS


@pytest.mark.parametrize("text", ["0000000001", "not-an-id"])
def test_load_unfinished_ips_is_none_for_a_missing_or_malformed_id(db, text):
    assert load_unfinished_ips(db, text) is None


# -- Withdrawal -----------------------------------------------------------------------------


def admin(db: Database):
    return sign_in(
        db,
        "dev:admin",
        "admin",
        None,
        None,
        now="2026-09-20T12:00:00Z",
        draw=lambda: 1,
    )


def test_withdraw_and_restore_change_only_lifecycle_state_and_log_each_action(
    db, manifest
):
    seed_id = store_seed(db, manifest, IPS, draw=lambda: 7)
    user = admin(db)
    original = seed_row(db, seed_id)

    withdraw_seed(
        db,
        seed_id,
        user.id,
        "  broken course  ",
        now="2026-09-20T13:00:00Z",
    )
    withdrawn = seed_row(db, seed_id)
    assert withdrawn.withdrawn_at == "2026-09-20T13:00:00Z"
    assert withdrawn.withdrawn
    assert withdrawn.manifest_json == original.manifest_json
    assert load_unfinished_ips(db, seed_id) == IPS

    restore_seed(db, seed_id, user.id, now="2026-09-20T14:00:00Z")
    restored = seed_row(db, seed_id)
    assert restored.withdrawn_at is None
    assert not restored.withdrawn
    assert restored.manifest_json == original.manifest_json
    assert load_unfinished_ips(db, seed_id) == IPS

    with db.transaction() as conn:
        actions = conn.execute(
            """
            SELECT action, target_type, target_id, note, created_at
            FROM admin_actions ORDER BY id
            """
        ).fetchall()
    assert [tuple(action) for action in actions] == [
        ("withdraw", "seed", seed_id, "broken course", "2026-09-20T13:00:00Z"),
        ("restore", "seed", seed_id, None, "2026-09-20T14:00:00Z"),
    ]


def test_invalid_withdrawal_transitions_write_no_audit_rows(db, manifest):
    seed_id = store_seed(db, manifest, IPS)
    user = admin(db)
    with pytest.raises(SeedNotWithdrawnError):
        restore_seed(db, seed_id, user.id)
    withdraw_seed(db, seed_id, user.id)
    with pytest.raises(SeedAlreadyWithdrawnError):
        withdraw_seed(db, seed_id, user.id)
    with pytest.raises(KeyError):
        withdraw_seed(db, "0000000001", user.id)
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM admin_actions").fetchone()[0] == 1


# -- Stored holes ---------------------------------------------------------------------------


def with_transforms(manifest: Manifest, transforms: dict[int, tuple[str, ...]]):
    """`manifest` with the transforms given for each hole index."""
    slots = tuple(
        replace(slot, transforms=transforms.get(index, ()))
        for index, slot in enumerate(manifest.course.holes)
    )
    return replace(manifest, course=replace(manifest.course, holes=slots))


def stored_hashes(db: Database) -> set[str]:
    with db.transaction() as conn:
        return {row[0] for row in conn.execute("SELECT content_hash FROM hole_data")}


#: what a build would hand over; any 18 holes do, since storing does not transform
HOLES = [synthetic_hole(number) for number in range(1, 19)]


def test_every_hole_is_stored_as_built_with_its_catalog_facts(db, manifest):
    catalog = Catalog.load()
    changed = with_transforms(manifest, {0: ("mirror@1",)})
    seed_id = insert_seed(db, changed, IPS, HOLES, catalog)
    holes = load_seed_holes(db, seed_id)
    assert [hole.position for hole in holes] == list(range(1, 19))
    assert stored_hashes(db) == {content_hash(hole) for hole in HOLES}
    for hole, slot, built in zip(holes, changed.course.holes, HOLES, strict=True):
        entry = catalog[slot.id]
        assert (hole.hole_id, hole.par) == (str(slot.id), slot.par)
        assert hole.pin_index == predict_hole(slot.wind_seed).pin_index
        assert hole.data_hash == content_hash(built)
        assert hole.distance == built.metadata["distance"]
        assert hole.author == entry.author
        assert isinstance(entry.source, RomSource)
        assert (hole.source_rom, hole.source_course, hole.source_hole) == (
            entry.source.rom,
            entry.source.course,
            entry.source.hole,
        )
    assert load_hole_data(db, holes[4].data_hash) == json.loads(
        canonical_json(HOLES[4])
    )
    assert seed_row(db, seed_id).required_roms == required_roms(changed, catalog)


def test_a_hole_that_is_not_vanilla_has_no_source(db, manifest):
    base = Catalog.load()[manifest.course.holes[0].id]
    derived = CatalogEntry(
        HoleId("jdharms/stored_short3"),
        DerivedSource(base, "derived/jdharms/stored_short3.json"),
        "1" * 64,
        3,
        190,
        "jdharms",
    )
    catalog = Catalog(1, {**Catalog.load().entries, derived.id: derived})
    first = replace(manifest.course.holes[0], id=derived.id)
    changed = replace(
        manifest,
        course=replace(manifest.course, holes=(first, *manifest.course.holes[1:])),
    )
    hole = load_seed_holes(db, insert_seed(db, changed, IPS, HOLES, catalog))[0]
    assert (hole.author, hole.source_rom, hole.source_course, hole.source_hole) == (
        "jdharms",
        None,
        None,
        None,
    )


def test_a_hole_two_seeds_share_is_stored_once(db, manifest):
    first = store_seed(db, manifest, IPS)
    second = store_seed(db, manifest, IPS)
    assert len(stored_hashes(db)) == 18
    assert [hole.data_hash for hole in load_seed_holes(db, first)] == [
        hole.data_hash for hole in load_seed_holes(db, second)
    ]


def test_a_seed_needs_the_holes_its_build_made(db, manifest):
    with pytest.raises(MissingHolesError, match="got 17"):
        insert_seed(db, manifest, IPS, HOLES[:17], Catalog.load())
    assert counts(db) == (0, 0)


def test_a_stored_hole_is_checked_against_its_hash(db, manifest):
    seed_id = store_seed(db, manifest, IPS)
    digest = load_seed_holes(db, seed_id)[0].data_hash
    other = zlib.compress(canonical_json(HOLES[1]))
    with db.transaction() as conn:
        conn.execute(
            "UPDATE hole_data SET data = ? WHERE content_hash = ?", (other, digest)
        )
    with pytest.raises(RuntimeError, match="has content hash"):
        load_hole_data(db, digest)
    with pytest.raises(LookupError, match="no stored hole"):
        load_hole_data(db, hashlib.sha256(b"").hexdigest())


def test_a_seed_that_does_not_exist_has_no_holes(db):
    assert load_seed_holes(db, "0000000001") == []


# -- Seeds stored before migration 7 ----------------------------------------------------------


def test_a_seed_stored_before_its_holes_were_is_found_and_not_shown(db, manifest):
    filled = store_seed(db, manifest, IPS)
    seed_id = store_seed(db, manifest, IPS)
    unfill(db, seed_id)
    assert unfilled_seeds(db) == [seed_id]
    with pytest.raises(RuntimeError, match="golf-site-backfill from server-v4.1.3"):
        load_seed(db, seed_id)
    assert seed_row(db, filled).id == filled
