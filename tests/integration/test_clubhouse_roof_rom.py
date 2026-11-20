"""Integration tests: the club house roof against the real vanilla ROM."""

from pathlib import Path

import pytest

from golf.core.graphics_codec import VideoMemory, load_graphics_table
from golf.core.patches import clubhouse_roof_patch
from golf.core.patches.clubhouse_roof import (
    NAMETABLE_BANK,
    PALETTE_BANK,
    ROOF_COLOR_ADDR,
)
from golf.core.rom_reader import RomReader
from golf.core.rom_writer import RomWriter

ROM_PATH = "nes_open_us.nes"

#: bank 12: LC_AC01_PlaceClubHouseGolfer's Load32BytesToBuffer and its inline pointer
PALETTE_LOAD_ADDR = 0xAC29
WALK_OUT_PALETTE_ADDR = 0xADA4
#: the scene's background tiles and its nametable
TILES = (5, 0xA22F)
NAMETABLE = (NAMETABLE_BANK, 0x88BF)
ROOF_SUBPALETTE = 1
ROOF_ATTRIBUTE = 0x23C1

pytestmark = pytest.mark.skipif(
    not Path(ROM_PATH).exists(), reason=f"{ROM_PATH} not present"
)


def screen(rom: RomReader) -> bytearray:
    vram = VideoMemory()
    load_graphics_table(rom, *TILES, vram)
    load_graphics_table(rom, *NAMETABLE, vram)
    return vram.data


def subpalette_at(data: bytearray, column: int, row: int) -> int:
    attribute = data[0x23C0 + (row // 4) * 8 + column // 4]
    return attribute >> (((row % 4) // 2) * 4 + ((column % 4) // 2) * 2) & 3


def test_roof_is_background_subpalette_1_color_2():
    rom = RomReader(ROM_PATH)
    load = rom.read_switched(PALETTE_LOAD_ADDR, PALETTE_BANK, 5)
    assert load == bytes([0x20, 0x0A, 0xD8, 0xA4, 0xAD])  # JSR $D80A / .dw $ADA4
    palette = rom.read_switched(WALK_OUT_PALETTE_ADDR, PALETTE_BANK, 8)
    assert palette[4:8] == bytes([0x0F, 0x30, 0x27, 0x3C])
    assert ROOF_COLOR_ADDR == WALK_OUT_PALETTE_ADDR + 4 * ROOF_SUBPALETTE + 2


def test_apply_recolors_the_roof_and_fixes_only_tile_4_1(tmp_path):
    vanilla = RomReader(ROM_PATH)
    out = tmp_path / "clubhouse_roof.nes"
    writer = RomWriter(ROM_PATH, str(out))
    patch = clubhouse_roof_patch(0x1B)
    assert patch.can_apply(writer)
    patch.apply(writer)
    writer.save()

    patched = RomReader(str(out))
    assert patched.read_switched(ROOF_COLOR_ADDR, PALETTE_BANK) == b"\x1b"
    before, after = screen(vanilla), screen(patched)
    changed = [addr for addr in range(len(before)) if before[addr] != after[addr]]
    assert changed == [ROOF_ATTRIBUTE]
    assert (before[ROOF_ATTRIBUTE], after[ROOF_ATTRIBUTE]) == (0x50, 0x51)
    # tile (4, 1) is the roof's edge, tile $36, and it joins the roof's subpalette
    assert after[0x2000 + 1 * 32 + 4] == 0x36
    assert (subpalette_at(before, 4, 1), subpalette_at(after, 4, 1)) == (0, 1)
