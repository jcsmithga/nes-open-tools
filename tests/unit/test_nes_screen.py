"""`golf.rendering.nes_screen`: a screen drawn from a hand-made PPU state."""

from golf.core.palettes import NES_SYSTEM_PALETTE
from golf.physics.ppu import PpuState
from golf.rendering.nes_screen import HEIGHT, WIDTH, render_screen, sprite_tile

BLACK, WHITE, RED = 0x0F, 0x30, 0x16
SOLID = 1  # every pixel color 3
LEFT_HALF = 2  # color 1 in its left four columns, clear in the right four
CLEAR = (0, 0, 0, 0)


def ppu_with_tiles() -> PpuState:
    ppu = PpuState()
    ppu.vram[SOLID * 16 : SOLID * 16 + 16] = b"\xff" * 16
    ppu.vram[LEFT_HALF * 16 : LEFT_HALF * 16 + 8] = b"\xf0" * 8
    ppu.vram[0x3F00] = BLACK
    ppu.vram[0x3F03] = WHITE
    ppu.vram[0x3F11] = RED
    ppu.vram[0x3F13] = RED
    ppu.oam[:] = b"\xff" * 256
    return ppu


def color(value: int) -> tuple[int, int, int]:
    return NES_SYSTEM_PALETTE[value]


def test_the_background_draws_tiles_in_their_palette():
    ppu = ppu_with_tiles()
    ppu.vram[0x2000] = SOLID
    image = render_screen(ppu)
    assert image.size == (WIDTH, HEIGHT)
    assert image.getpixel((0, 0)) == color(WHITE)
    assert image.getpixel((7, 7)) == color(WHITE)
    assert image.getpixel((8, 0)) == color(BLACK)


def test_the_scroll_moves_the_background():
    ppu = ppu_with_tiles()
    ppu.vram[0x2001] = SOLID
    ppu.scroll_x = 8
    image = render_screen(ppu)
    assert image.getpixel((0, 0)) == color(WHITE)


def test_a_sprite_is_drawn_one_row_below_its_y():
    ppu = ppu_with_tiles()
    ppu.oam[0:4] = bytes([9, SOLID, 0, 20])
    image = render_screen(ppu)
    assert image.getpixel((20, 10)) == color(RED)
    assert image.getpixel((20, 9)) == color(BLACK)


def test_a_sprite_behind_the_background_shows_only_where_it_is_clear():
    ppu = ppu_with_tiles()
    ppu.vram[0x2000] = SOLID
    ppu.oam[0:4] = bytes([0, SOLID, 0x20, 4])
    image = render_screen(ppu)
    assert image.getpixel((5, 2)) == color(WHITE)
    assert image.getpixel((9, 2)) == color(RED)


def test_a_flipped_sprite_tile():
    ppu = ppu_with_tiles()
    plain = sprite_tile(ppu, LEFT_HALF, 0)
    flipped = sprite_tile(ppu, LEFT_HALF, 0x40)
    assert plain.getpixel((0, 0)) == (*color(RED), 255)
    assert plain.getpixel((7, 0)) == CLEAR
    assert flipped.getpixel((7, 0)) == (*color(RED), 255)
    assert flipped.getpixel((0, 0)) == CLEAR
