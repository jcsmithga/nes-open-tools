"""Integration: the two-stage build from a generated manifest, on the real vanilla ROM."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from golf.algorithms import hazards
from golf.algorithms.mirror import mirror_hole
from golf.core import ips
from golf.core.patches import (
    PatchStack,
    StackError,
    clubhouse_roof,
    peach_dress,
    signpost_color,
)
from golf.core.patches.course import CoursePatch
from golf.core.patches.extended_sram_defaults import BallSpin, SwingSpeed
from golf.core.patches.qr_credentials import PLACEHOLDERS, placeholder_offset
from golf.core.patches.scorecard_qr import SCORECARD_QR_PATCH
from golf.core.patches.sram_defaults import MAGIC_CHECK_ADDRS, Club, magic_bytes
from golf.core.patches.stroke_indexes import LENGTH as HANDICAP_LENGTH
from golf.core.patches.stroke_indexes import PRG_OFFSET as HANDICAP_OFFSET
from golf.core.rom_utils import cpu_to_prg_switched
from golf.core.rom_writer import RomWriter
from golf.qr import port
from golf.randomizer.build import (
    BUILD_VERSION,
    BuildError,
    PlayerOptions,
    build_unfinished,
    credentials_for,
    finish,
    finishing_steps,
    intro_sky_step,
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
from tests.legacy_manifest import as_schema
from tests.new_save import new_save
from tests.stored_seeds import STROKE_INDEXES

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
    "swing_speed_hud",
    "hud_course_abbrev",
    "round_stats",
    "scorecard_qr",
    "signpost_random_banner",
    "signpost_color",
    "peach_dress",
    "clubhouse_roof",
    "course_intro_sky",
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


#: the courses the golden hashes below were recorded with. Generation draws from the
#: live catalog and curation, which move whenever a hole is published or curated
#: (ADR 0023), so the golden manifests hold their holes fixed and the hashes move with
#: the build alone.
JP_HOLES = (
    "jp_australia/11", "jp_hawaii/04", "nes_uk/08", "nes_japan/02", "nes_uk/16",
    "jp_hawaii/02", "jp_uk/10", "jp_australia/03", "jp_hawaii/15", "jp_hawaii/10",
    "jp_uk/13", "jp_australia/17", "jp_uk/03", "jp_france/06", "nes_japan/04",
    "jp_france/18", "jp_australia/10", "nes_uk/18",
)  # fmt: skip
NES_HOLES = (
    "nes_uk/05", "nes_us/10", "nes_us/08", "nes_us/13", "nes_us/04", "nes_us/17",
    "nes_us/11", "nes_japan/18", "nes_japan/09", "nes_japan/03", "nes_uk/04",
    "nes_japan/17", "nes_japan/13", "nes_us/03", "nes_japan/08", "nes_uk/07",
    "nes_uk/10", "nes_us/02",
)  # fmt: skip


def pinned(manifest, holes: tuple[str, ...], catalog: Catalog):
    """`manifest` with these holes on its slots, each slot's wind and transforms kept."""
    slots = tuple(
        replace(slot, id=catalog[hole].id, par=catalog[hole].par)
        for slot, hole in zip(manifest.course.holes, holes, strict=True)
    )
    return replace(manifest, course=replace(manifest.course, holes=slots))


@pytest.fixture(scope="module")
def jp_manifest(catalog, curation, vanilla_jp_courses):
    """Drawn with no expert cap, like `nes_manifest`, so the golden hashes below move
    with the build and not with the default expert cap."""
    settings = Settings(
        prng_seed="build-stages-jp-1",
        music="jp_france",
        constraints=standard_rules(None),
    )
    return pinned(generate(catalog, curation, settings), JP_HOLES, catalog)


@pytest.fixture(scope="module")
def nes_manifest(catalog, curation):
    settings = Settings(
        prng_seed="build-stages-nes",
        sources=frozenset({US_ROM}),
        music="nes_us",
        mercy_point=None,
        constraints=standard_rules(None),
    )
    return pinned(generate(catalog, curation, settings), NES_HOLES, catalog)


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


def test_build_version_nine_golden_unfinished_ips_hashes(
    unfinished, jp_manifest, nes_manifest, catalog, store, vanilla
):
    assert BUILD_VERSION == 9
    assert any(str(slot.id) == "jp_france/18" for slot in jp_manifest.course.holes)
    nes = build_unfinished(nes_manifest, catalog, store, vanilla)
    assert {
        "jp_france_18": hashlib.sha256(unfinished.ips).hexdigest(),
        "nes_only": hashlib.sha256(nes.ips).hexdigest(),
    } == {
        "jp_france_18": "815f48e0341fa1489eb5a88461d131dcc499d0a9a08d3ee9faa295624ed9e6b9",
        "nes_only": "9b78e3b78c72368161c85ffcd8a7cf7b66f60d9ac65005fddf90b080e2ca520d",
    }


def test_the_unfinished_rom_has_the_seeds_colors_and_the_sky(unfinished, jp_manifest):
    course = jp_manifest.course
    for addr, bank, color in (
        (peach_dress.DRESS_COLOR_ADDR, peach_dress.PALETTE_BANK, course.dress_color),
        (
            signpost_color.BANNER_COLOR_ADDR,
            signpost_color.PALETTE_BANK,
            course.signpost_color,
        ),
        (
            clubhouse_roof.ROOF_COLOR_ADDR,
            clubhouse_roof.PALETTE_BANK,
            course.roof_color,
        ),
        (
            clubhouse_roof.ROOF_ATTRIBUTE_ADDR,
            clubhouse_roof.NAMETABLE_BANK,
            clubhouse_roof.FIXED_ROOF_ATTRIBUTE,
        ),
    ):
        assert unfinished.rom[HEADER + cpu_to_prg_switched(addr, bank)] == color
    assert intro_sky_step().is_applied(RomWriter.from_bytes(unfinished.rom))


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
    # expected from the operations themselves, not `apply_transforms`, which would read
    # back what the build just put in the transform cache
    draws = {"hazards@1": hazards.uniform, "hazards-weighted@1": hazards.weighted}
    for i, (slot, hole) in enumerate(zip(slots, course.holes, strict=True)):
        flipped = mirror_hole(store.load(catalog[slot.id]))
        expected = hazards.redraw_hazards(flipped, i, draws[styles[i % 2]])
        assert hole.to_dict() == expected.to_dict()
    # every hole of this build is a cache hit, from the steps above
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


def handicap_table(rom: bytes) -> bytes:
    start = HEADER + HANDICAP_OFFSET
    return rom[start : start + HANDICAP_LENGTH]


def test_the_unfinished_rom_carries_each_holes_own_handicap(unfinished):
    assert handicap_table(unfinished.rom) == bytes(
        hole.metadata["handicap"] for hole in unfinished.holes
    )


@pytest.mark.parametrize("signed_in_too", [False, True], ids=["guest", "signed in"])
def test_a_rated_seed_finishes_with_its_stroke_indexes(
    signed_in_too, jp_manifest, unfinished, vanilla
):
    credentials = CREDENTIALS if signed_in_too else None
    finished = finish(
        jp_manifest,
        vanilla,
        unfinished.ips,
        OPTIONS,
        credentials,
        stroke_indexes=STROKE_INDEXES,
    )
    assert list(finished.regions) == [
        "sram_defaults",
        "stroke_indexes",
        "qr_credentials" if signed_in_too else "qr_disable",
    ]
    assert handicap_table(finished.rom) == bytes(STROKE_INDEXES)
    allowed = set().union(*(file_offsets(r) for r in finished.regions.values()))
    assert changed(unfinished.rom, finished.rom) <= allowed


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


def test_a_stored_abi_one_seed_takes_its_stroke_indexes_too(
    jp_manifest, vanilla, abi_1_unfinished
):
    """No ABI bump: every artifact has left the holes' own handicaps there (ADR 0036)."""
    finished = finish(
        legacy(jp_manifest),
        vanilla,
        abi_1_unfinished,
        ABI_1_OPTIONS,
        stroke_indexes=STROKE_INDEXES,
    )
    assert handicap_table(finished.rom) == bytes(STROKE_INDEXES)


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


@pytest.mark.parametrize("rated", [False, True], ids=["unrated", "rated"])
@pytest.mark.parametrize("credentials", [CREDENTIALS, None], ids=["signed in", "guest"])
def test_the_stages_overlap_only_at_the_placeholders(
    credentials, rated, jp_manifest, vanilla, unfinished
):
    finished = finish(
        jp_manifest,
        vanilla,
        unfinished.ips,
        OPTIONS,
        credentials,
        stroke_indexes=STROKE_INDEXES if rated else None,
    )
    unfinished_bytes = set().union(
        *(prg_bytes(regions) for regions in unfinished.regions.values())
    )
    finishing_bytes = set().union(
        *(prg_bytes(regions) for regions in finished.regions.values())
    )
    overlap = unfinished_bytes & finishing_bytes
    qr = prg_bytes(unfinished.regions["scorecard_qr"])
    options = prg_bytes(unfinished.regions["extended_sram_defaults"])
    # the course wrote each hole's own handicap there; a rated seed's replace them
    handicaps = set(range(HANDICAP_OFFSET, HANDICAP_OFFSET + HANDICAP_LENGTH))
    assert overlap & qr, (
        "the QR finishing patch should rewrite bytes scorecard_qr wrote"
    )
    assert overlap & options, "the options table should be rewritten"
    assert (overlap & handicaps == handicaps) is rated
    assert overlap <= qr | options | handicaps


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
