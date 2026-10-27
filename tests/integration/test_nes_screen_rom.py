"""
`NesMachine` with `ppu=True` against the game: the screen a putt leaves behind,
drawn by the game's own code through the PPU's registers.
"""

from pathlib import Path

import pytest

from golf.core.rom_reader import RomReader
from golf.core.rom_text import encodings
from golf.physics import PUTTER, ShotInput
from golf.physics.ppu import HORIZONTAL
from golf.physics.rom_game import RomGameShot
from golf.rendering.nes_screen import HEIGHT, WIDTH, render_screen

ROM_PATH = "nes_open_us.nes"
HUD_SPACE = b"\xff"

pytestmark = pytest.mark.skipif(
    not Path(ROM_PATH).exists(), reason=f"{ROM_PATH} not present"
)


@pytest.fixture(scope="module")
def putted() -> RomGameShot:
    game = RomGameShot(RomReader(ROM_PATH), "us", 1, ppu=True)
    flag_x, flag_y = game.flag.x >> 8, game.flag.y >> 8
    game.play(ShotInput(club=PUTTER, swing_speed=1, x=flag_x + 4, y=flag_y + 3))
    return game


def test_the_game_fills_the_ppu(putted):
    ppu = putted.machine.ppu
    assert ppu is not None
    assert ppu.vram[0x3F00] == 0x0F
    assert any(ppu.vram[0x0000:0x2000])
    assert ppu.mirroring == HORIZONTAL


def test_the_hud_is_in_the_nametable(putted):
    ppu = putted.machine.ppu
    scorecard = encodings()["scorecard"]
    letters, digit = scorecard.encode("PAR"), scorecard.encode("4")
    assert letters is not None and digit is not None
    # The HUD's space is $FF, not the scorecard's.
    assert letters + HUD_SPACE + digit in bytes(ppu.vram[0x2000:0x23C0])


def test_the_screen_draws(putted):
    image = render_screen(putted.machine.ppu)
    assert image.size == (WIDTH, HEIGHT)
    colors = image.getcolors()
    assert colors is not None and len(colors) >= 5
