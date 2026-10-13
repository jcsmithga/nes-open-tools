"""Integration: seeds rolled from the checked-in presets build into ROMs.

The composed draw gives courses no seed had before: eighteen par 3s, a par 58, a
vanilla course from end to end. This builds some of them on the real vanilla ROM and
reads the course back out of the cartridge's tables.
"""

from pathlib import Path

import pytest

from golf.core import rom_utils
from golf.core.patches.course import DIGIT_TILES, PAR_TOTAL_CELLS, SCORECARD_BANK
from golf.core.patches.sram_defaults import VANILLA_CLUBS, VANILLA_NAME
from golf.core.rom_reader import RomReader
from golf.randomizer.build import PlayerOptions, build_unfinished, finish
from golf.randomizer.catalog import JP_ROM, US_ROM, Catalog, HoleStore
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import required_roms
from golf.randomizer.request import load_request, presets

ROOT = Path(__file__).resolve().parents[2]
ROM_PATH = ROOT / "nes_open_us.nes"

pytestmark = pytest.mark.skipif(
    not ROM_PATH.exists(), reason=f"{ROM_PATH.name} not present"
)

#: preset -> the course par its seeds have
BUILT = {
    "par_3_course": 54,
    "executive": 58,
    "short_front": 63,
    "tips": 72,
    "vanilla_nes_us": 72,
    "vanilla_jp_uk": 72,
}


@pytest.fixture(scope="module")
def vanilla() -> bytes:
    return ROM_PATH.read_bytes()


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load()


@pytest.fixture(scope="module")
def store(vanilla_courses, vanilla_jp_courses) -> HoleStore:
    return HoleStore(vanilla_courses)


@pytest.mark.parametrize("name", sorted(BUILT))
def test_a_preset_seed_builds_and_carries_its_course(name, vanilla, catalog, store):
    settings = load_request(presets()[name], f"rom-{name}")
    manifest = generate(catalog, CurationSnapshot.load(), settings)
    course = manifest.course
    assert course.par == BUILT[name]

    unfinished = build_unfinished(manifest, catalog, store, vanilla)
    options = PlayerOptions(VANILLA_NAME, frozenset(VANILLA_CLUBS))
    built = finish(manifest, vanilla, unfinished.ips, options).rom
    rom = RomReader.from_bytes(built)
    for index, slot in enumerate(course.holes):
        entry = catalog[slot.id]
        assert rom.read_fixed_byte(rom_utils.TABLE_PAR + index) == slot.par
        yards = (
            rom.read_fixed_byte(rom_utils.TABLE_DISTANCE_100 + index) * 100
            + rom.read_fixed_byte(rom_utils.TABLE_DISTANCE_10 + index) * 10
            + rom.read_fixed_byte(rom_utils.TABLE_DISTANCE_1 + index)
        )
        assert yards == entry.distance
    digits = bytes([DIGIT_TILES + course.par // 10, DIGIT_TILES + course.par % 10])
    assert unfinished.course_stats.total_par == course.par
    for cell in PAR_TOTAL_CELLS:
        assert rom.read_switched(cell, SCORECARD_BANK, 2) == digits


def test_a_vanilla_preset_needs_only_its_own_rom(catalog):
    curation = CurationSnapshot.load()
    for name, roms in (
        ("vanilla_nes_us", (US_ROM,)),
        ("vanilla_nes_japan", (US_ROM,)),
        ("vanilla_jp_hawaii", (US_ROM, JP_ROM)),
    ):
        manifest = generate(catalog, curation, load_request(presets()[name], "roms"))
        if name.startswith("vanilla_nes"):
            assert manifest.course.music.startswith("nes_")
        assert required_roms(manifest, catalog) == roms
