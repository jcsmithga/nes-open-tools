"""Integration: seeds of each course mode build into ROMs.

The modes give courses no seed had before: eighteen par 3s, a vanilla course from end
to end. This builds them on the real vanilla ROM and reads the course back out of the
cartridge's tables.
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
from golf.randomizer.request import settings_from_request

ROOT = Path(__file__).resolve().parents[2]
ROM_PATH = ROOT / "nes_open_us.nes"

pytestmark = pytest.mark.skipif(
    not ROM_PATH.exists(), reason=f"{ROM_PATH.name} not present"
)


def vanilla_course(course: str, rom: str) -> dict:
    return {
        "sources": [rom],
        "constraints": [{"rule": "vanilla_course", "course": course}],
    }


#: mode -> its request, and the course par its seeds have
BUILT = {
    "par_3": (
        {"include": ["derived"], "constraints": [{"rule": "par_3"}]},
        54,
    ),
    "standard": ({}, 72),
    "vanilla_nes_us": (vanilla_course("nes_us", US_ROM), 72),
    "vanilla_jp_uk": (vanilla_course("jp_uk", JP_ROM), 72),
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
def test_a_mode_seed_builds_and_carries_its_course(name, vanilla, catalog, store):
    request, par = BUILT[name]
    settings = settings_from_request(request, f"rom-{name}")
    manifest = generate(catalog, CurationSnapshot.load(), settings)
    course = manifest.course
    assert course.par == par

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


def test_a_vanilla_course_needs_only_its_own_rom(catalog):
    curation = CurationSnapshot.load()
    for course, rom, roms in (
        ("nes_us", US_ROM, (US_ROM,)),
        ("nes_japan", US_ROM, (US_ROM,)),
        ("jp_hawaii", JP_ROM, (US_ROM, JP_ROM)),
    ):
        settings = settings_from_request(vanilla_course(course, rom), "roms")
        manifest = generate(catalog, curation, settings)
        if rom == US_ROM:
            assert manifest.course.music.startswith("nes_")
        assert required_roms(manifest, catalog) == roms
