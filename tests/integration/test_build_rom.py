"""Integration: the two-stage build from a generated manifest, on the real vanilla ROM."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from golf.core import ips
from golf.core.patches import PatchStack, StackError
from golf.core.patches.course import CoursePatch
from golf.core.patches.extended_sram_defaults import BallSpin, SwingSpeed
from golf.core.patches.qr_credentials import PLACEHOLDERS, placeholder_offset
from golf.core.patches.scorecard_qr import SCORECARD_QR_PATCH
from golf.core.patches.sram_defaults import MAGIC_CHECK_ADDRS, Club, magic_bytes
from golf.qr import port
from golf.randomizer.build import (
    BUILD_VERSION,
    BuildError,
    PlayerOptions,
    build_unfinished,
    credentials_for,
    finish,
    finishing_steps,
    unfinished_steps,
)
from golf.randomizer.catalog import US_ROM, Catalog, HoleStore
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import (
    ClubRules,
    Settings,
    StoredManifest,
    standard_rules,
)
from golf.randomizer.transforms import apply_transforms
from tests.legacy_manifest import as_schema
from tests.new_save import new_save

ROOT = Path(__file__).resolve().parents[2]
ROM_PATH = ROOT / "nes_open_us.nes"
HEADER = 0x10

pytestmark = pytest.mark.skipif(
    not ROM_PATH.exists(), reason=f"{ROM_PATH.name} not present"
)

UNFINISHED_ORDER = [
    "wram_expansion",
    "multi_bank_lookup",
    "course_mirrors",
    "course",
    "seeded_wind",
    "wind_anchors",
    "wind_fix",
    "music_import",
    "mercy_tap_in",
    "green_shortcut",
    "round_stats",
    "scorecard_qr",
    "signpost_random_banner",
    "scorecard_course_name",
    "menu_trim",
    "extended_sram_defaults",
]

OPTIONS = PlayerOptions(
    "LUIGI",
    frozenset({Club.W1, Club.W3, Club.I5, Club.PW, Club.SW}),
    bgm=False,
    swing=SwingSpeed.FAST,
    putt=SwingSpeed.SLOW,
    spin=BallSpin.BACK1,
)
#: what finish ABI 1 can write: no swing, putt or spin defaults
ABI_1_OPTIONS = replace(
    OPTIONS, swing=SwingSpeed.OFF, putt=SwingSpeed.OFF, spin=BallSpin.OFF
)
CREDENTIALS = credentials_for(
    839299365868340223, 0xDEADBEEF, (b"\x11" * 8, b"\x22" * 8)
)


@pytest.fixture(scope="module")
def vanilla() -> bytes:
    return ROM_PATH.read_bytes()


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load()


@pytest.fixture(scope="module")
def store(vanilla_courses) -> HoleStore:
    return HoleStore(vanilla_courses)


@pytest.fixture(scope="module")
def curation() -> CurationSnapshot:
    return CurationSnapshot.load()


@pytest.fixture(scope="module")
def jp_manifest(catalog, curation, vanilla_jp_courses):
    """Drawn with no expert cap, like `nes_manifest`, so the golden hashes below move
    with the build and not with the default expert cap."""
    settings = Settings(
        prng_seed="build-stages-jp-1",
        music="jp_france",
        constraints=standard_rules(72, None),
    )
    return generate(catalog, curation, settings)


@pytest.fixture(scope="module")
def nes_manifest(catalog, curation):
    settings = Settings(
        prng_seed="build-stages-nes",
        sources=frozenset({US_ROM}),
        music="nes_us",
        mercy_point=None,
        constraints=standard_rules(72, None),
    )
    return generate(catalog, curation, settings)


@pytest.fixture(scope="module")
def unfinished(jp_manifest, catalog, store, vanilla):
    return build_unfinished(jp_manifest, catalog, store, vanilla)


@pytest.fixture(scope="module")
def signed_in(jp_manifest, vanilla, unfinished):
    return finish(jp_manifest, vanilla, unfinished.ips, OPTIONS, CREDENTIALS)


@pytest.fixture(scope="module")
def guest(jp_manifest, vanilla, unfinished):
    return finish(jp_manifest, vanilla, unfinished.ips, OPTIONS)


def file_offsets(regions) -> set[int]:
    return {HEADER + i for start, end in regions for i in range(start, end)}


def prg_bytes(regions) -> set[int]:
    return {i for start, end in regions for i in range(start, end)}


def changed(before: bytes, after: bytes) -> set[int]:
    return {i for i in range(len(before)) if before[i] != after[i]}


# -- Unfinished ------------------------------------------------------------------------------


def test_the_unfinished_rom_runs_every_step_in_order(unfinished):
    assert list(unfinished.regions) == UNFINISHED_ORDER
    for name, regions in unfinished.regions.items():
        assert regions, f"{name} wrote nothing"


def test_the_unfinished_ips_rebuilds_the_rom(unfinished, vanilla):
    assert ips.apply(vanilla, unfinished.ips) == unfinished.rom


def test_the_unfinished_build_is_deterministic(
    unfinished, jp_manifest, catalog, store, vanilla
):
    assert build_unfinished(jp_manifest, catalog, store, vanilla).ips == unfinished.ips


def test_build_version_six_golden_unfinished_ips_hashes(
    unfinished, jp_manifest, nes_manifest, catalog, store, vanilla
):
    assert BUILD_VERSION == 6
    assert any(str(slot.id) == "jp_france/18" for slot in jp_manifest.course.holes)
    nes = build_unfinished(nes_manifest, catalog, store, vanilla)
    assert {
        "jp_france_18": hashlib.sha256(unfinished.ips).hexdigest(),
        "nes_only": hashlib.sha256(nes.ips).hexdigest(),
    } == {
        "jp_france_18": "6f45b2a7508dee316cc7b71efe01b139c5f08a1a3d5e9cbe6f4d8c60089e9077",
        "nes_only": "0191e20fb5595358f28bcf63ee5f38e83358ab848c7f832115745d7e15461ad1",
    }


def test_the_unfinished_rom_holds_the_placeholder_fill(unfinished):
    for _, symbol, length in PLACEHOLDERS:
        start = HEADER + placeholder_offset(symbol)
        assert (
            unfinished.rom[start : start + length] == bytes([port.PATCH_FILL]) * length
        )


def test_a_nes_open_seed_repoints_the_theme_and_can_leave_out_mercy(
    nes_manifest, catalog, store, vanilla
):
    names = [
        step.name for step in unfinished_steps(nes_manifest, catalog, store, vanilla)
    ]
    assert "course_theme" in names and "music_import" not in names
    assert "mercy_tap_in" not in names
    build = build_unfinished(nes_manifest, catalog, store, vanilla)
    table = HEADER + 0x3C000 + (0xDA14 - 0xC000)
    assert build.rom[table : table + 3] == b"\x02\x02\x02"


def test_a_seed_with_transforms_builds_its_transformed_holes(
    nes_manifest, catalog, store, vanilla
):
    styles = ("hazards@1", "hazards-weighted@1")
    slots = tuple(
        replace(slot, transforms=("mirror@1", f"{styles[i % 2]}:{i}"))
        for i, slot in enumerate(nes_manifest.course.holes)
    )
    manifest = replace(nes_manifest, course=replace(nes_manifest.course, holes=slots))
    steps = unfinished_steps(manifest, catalog, store, vanilla)
    course = next(step for step in steps if isinstance(step, CoursePatch))
    for slot, hole in zip(slots, course.holes, strict=True):
        expected = apply_transforms(store.load(catalog[slot.id]), slot.transforms)
        assert hole.to_dict() == expected.to_dict()
    build = build_unfinished(manifest, catalog, store, vanilla)
    assert build.ips != build_unfinished(nes_manifest, catalog, store, vanilla).ips
    assert [hole.to_dict() for hole in build.holes] == [
        hole.to_dict() for hole in course.holes
    ]


def club_house(steps) -> list[str]:
    menu_trim = next(step for step in steps if step.name == "menu_trim")
    options = next(
        sub for sub in menu_trim.patches if sub.name == "menu_trim_club_house_options"
    )
    return [f"{options.patched[0]} entries"]


def test_only_a_seed_with_club_rules_drops_choose_clubs(
    jp_manifest, catalog, store, vanilla
):
    assert jp_manifest.course.clubs == ClubRules()
    strict = replace(
        jp_manifest, course=replace(jp_manifest.course, clubs=ClubRules(max=10))
    )
    open_steps = unfinished_steps(jp_manifest, catalog, store, vanilla)
    strict_steps = unfinished_steps(strict, catalog, store, vanilla)
    assert club_house(open_steps) == ["5 entries"]
    assert club_house(strict_steps) == ["4 entries"]


def test_the_base_must_be_vanilla(jp_manifest, catalog, store, unfinished):
    with pytest.raises(BuildError, match="SHA-1"):
        build_unfinished(jp_manifest, catalog, store, unfinished.rom)


# -- Finished --------------------------------------------------------------------------------


def test_signed_in_finishing_changes_only_defaults_and_credentials(
    signed_in, unfinished, vanilla
):
    assert list(signed_in.regions) == [
        "sram_defaults",
        "qr_credentials",
    ]
    allowed = set().union(*(file_offsets(r) for r in signed_in.regions.values()))
    assert changed(unfinished.rom, signed_in.rom) <= allowed
    assert ips.apply(vanilla, signed_in.ips) == signed_in.rom

    values = {
        "seed_id": CREDENTIALS.seed_id,
        "player_ids": b"".join(CREDENTIALS.player_ids),
        "mac_keys": b"".join(CREDENTIALS.keys),
    }
    for suffix, symbol, length in PLACEHOLDERS:
        start = HEADER + placeholder_offset(symbol)
        assert signed_in.rom[start : start + length] == values[suffix]


@pytest.fixture(scope="module")
def abi_1_unfinished(jp_manifest, catalog, store, vanilla) -> bytes:
    """An unfinished IPS exposing finish ABI 1: today's stack without the options routine."""
    steps = [
        step
        for step in unfinished_steps(jp_manifest, catalog, store, vanilla)
        if step.name != "extended_sram_defaults"
    ]
    return PatchStack(steps).ips(vanilla)


def legacy(manifest) -> StoredManifest:
    """`manifest` as the randomizer 1.0 site stored it: schema 1, finish ABI 1."""
    return StoredManifest.from_json(as_schema(manifest.to_json(), 1))


def test_a_stored_abi_one_unfinished_ips_can_still_be_finished(
    jp_manifest, vanilla, abi_1_unfinished
):
    finished = finish(legacy(jp_manifest), vanilla, abi_1_unfinished, ABI_1_OPTIONS)
    assert list(finished.regions) == ["sram_defaults", "qr_disable"]
    sram = new_save(finished.rom)
    assert sram[0x0F98:0x0FB0] == bytes([0x00]) + bytes([0xFF] * 23)
    assert sram[0x0004:0x000E] == b"LUIGI     "


def test_an_abi_one_seed_refuses_options_it_cannot_write(
    jp_manifest, vanilla, abi_1_unfinished
):
    with pytest.raises(BuildError, match="cannot set swing, putt or spin"):
        finish(legacy(jp_manifest), vanilla, abi_1_unfinished, OPTIONS)


def test_a_finished_rom_starts_a_new_save_with_the_players_options(
    signed_in, jp_manifest
):
    sram = new_save(signed_in.rom)
    assert sram[0x0F98:0x0F9C] == bytes([0x00, 0x02, 0x00, 0x03])
    assert sram[0x0F9C:0x0FB0] == bytes([0xFF] * 20)
    assert sram[0x0004:0x000E] == b"LUIGI     "
    assert sram[0x0001:0x0003] == magic_bytes(jp_manifest.course.sram_magic)


def test_guest_finishing_changes_only_defaults_and_the_splice(
    guest, unfinished, vanilla
):
    assert list(guest.regions) == [
        "sram_defaults",
        "qr_disable",
    ]
    splice = HEADER + SCORECARD_QR_PATCH.splice_offset
    allowed = file_offsets(guest.regions["sram_defaults"]) | {splice, splice + 1}
    assert changed(unfinished.rom, guest.rom) <= allowed
    assert guest.rom[splice : splice + 2] == vanilla[splice : splice + 2]
    assert ips.apply(vanilla, guest.ips) == guest.rom


def test_finishing_writes_the_seeds_sram_magic(signed_in, jp_manifest):
    magic = jp_manifest.course.sram_magic
    for index, addr in enumerate(MAGIC_CHECK_ADDRS):
        operand = HEADER + 9 * 0x4000 + (addr - 0x8000)
        assert signed_in.rom[operand] == magic_bytes(magic)[index]


@pytest.mark.parametrize("flavor", ["signed_in", "guest"])
def test_the_stages_overlap_only_at_the_placeholders(flavor, request, unfinished):
    finished = request.getfixturevalue(flavor)
    unfinished_bytes = set().union(
        *(prg_bytes(regions) for regions in unfinished.regions.values())
    )
    finishing_bytes = set().union(
        *(prg_bytes(regions) for regions in finished.regions.values())
    )
    overlap = unfinished_bytes & finishing_bytes
    qr = prg_bytes(unfinished.regions["scorecard_qr"])
    options = prg_bytes(unfinished.regions["extended_sram_defaults"])
    assert overlap & qr, (
        "the QR finishing patch should rewrite bytes scorecard_qr wrote"
    )
    assert overlap & options, "the options table should be rewritten"
    assert overlap <= qr | options


@pytest.mark.parametrize(
    "credentials, finisher", [(CREDENTIALS, "qr_credentials"), (None, "qr_disable")]
)
def test_one_stack_of_both_stages_is_refused_at_the_first_placeholder(
    credentials, finisher, jp_manifest, catalog, store, vanilla
):
    steps = unfinished_steps(jp_manifest, catalog, store, vanilla)
    steps += finishing_steps(OPTIONS, jp_manifest.course.sram_magic, credentials)
    with pytest.raises(
        StackError, match="step 'sram_defaults'.*'extended_sram_defaults'"
    ):
        PatchStack(steps).build(vanilla)
    without_defaults = [step for step in steps if step.name != "sram_defaults"]
    with pytest.raises(StackError, match=f"step '{finisher}'.*scorecard_qr"):
        PatchStack(without_defaults).build(vanilla)


def test_finishing_refuses_a_bag_the_seed_forbids(jp_manifest, vanilla, unfinished):
    strict = replace(
        jp_manifest,
        course=replace(
            jp_manifest.course, clubs=ClubRules(banned=frozenset({Club.W1}))
        ),
    )
    with pytest.raises(BuildError, match="bans 1W"):
        finish(strict, vanilla, unfinished.ips, OPTIONS)


def test_finishing_refuses_a_non_vanilla_base(jp_manifest, unfinished):
    with pytest.raises(BuildError, match="SHA-1"):
        finish(jp_manifest, unfinished.rom, unfinished.ips, OPTIONS)
