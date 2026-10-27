"""
A picture of the NES screen from the PPU's memory (`golf.physics.ppu.PpuState`).

Draws the background through the scroll and the sprites over it, front to back
by OAM order, as the console would for a frame with no mid-frame changes.
"""

from PIL import Image

from golf.core.chr_tile import decode_tile
from golf.core.palettes import NES_SYSTEM_PALETTE
from golf.physics.ppu import PpuState

WIDTH = 256
HEIGHT = 240


def _tile(ppu: PpuState, table: int, tile: int) -> list[list[int]]:
    base = table * 0x1000 + tile * 16
    return decode_tile(bytes(ppu.vram[base : base + 16]))


def _color(ppu: PpuState, palette: int, value: int) -> tuple[int, int, int]:
    index = 0x3F00 if value == 0 else 0x3F00 + palette * 4 + value
    return NES_SYSTEM_PALETTE[ppu.vram[index] & 0x3F]


def render_background(ppu: PpuState) -> tuple[Image.Image, list[list[bool]]]:
    """The background through the scroll, and which pixels are opaque."""
    image = Image.new("RGB", (WIDTH, HEIGHT))
    pixels = image.load()
    assert pixels is not None
    opaque = [[False] * WIDTH for _ in range(HEIGHT)]
    table = ppu.ctrl >> 4 & 1
    base_x = (ppu.ctrl & 1) * 256 + ppu.scroll_x
    base_y = (ppu.ctrl >> 1 & 1) * 240 + ppu.scroll_y
    tiles: dict[int, list[list[int]]] = {}
    for y in range(HEIGHT):
        world_y = (base_y + y) % 480
        nametable_y, row = divmod(world_y, 240)
        for x in range(WIDTH):
            world_x = (base_x + x) % 512
            nametable_x, column = divmod(world_x, 256)
            nametable = 0x2000 + (nametable_y * 2 + nametable_x) * 0x400
            cell = ppu.nametable_address(nametable + (row >> 3) * 32 + (column >> 3))
            tile = ppu.vram[cell]
            attribute = ppu.vram[
                ppu.nametable_address(
                    nametable + 0x3C0 + (row >> 5) * 8 + (column >> 5)
                )
            ]
            shift = (row >> 4 & 1) * 4 + (column >> 4 & 1) * 2
            palette = attribute >> shift & 3
            pattern = tiles.get(tile)
            if pattern is None:
                pattern = tiles[tile] = _tile(ppu, table, tile)
            value = pattern[row & 7][column & 7]
            opaque[y][x] = value != 0
            pixels[x, y] = _color(ppu, palette, value)
    return image, opaque


def render_sprites(
    ppu: PpuState, image: Image.Image, opaque: list[list[bool]] | None = None
) -> None:
    """Draw OAM over `image`; behind-background sprites show only where it is clear."""
    pixels = image.load()
    assert pixels is not None
    tall = bool(ppu.ctrl & 0x20)
    drawn = [[False] * WIDTH for _ in range(HEIGHT)]
    for sprite in range(64):
        y, tile, attributes, x = ppu.oam[sprite * 4 : sprite * 4 + 4]
        if y >= 0xEF:
            continue
        y += 1
        palette = 4 + (attributes & 3)
        flip_x = attributes & 0x40
        flip_y = attributes & 0x80
        behind = attributes & 0x20
        height = 16 if tall else 8
        for row in range(height):
            source_row = height - 1 - row if flip_y else row
            if tall:
                table = tile & 1
                index = (tile & 0xFE) + (source_row >> 3)
            else:
                table = ppu.ctrl >> 3 & 1
                index = tile
            pattern = _tile(ppu, table, index)[source_row & 7]
            for column in range(8):
                value = pattern[7 - column if flip_x else column]
                px, py = x + column, y + row
                if not value or px >= WIDTH or py >= HEIGHT or drawn[py][px]:
                    continue
                drawn[py][px] = True
                if behind and opaque is not None and opaque[py][px]:
                    continue
                pixels[px, py] = _color(ppu, palette, value)


def render_screen(ppu: PpuState) -> Image.Image:
    image, opaque = render_background(ppu)
    render_sprites(ppu, image, opaque)
    return image


def sprite_tile(ppu: PpuState, tile: int, attributes: int) -> Image.Image:
    """One 8x8 sprite as OAM `tile` and `attributes` draw it, clear where it is."""
    image = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    pixels = image.load()
    assert pixels is not None
    table = ppu.ctrl >> 3 & 1
    pattern = _tile(ppu, table, tile)
    for row in range(8):
        source = pattern[7 - row if attributes & 0x80 else row]
        for column in range(8):
            value = source[7 - column if attributes & 0x40 else column]
            if value:
                pixels[column, row] = (*_color(ppu, 4 + (attributes & 3), value), 255)
    return image
