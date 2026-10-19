"""Unit tests for the two-stage build's pieces that need no ROM."""

from dataclasses import replace

import pytest

from golf.core import rom_utils
from golf.core.patches import (
    BytePatch,
    CompositePatch,
    QrCredentials,
    course_theme_patch,
)
from golf.core.patches.course_theme import VANILLA_COURSE_BGM
from golf.core.patches.extended_sram_defaults import (
    EXTENDED_SRAM_DEFAULTS_PATCH,
    BallSpin,
    SwingSpeed,
)
from golf.core.patches.music_import import MusicImportPatch
from golf.core.patches.qr_credentials import PLACEHOLDERS, placeholder_offset
from golf.core.patches.scorecard_qr import (
    QR_DISABLE_PATCH,
    SCORECARD_QR_PATCH,
    TRAMPOLINE_ENTRY_BYTES,
)
from golf.core.patches.sram_defaults import Club, magic_bytes
from golf.qr import payload, port
from golf.randomizer.build import (
    FINISH_ABI_VERSION,
    BuildError,
    PlayerOptions,
    build_unfinished,
    clubs_from_labels,
    credentials_for,
    credentials_for_slots,
    finish,
    finishing_steps,
    music_step,
    player_id_bytes,
    seed_id_bytes,
)
from golf.randomizer.catalog import JP_ROM, US_ROM, Catalog, HoleStore
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import (
    LEGACY_BUILD_VERSION,
    LEGACY_FINISH_ABI_VERSION,
    ClubRules,
    Settings,
)
from golf.randomizer.music import TRACKS

KEYS = (bytes(range(1, 9)), bytes(range(9, 17)))


def test_historical_build_versions_are_refused_before_building():
    current = generate(
        Catalog.load(), CurationSnapshot.load(), Settings(prng_seed="old-build")
    )
    legacy = replace(
        current,
        build_version=LEGACY_BUILD_VERSION,
        finish_abi_version=LEGACY_FINISH_ABI_VERSION,
    )
    with pytest.raises(BuildError, match="requires unfinished build version 1"):
        build_unfinished(legacy, Catalog.load(), HoleStore(), b"")


def test_a_build_refuses_to_mislabel_the_finish_abi_it_produces():
    current = generate(
        Catalog.load(), CurationSnapshot.load(), Settings(prng_seed="wrong-abi")
    )
    mislabeled = replace(current, finish_abi_version=FINISH_ABI_VERSION + 1)
    with pytest.raises(BuildError, match=f"produces ABI {FINISH_ABI_VERSION}"):
        build_unfinished(mislabeled, Catalog.load(), HoleStore(), b"")


def test_finishing_refuses_an_unsupported_artifact_abi_before_reading_the_rom():
    current = generate(
        Catalog.load(), CurationSnapshot.load(), Settings(prng_seed="future-abi")
    )
    future = replace(current, finish_abi_version=FINISH_ABI_VERSION + 1)
    with pytest.raises(
        BuildError, match=f"cannot finish artifact ABI {FINISH_ABI_VERSION + 1}"
    ):
        finish(future, b"", b"", options())


def qr_contract() -> dict:
    """
    The QR half of the finish ABI, which ABIs 1 and 2 share (ADR 0009).

    The payload protocol is not part of it: the unfinished image decides what the
    ROM sends, finishing writes none of it, and the server accepts every version
    ever released. Nor is the far call's target in the trampoline, which finishing
    never checks, so the routine it reaches may move between build versions.
    """
    trampoline = SCORECARD_QR_PATCH.trampoline
    return {
        "credential_lengths": (
            payload.SEED_ID_LEN,
            payload.PLAYER_ID_LEN,
            payload.KEY_LEN,
        ),
        "placeholder_fill": port.PATCH_FILL,
        "placeholders": tuple(
            (suffix, placeholder_offset(symbol), length)
            for suffix, symbol, length in PLACEHOLDERS
        ),
        "qr_identity": (
            SCORECARD_QR_PATCH.trampoline_offset,
            bytes(
                byte
                for i, byte in enumerate(trampoline)
                if i not in TRAMPOLINE_ENTRY_BYTES
            ),
            SCORECARD_QR_PATCH.splice_offset,
            SCORECARD_QR_PATCH.splice_bytes,
        ),
        "guest_disable": (
            QR_DISABLE_PATCH.prg_offset,
            QR_DISABLE_PATCH.original,
            QR_DISABLE_PATCH.patched,
        ),
    }


QR_CONTRACT = {
    "credential_lengths": (8, 4, 8),
    "placeholder_fill": 0,
    "placeholders": (
        ("seed_id", 0x8E5F, 8),
        ("player_ids", 0x8E67, 8),
        ("mac_keys", 0x8E6F, 16),
    ),
    "qr_identity": (
        0x3DCBD,
        bytes.fromhex("20ba852072d30260"),  # less the entry point's two bytes
        0x3452E,
        bytes.fromhex("bddc"),
    ),
    "guest_disable": (0x3452E, bytes.fromhex("bddc"), bytes.fromhex("ba85")),
}

SRAM_NAME_AND_CLUBS = (
    ("sram_defaults_player_name", 0x26D5B, b"MARIO     ", 10),
    (
        "sram_defaults_clubs",
        0x26E23,
        bytes.fromhex("00010205060708090a0b0c0d0e0f"),
        14,
    ),
)
SRAM_MAGIC = (
    ("sram_defaults_magic_check_6001", 0x26CC0, b"5", 1),
    ("sram_defaults_magic_check_6002", 0x26CC7, b"S", 1),
    ("sram_defaults_magic_write_6001", 0x26D51, b"5", 1),
    ("sram_defaults_magic_write_6002", 0x26D56, b"S", 1),
)


def consumed(steps) -> tuple:
    """What finishing steps read and write, apart from the QR credentials."""
    return tuple(
        (patch.name, patch.prg_offset, patch.original, len(patch.patched))
        for step in steps
        if step.name not in ("qr_credentials", "qr_disable")
        for patch in step.patches
    )


def test_finish_abi_one_contract_is_stable_without_rom_or_course_data():
    """Changing a consumed location, preimage, width or protocol requires an ABI decision."""
    steps = finishing_steps(options(bgm=False), 0x5247, None, abi=1)
    assert {**qr_contract(), "sram": consumed(steps)} == {
        **QR_CONTRACT,
        "sram": (
            *SRAM_NAME_AND_CLUBS,
            ("sram_defaults_bgm_off", 0x26D4E, b"\x10", 1),
            *SRAM_MAGIC,
        ),
    }


def test_finish_abi_two_contract_is_stable_without_rom_or_course_data():
    """ABI 2 moves BGM into the new-save options table that the unfinished build installs."""
    all_options = options(
        bgm=False, swing=SwingSpeed.FAST, putt=SwingSpeed.SLOW, spin=BallSpin.BACK2
    )
    steps = finishing_steps(all_options, 0x5247, None, abi=2)
    assert FINISH_ABI_VERSION == 2
    assert {
        **qr_contract(),
        "sram": consumed(steps),
        "extended_sram_defaults": tuple(
            (patch.prg_offset, patch.patched)
            for patch in EXTENDED_SRAM_DEFAULTS_PATCH.patches
        ),
    } == {
        **QR_CONTRACT,
        "sram": (
            *SRAM_NAME_AND_CLUBS,
            *SRAM_MAGIC,
            ("sram_defaults_option_table", 0x27531, b"\xff\xff\xff\xff", 4),
        ),
        "extended_sram_defaults": (
            (
                0x27519,
                bytes.fromhex(
                    "a217a9ff9d986fca10faa203bd31b59d986fca10f74c50adffffffff"
                ),
            ),
            (0x26D46, bytes.fromhex("4c19b5eaeaeaeaeaeaea")),
        ),
    }


def options(**overrides) -> PlayerOptions:
    return PlayerOptions(
        **({"player_name": "LUIGI", "clubs": {Club.W1, Club.PW}} | overrides)
    )


class TestPlayerOptions:
    def test_the_putter_is_added(self):
        assert options().clubs == {Club.W1, Club.PW, Club.PT}

    def test_valid_options_pass_open_rules(self):
        options().check(ClubRules())

    def test_over_the_max(self):
        with pytest.raises(BuildError, match="at most 2 clubs"):
            options().check(ClubRules(max=2))

    def test_a_banned_club(self):
        with pytest.raises(BuildError, match="bans 1W"):
            options().check(ClubRules(banned=frozenset({Club.W1, Club.SW})))

    def test_a_required_bag_must_match(self):
        options().check(ClubRules(required_bag=frozenset({Club.W1, Club.PW})))
        with pytest.raises(BuildError, match="requires the bag"):
            options().check(ClubRules(required_bag=frozenset({Club.W1})))

    @pytest.mark.parametrize(
        "overrides, message",
        [
            (dict(player_name="LUIGI!"), "cannot store"),
            (dict(player_name=""), "1-10 characters"),
            (dict(player_name="ABCDEFGHIJK"), "1-10 characters"),
            (dict(bgm="on"), "bgm"),
            (dict(swing=3), "swing must be a SwingSpeed"),
            (dict(putt=True), "putt must be a SwingSpeed"),
            (dict(spin=5), "spin must be a BallSpin"),
            (dict(spin="BACK2"), "spin must be a BallSpin"),
        ],
    )
    def test_rejects_options_no_rom_can_hold(self, overrides, message):
        with pytest.raises(BuildError, match=message):
            options(**overrides)

    def test_option_defaults_are_off_and_take_plain_values(self):
        assert not options().has_option_defaults
        chosen = options(swing=2, putt=0, spin=4)
        assert (chosen.swing, chosen.putt, chosen.spin) == (
            SwingSpeed.FAST,
            SwingSpeed.SLOW,
            BallSpin.BACK2,
        )
        assert chosen.has_option_defaults

    def test_clubs_from_labels(self):
        assert clubs_from_labels(["1w", "PW"]) == {Club.W1, Club.PW}
        with pytest.raises(BuildError, match="unknown club"):
            clubs_from_labels(["5W"])


class TestCredentials:
    def test_integers_become_big_endian_fields(self):
        credentials = credentials_for(0x0102030405060708, 0x0A0B0C0D, KEYS)
        assert credentials.seed_id == bytes.fromhex("0102030405060708")
        assert credentials.player_ids == (bytes.fromhex("0a0b0c0d"),) * 2
        assert credentials.keys == KEYS
        assert int.from_bytes(credentials.seed_id, "big") == 0x0102030405060708

    def test_field_lengths(self):
        assert len(seed_id_bytes(1)) == payload.SEED_ID_LEN
        assert len(player_id_bytes(1)) == payload.PLAYER_ID_LEN
        assert seed_id_bytes(62**10 - 1) == (62**10 - 1).to_bytes(8, "big")

    @pytest.mark.parametrize("value", [0, -1, 1 << 64, True, "1"])
    def test_rejects_seed_ids_the_server_rejects_or_the_field_cannot_hold(self, value):
        with pytest.raises(BuildError, match="qr_seed_id"):
            seed_id_bytes(value)

    @pytest.mark.parametrize("value", [0, -1, 1 << 32, False])
    def test_rejects_bad_player_ids(self, value):
        with pytest.raises(BuildError, match="player_id"):
            player_id_bytes(value)

    def test_rejects_bad_keys(self):
        with pytest.raises(BuildError, match="key"):
            credentials_for(1, 1, (b"short", KEYS[1]))

    def test_each_slot_carries_its_own_player_and_key(self):
        credentials = credentials_for_slots(7, (0x0A, KEYS[0]), (0x0B, KEYS[1]))
        assert credentials.seed_id == (7).to_bytes(8, "big")
        assert credentials.player_ids == (
            (0x0A).to_bytes(4, "big"),
            (0x0B).to_bytes(4, "big"),
        )
        assert credentials.keys == KEYS

    def test_one_player_in_both_slots_is_their_own_download(self):
        assert credentials_for_slots(
            7, (0x0A, KEYS[0]), (0x0A, KEYS[1])
        ) == credentials_for(7, 0x0A, KEYS)

    def test_rejects_a_bad_player_in_either_slot(self):
        with pytest.raises(BuildError, match="player_id"):
            credentials_for_slots(1, (1, KEYS[0]), (0, KEYS[1]))


class TestMusicStep:
    @pytest.mark.parametrize(
        "slug", [slug for slug, theme in TRACKS.items() if theme.rom == US_ROM]
    )
    def test_nes_open_themes_only_repoint_the_course_bgm_table(self, slug):
        step = music_step(slug)
        assert isinstance(step, BytePatch)
        assert step.name == "course_theme"
        assert step.patched == bytes([TRACKS[slug].music_id]) * 3

    @pytest.mark.parametrize(
        "slug", [slug for slug, theme in TRACKS.items() if theme.rom == JP_ROM]
    )
    def test_mario_open_themes_are_imported(self, slug):
        step = music_step(slug)
        assert isinstance(step, MusicImportPatch)
        assert step.track == TRACKS[slug].music_id


class TestCourseTheme:
    def test_writes_every_course_bgm_entry(self):
        patch = course_theme_patch(0x04)
        assert patch.prg_offset == rom_utils.cpu_to_prg_fixed(0xDA14)
        assert patch.original == VANILLA_COURSE_BGM
        assert patch.patched == b"\x04\x04\x04"

    @pytest.mark.parametrize("music_id", [0x01, 0x05, 0x0B])
    def test_only_us_course_themes(self, music_id):
        with pytest.raises(ValueError, match="not a US ROM course theme"):
            course_theme_patch(music_id)


class TestFinishingSteps:
    def test_signed_in(self):
        credentials = credentials_for(1, 2, KEYS)
        steps = finishing_steps(options(), 0x5247, credentials)
        assert [step.name for step in steps] == [
            "sram_defaults",
            "qr_credentials",
        ]

    def test_guest(self):
        assert [step.name for step in finishing_steps(options(), 0x5247, None)] == [
            "sram_defaults",
            "qr_disable",
        ]

    def test_abi_one_has_no_option_table(self):
        steps = finishing_steps(options(), 0x5247, None, abi=1)
        assert [step.name for step in steps] == ["sram_defaults", "qr_disable"]

    def test_the_seed_magic_is_written(self):
        (defaults, _) = finishing_steps(options(bgm=False), 0x5247, None)
        assert isinstance(defaults, CompositePatch)
        writes = {sub.name: sub.patched for sub in defaults.patches}
        assert writes["sram_defaults_magic_write_6001"] + writes[
            "sram_defaults_magic_write_6002"
        ] == magic_bytes(0x5247)
        assert writes["sram_defaults_magic_check_6001"] + writes[
            "sram_defaults_magic_check_6002"
        ] == magic_bytes(0x5247)
        assert writes["sram_defaults_player_name"] == b"LUIGI     "

    def test_abi_two_writes_every_option_into_the_table(self):
        chosen = options(
            bgm=False, swing=SwingSpeed.MEDIUM, putt=SwingSpeed.OFF, spin=BallSpin.TOP2
        )
        (defaults, _) = finishing_steps(chosen, 0x5247, None)
        assert isinstance(defaults, CompositePatch)
        writes = {sub.name: sub.patched for sub in defaults.patches}
        assert "sram_defaults_bgm_off" not in writes
        assert writes["sram_defaults_option_table"] == bytes([0x00, 0x01, 0xFF, 0x00])

    def test_abi_one_writes_bgm_with_the_loop_edit(self):
        (defaults, _) = finishing_steps(options(bgm=False), 0x5247, None, abi=1)
        assert isinstance(defaults, CompositePatch)
        assert "sram_defaults_bgm_off" in {sub.name for sub in defaults.patches}

    def test_abi_one_refuses_options_it_cannot_write(self):
        with pytest.raises(BuildError, match="cannot set swing, putt or spin"):
            finishing_steps(options(spin=BallSpin.NORMAL), 0x5247, None, abi=1)

    def test_an_unknown_abi_is_refused(self):
        with pytest.raises(BuildError, match="cannot finish artifact ABI 9"):
            finishing_steps(options(), 0x5247, None, abi=9)

    def test_credentials_type(self):
        assert isinstance(credentials_for(1, 1, KEYS), QrCredentials)
