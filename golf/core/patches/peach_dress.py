"""
Peach's dress: recolor the Princess Peach sprite in the putting view's left panel.

`DrawPeachPanel` (bank 13 `$BE0B`) draws her only while putting, from
`PeachBodyMetaspriteData` (`$BEF2`): the dress is the first chunk's 12 sprites, all in
sprite palette 0, and it uses only color 1 of that palette. `LoadGreenDetailViewTileset`
copies `GreenDetailPaletteData` (`$95FE`, 32 bytes) into `PaletteBuffer` whenever the
green detail view is drawn, so the dress is the single byte at `$960F`, vanilla `$25`.
Nothing else on the putting screen uses that palette entry: the flag and ball are
palette 3.

Only the curated colors below are accepted. They are the 54 non-black NES colors less
her skin (`$36`) and hair (`$28`), and less one of each pair that looks the same as
another on her sprite, judged by CIEDE2000 distance under Mesen's default palette and by
eye. `DRESS_COLOR_FAMILIES` groups them by the color they read as. Green has the most
entries because three of the NES's twelve hue columns read as green, so a random dress
comes from `draw_family_color`, which picks a family first, then a color within it.
White (`$30`) is a family of its own at half the weight of the others, so a white dress
is the rarest.
"""

from golf.core import rom_utils
from golf.core.palettes import ColorFamily, family_colors

from .byte_patch import BytePatch

PALETTE_BANK = 13
DRESS_COLOR_ADDR = 0x960F  # GreenDetailPaletteData + $11: sprite palette 0, color 1
VANILLA_DRESS_COLOR = 0x25

#: the curated colors, grouped by the color each family reads as; draw one with
#: palettes.draw_family_color, which honors each family's weight
DRESS_COLOR_FAMILIES: tuple[ColorFamily, ...] = (
    ColorFamily("green", (0x0B, 0x19, 0x29, 0x2A, 0x2B, 0x39, 0x3B)),
    ColorFamily("blue", (0x01, 0x11, 0x12, 0x21, 0x22, 0x31, 0x32)),
    ColorFamily("purple", (0x03, 0x04, 0x13, 0x14, 0x23, 0x33)),
    ColorFamily("pink", (0x05, 0x15, 0x24, 0x25, 0x35)),
    ColorFamily("orange", (0x07, 0x17, 0x27, 0x37)),
    ColorFamily("teal", (0x0C, 0x1C, 0x2C, 0x3C)),
    ColorFamily("gray", (0x00, 0x2D, 0x3D)),
    ColorFamily("white", (0x30,), weight=0.5),
    ColorFamily("red", (0x06, 0x16, 0x26)),
    ColorFamily("yellow", (0x08, 0x18, 0x38)),
)

#: every curated color, the families concatenated in order
DRESS_COLORS = family_colors(DRESS_COLOR_FAMILIES)


def peach_dress_patch(color: int) -> BytePatch:
    """Set Peach's dress to `color`, one of `DRESS_COLORS`."""
    if color not in DRESS_COLORS:
        raise ValueError(
            f"${color:02X} is not a curated dress color; see DRESS_COLOR_FAMILIES"
        )
    return BytePatch(
        name="peach_dress",
        description=f"Color Peach's dress ${color:02X} in the putting view",
        prg_offset=rom_utils.cpu_to_prg_switched(DRESS_COLOR_ADDR, PALETTE_BANK),
        original=bytes([VANILLA_DRESS_COLOR]),
        patched=bytes([color]),
    )
