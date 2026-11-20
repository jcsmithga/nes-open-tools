"""Render the club house walk-out background with the roof in other colors.

Loads the scene's background the way `LC_AC01_PlaceClubHouseGolfer` (bank 12) does -
bank 5 `$A22F` (tiles at `$1000`) and bank 7 `$88BF` (nametable and attributes) -
with the palette at bank 12 `$ADA4`, and draws it without the golfer.

The roof is background subpalette 1, color 2 (`$ADAA`, vanilla `$27`); nothing else on
the screen draws that entry. Tile (4, 1), the roof's top edge, sits in an attribute
quadrant set to subpalette 0, so its one roof pixel shows the sky's `$22`; the
renders other than `vanilla.png` set that quadrant to subpalette 1, as setting bank 7
`$8A20` (a literal in the nametable stream) from `$50` to `$51` does. See
`docs/cutscene_sprites.md`.

Writes `vanilla.png`, `vanilla_attr_fixed.png` and `roof_colors_sheet.png`, the club
house corner for every color.
"""

import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from golf.core.graphics_codec import VideoMemory, load_graphics_table
from golf.core.palettes import NES_BLACK_ENTRIES, NES_SYSTEM_PALETTE
from golf.core.rom_reader import RomReader

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.dirname(os.path.abspath(__file__))

SCENE_BANK = 12
PALETTE = 0xADA4
ROOF_ENTRY = 6  # subpalette 1, color 2
TILES = (5, 0xA22F)
NAMETABLE = (7, 0x88BF)

# Tile (4, 1) is the top-left quadrant of attribute byte $23C1
BAD_ATTRIBUTE = 0x23C1
BAD_QUADRANT_SHIFT = 0

# The sky ($22, $3C), the grass ($1A), and white; the blacks are dropped separately
EXCLUDED = {0x20, 0x30, 0x22, 0x3C, 0x1A}

# The club house corner, in tiles: columns 0-16, rows 0-21
CROP = (0, 0, 17 * 8, 22 * 8)


def load_screen(rom: RomReader) -> VideoMemory:
    vram = VideoMemory()
    load_graphics_table(rom, *TILES, vram)
    load_graphics_table(rom, *NAMETABLE, vram)
    return vram


def fix_attribute(vram: VideoMemory) -> None:
    value = vram.data[BAD_ATTRIBUTE]
    value &= ~(3 << BAD_QUADRANT_SHIFT) & 0xFF
    value |= 1 << BAD_QUADRANT_SHIFT
    vram.data[BAD_ATTRIBUTE] = value


def render(vram: VideoMemory, palette: bytes) -> Image.Image:
    image = Image.new("RGB", (256, 240))
    pixels = image.load()
    assert pixels is not None
    for ty in range(30):
        for tx in range(32):
            tile = vram.data[0x2000 + ty * 32 + tx]
            attribute = vram.data[0x23C0 + (ty // 4) * 8 + tx // 4]
            sub = attribute >> (((ty % 4) // 2) * 4 + ((tx % 4) // 2) * 2) & 3
            pattern = vram.tile(tile, 0x1000)
            for y in range(8):
                for x in range(8):
                    value = pattern[y][x]
                    nes = palette[0] if value == 0 else palette[sub * 4 + value]
                    pixels[tx * 8 + x, ty * 8 + y] = NES_SYSTEM_PALETTE[nes & 0x3F]
    return image


def roof_colors() -> list[int]:
    return [c for c in range(0x40) if c not in NES_BLACK_ENTRIES and c not in EXCLUDED]


def contact_sheet(crops: list[tuple[int, Image.Image]], columns: int) -> Image.Image:
    scale = 2
    width, height = CROP[2] * scale, CROP[3] * scale
    label = 18
    gap = 8
    rows = -(-len(crops) // columns)
    sheet = Image.new(
        "RGB",
        (columns * (width + gap) + gap, rows * (height + label + gap) + gap),
        (40, 40, 40),
    )
    draw = ImageDraw.Draw(sheet)
    for i, (color, crop) in enumerate(crops):
        row, column = divmod(i, columns)
        x = gap + column * (width + gap)
        y = gap + row * (height + label + gap)
        sheet.paste(crop.resize((width, height), Image.Resampling.NEAREST), (x, y))
        draw.rectangle(
            (x, y + height + 2, x + 14, y + height + 14),
            fill=NES_SYSTEM_PALETTE[color],
            outline=(255, 255, 255),
        )
        draw.text((x + 20, y + height + 3), f"${color:02X}", fill=(255, 255, 255))
    return sheet


def main() -> None:
    rom = RomReader(os.path.join(ROOT, "nes_open_us.nes"))
    palette = bytes(rom.read_switched(PALETTE, SCENE_BANK, 32))
    assert palette[ROOF_ENTRY] == 0x27, f"roof is ${palette[ROOF_ENTRY]:02X}"

    vram = load_screen(rom)
    big = (512, 480)
    render(vram, palette).resize(big, Image.Resampling.NEAREST).save(
        os.path.join(HERE, "vanilla.png")
    )
    fix_attribute(vram)
    render(vram, palette).resize(big, Image.Resampling.NEAREST).save(
        os.path.join(HERE, "vanilla_attr_fixed.png")
    )

    crops = []
    for color in roof_colors():
        recolored = bytearray(palette)
        recolored[ROOF_ENTRY] = color
        crops.append((color, render(vram, bytes(recolored)).crop(CROP)))
    contact_sheet(crops, columns=8).save(os.path.join(HERE, "roof_colors_sheet.png"))
    print(f"wrote {len(crops)} colors to {HERE}")


if __name__ == "__main__":
    main()
