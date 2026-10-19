"""Hosted ROMs: claiming slots in order, releasing them, downloads, expiry and withdrawal."""

import json

import pytest

from golf.core.patches.sram_defaults import Club
from golf.randomizer.catalog import Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Settings
from server import audit
from server.db import Database
from server.forms import SavedSettings
from server.hosting import (
    EXPIRED,
    FULL,
    TOKEN_LENGTH,
    UNKNOWN,
    WITHDRAWN,
    Claim,
    HostingError,
    claim_slot,
    create_hosted_rom,
    expiry,
    hosted_roms_for_host,
    load_by_token,
    load_hosted_rom,
    mark_downloaded,
    recent_seeds,
    release_slot,
    save_hosted_options,
)
from server.seeds import withdraw_seed
from server.users import sign_in
from tests.stored_seeds import store_seed

NOW = "2026-10-21T18:00:00Z"


@pytest.fixture(scope="module")
def manifest():
    return generate(
        Catalog.load(), CurationSnapshot.load(), Settings(prng_seed="hosting")
    )


@pytest.fixture
def db():
    database = Database(":memory:")
    database.migrate()
    yield database
    database.close()


@pytest.fixture
def seed_id(db, manifest):
    return store_seed(db, manifest)


def user(db: Database, name: str) -> int:
    return sign_in(db, f"dev:{name}", name, name.title(), None).id


@pytest.fixture
def host(db):
    return user(db, "host")


@pytest.fixture
def hosted(db, seed_id, host):
    return create_hosted_rom(db, seed_id, host, now=NOW)


def actions(db: Database) -> list[tuple]:
    with db.transaction() as conn:
        return [
            (row["admin_id"], row["action"], row["target_type"], row["target_id"])
            + (json.loads(row["detail"]),)
            for row in conn.execute("SELECT * FROM admin_actions ORDER BY id")
        ]


def test_a_new_hosted_rom_has_both_slots_free_and_expires_later(
    db, seed_id, host, hosted
):
    assert (hosted.seed_id, hosted.host_id) == (seed_id, host)
    assert hosted.created_at == NOW
    assert hosted.expires_at == "2026-10-22T06:00:00Z" == expiry(NOW)
    assert hosted.claimants == (None, None)
    assert [slot.slot for slot in hosted.slots] == [0, 1]
    assert hosted.options is None
    assert hosted.downloaded_at is None
    assert not hosted.withdrawn
    assert len(hosted.token) == TOKEN_LENGTH == 22
    assert load_hosted_rom(db, hosted.public_id) == hosted
    assert load_by_token(db, hosted.token) == hosted
    assert actions(db) == [
        (host, audit.HOST, audit.HOSTED_ROM, hosted.public_id, {"seed": seed_id})
    ]


def test_hosting_refuses_a_missing_or_withdrawn_seed(db, seed_id, host):
    with pytest.raises(HostingError) as missing:
        create_hosted_rom(db, "0000000000", host)
    assert missing.value.reason == UNKNOWN
    withdraw_seed(db, seed_id, host)
    with pytest.raises(HostingError) as withdrawn:
        create_hosted_rom(db, seed_id, host)
    assert withdrawn.value.reason == WITHDRAWN


def test_claims_fill_player_one_then_player_two(db, hosted):
    alice, bob = user(db, "alice"), user(db, "bob")
    assert claim_slot(db, hosted.token, alice, now=NOW) == Claim(0, new=True)
    assert claim_slot(db, hosted.token, bob, now=NOW) == Claim(1, new=True)
    claimed = load_hosted_rom(db, hosted.public_id)
    assert claimed is not None
    assert claimed.claimants == (alice, bob)
    assert [slot.user_name for slot in claimed.slots] == ["Alice", "Bob"]
    assert [slot.claimed_at for slot in claimed.slots] == [NOW, NOW]


def test_claiming_again_finds_the_same_slot(db, hosted):
    alice = user(db, "alice")
    claim_slot(db, hosted.token, alice, now=NOW)
    assert claim_slot(db, hosted.token, alice, now="2026-10-21T19:00:00Z") == Claim(
        0, new=False
    )
    claimed = load_hosted_rom(db, hosted.public_id)
    assert claimed is not None
    assert claimed.claimants == (alice, None)
    assert claimed.slots[0].claimed_at == NOW


def test_a_full_hosted_rom_refuses_a_third_player(db, hosted):
    for name in ("alice", "bob"):
        claim_slot(db, hosted.token, user(db, name))
    with pytest.raises(HostingError) as full:
        claim_slot(db, hosted.token, user(db, "carol"))
    assert full.value.reason == FULL


def test_releasing_player_one_lets_the_next_claim_take_it(db, host, hosted):
    alice, bob, carol = user(db, "alice"), user(db, "bob"), user(db, "carol")
    claim_slot(db, hosted.token, alice)
    claim_slot(db, hosted.token, bob)
    release_slot(db, hosted.public_id, 0, host, now=NOW)
    released = load_hosted_rom(db, hosted.public_id)
    assert released is not None
    assert released.claimants == (None, bob)
    assert released.slots[0].claimed_at is None
    assert claim_slot(db, hosted.token, carol) == Claim(0, new=True)
    assert actions(db)[-1] == (
        host,
        audit.RELEASE,
        audit.HOSTED_ROM,
        hosted.public_id,
        {"slot": 0, "user_id": alice},
    )


def test_a_download_is_recorded_and_a_release_clears_it(db, host, hosted):
    alice = user(db, "alice")
    claim_slot(db, hosted.token, alice)
    mark_downloaded(db, hosted.public_id, host, (alice, None), now=NOW)
    downloaded = load_hosted_rom(db, hosted.public_id)
    assert downloaded is not None
    assert downloaded.downloaded_at == NOW
    assert actions(db)[-1] == (
        host,
        audit.HOSTED_DOWNLOAD,
        audit.HOSTED_ROM,
        hosted.public_id,
        {"claimants": [alice, None]},
    )

    # releasing the free slot changes no claim, but shows the claim code again
    release_slot(db, hosted.public_id, 1, host)
    released = load_hosted_rom(db, hosted.public_id)
    assert released is not None
    assert released.downloaded_at is None
    assert released.claimants == (alice, None)


def test_two_hosted_roms_of_one_seed_are_kept_apart(db, seed_id, host, hosted):
    alice, bob = user(db, "alice"), user(db, "bob")
    second = create_hosted_rom(db, seed_id, host)
    assert second.token != hosted.token
    assert second.public_id != hosted.public_id
    claim_slot(db, hosted.token, alice)
    assert claim_slot(db, second.token, bob) == Claim(0, new=True)
    # a player can be in more than one pair
    assert claim_slot(db, second.token, alice) == Claim(1, new=True)
    first = load_hosted_rom(db, hosted.public_id)
    assert first is not None and first.claimants == (alice, None)


def test_a_host_lists_only_their_own_hosted_roms_newest_first(
    db, seed_id, host, hosted
):
    other = user(db, "other")
    create_hosted_rom(db, seed_id, other)
    newer = create_hosted_rom(db, seed_id, host)
    assert [listed.public_id for listed in hosted_roms_for_host(db, host)] == [
        newer.public_id,
        hosted.public_id,
    ]


def test_an_expired_hosted_rom_refuses_claims(db, hosted):
    alice = user(db, "alice")
    assert not hosted.expired("2026-10-22T05:59:59Z")
    assert hosted.expired(hosted.expires_at)
    with pytest.raises(HostingError) as expired:
        claim_slot(db, hosted.token, alice, now=hosted.expires_at)
    assert expired.value.reason == EXPIRED
    assert claim_slot(db, hosted.token, alice, now="2026-10-22T05:59:59Z").new


def test_a_withdrawn_seed_refuses_claims(db, seed_id, host, hosted):
    withdraw_seed(db, seed_id, host)
    withdrawn = load_hosted_rom(db, hosted.public_id)
    assert withdrawn is not None and withdrawn.withdrawn
    with pytest.raises(HostingError) as refused:
        claim_slot(db, hosted.token, user(db, "alice"), now=NOW)
    assert refused.value.reason == WITHDRAWN


def test_an_unknown_token_or_id_finds_nothing(db, hosted):
    with pytest.raises(HostingError) as unknown:
        claim_slot(db, "not-a-token", user(db, "alice"))
    assert unknown.value.reason == UNKNOWN
    assert load_by_token(db, "not-a-token") is None
    assert load_hosted_rom(db, "0000000000") is None
    assert load_hosted_rom(db, "not an id") is None


def test_the_options_are_stored_on_the_hosted_rom(db, hosted):
    options = SavedSettings(player_name="ALICE", clubs=frozenset({Club.W1, Club.PT}))
    save_hosted_options(db, hosted.public_id, options)
    saved = load_hosted_rom(db, hosted.public_id)
    assert saved is not None and saved.options == options
    with pytest.raises(KeyError):
        save_hosted_options(db, "0000000000", options)


def test_releasing_refuses_a_missing_hosted_rom_or_slot(db, host, hosted):
    with pytest.raises(KeyError):
        release_slot(db, "0000000000", 0, host)
    with pytest.raises(ValueError):
        release_slot(db, hosted.public_id, 2, host)
    with pytest.raises(KeyError):
        mark_downloaded(db, "0000000000", host, (None, None))


def test_the_token_never_reaches_a_repr(db, hosted):
    claim_slot(db, hosted.token, user(db, "alice"))
    loaded = load_hosted_rom(db, hosted.public_id)
    assert loaded is not None
    assert hosted.token not in repr(loaded)
    assert hosted.token not in repr(hosted_roms_for_host(db, hosted.host_id))


def test_recent_seeds_are_the_sites_newest_active_seeds(db, manifest, host):
    other = user(db, "other")
    older = store_seed(db, manifest, now="2026-10-20T10:00:00Z", creator_id=host)
    newer = store_seed(db, manifest, now="2026-10-21T10:00:00Z", creator_id=other)
    anonymous = store_seed(db, manifest, now="2026-10-21T11:00:00Z")
    withdrawn = store_seed(db, manifest, now="2026-10-21T12:00:00Z")
    withdraw_seed(db, withdrawn, host)
    recent = recent_seeds(db)
    assert [seed.id for seed in recent] == [anonymous, newer, older]
    assert [seed.creator_name for seed in recent] == [None, "Other", "Host"]
    assert recent[0].magic_words == manifest.course.magic_words
    assert recent[0].par == manifest.course.par
    assert [seed.id for seed in recent_seeds(db, limit=1)] == [anonymous]
