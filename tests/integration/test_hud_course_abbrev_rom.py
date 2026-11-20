"""Integration tests: the HUD course abbreviation against the real vanilla ROM."""

from pathlib import Path

import pytest

from golf.core.patches import HUD_COURSE_ABBREV_PATCH
from golf.core.patches.hud_course_abbrev import (
    COURSE_LETTERS_ADDR,
    HUD_BANK,
    RANDOM_COURSE_LETTERS,
)
from golf.core.rom_reader import RomReader
from golf.core.rom_text import encodings
from golf.core.rom_writer import RomWriter

ROM_PATH = "nes_open_us.nes"

#: bank 13, LD_9BE1: LDA $9C47,X / STA $0414 / LDA $9C4A,X / STA $0415
LETTER_LOADS_ADDR = 0x9BED

pytestmark = pytest.mark.skipif(
    not Path(ROM_PATH).exists(), reason=f"{ROM_PATH} not present"
)


def test_the_hud_reads_both_letter_tables_by_course():
    rom = RomReader(ROM_PATH)
    loads = rom.read_switched(LETTER_LOADS_ADDR, HUD_BANK, 12)
    assert loads == bytes(
        [0xBD, 0x47, 0x9C, 0x8D, 0x14, 0x04, 0xBD, 0x4A, 0x9C, 0x8D, 0x15, 0x04]
    )
    assert COURSE_LETTERS_ADDR == 0x9C47


def test_the_letters_spell_rc_for_every_course():
    font = encodings()["scorecard"].decode
    letters = "".join(font[b] for b in RANDOM_COURSE_LETTERS)
    assert [letters[i] + letters[i + 3] for i in range(3)] == ["RC"] * 3


def test_apply_and_reload(tmp_path):
    out = tmp_path / "hud_course_abbrev.nes"
    writer = RomWriter(ROM_PATH, str(out))
    assert HUD_COURSE_ABBREV_PATCH.can_apply(writer)
    HUD_COURSE_ABBREV_PATCH.apply(writer)
    writer.save()

    patched = RomReader(str(out)).read_switched(COURSE_LETTERS_ADDR, HUD_BANK, 6)
    assert patched == RANDOM_COURSE_LETTERS
