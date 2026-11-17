"""
Signpost color: recolor the brick behind the pre-hole signpost's banner.

`LC_AC2F_DrawSignpostCard` (bank 12) loads its palette with `Load32BytesToBuffer`
from `$ADC4` (`docs/prehole_signpost.md`). The banner sits in background
subpalette 1 - `$30` letters, `$21` sky, `$15` brick on `$0F` - so the brick is the
single byte at `$ADCB`. The HOLE, PAR and yards signs below it are subpalette 0 and
keep their wood colors. On long-drive and nearest-pin contest holes the game writes
`$12` over the same entry at `$AC46`, after this palette load, so those holes show a
blue banner whatever this patch sets.

The byte sits just before the banner bodies `signpost_random_banner` reclaims
(`$ADE4`-`$B01B`), so the two patches stack.

Only the curated colors below are accepted: the non-black NES colors less the
banner's own white and sky, less every color the white letters are hard to read
against (the whole pale row and the faintest of the `$2x` row), and less one of each
pair that looks the same as another. `SIGNPOST_COLOR_FAMILIES` groups them by the
color they read as, and a random brick comes from `draw_family_color`, which picks a
family first, then a color within it, so each family is equally likely.

`SIGNPOST_DANGEROUS_COLORS` holds colors set aside on feedback, kept for a later look.
The patch rejects them too.
"""

from golf.core import rom_utils
from golf.core.palettes import ColorFamily, family_colors

from .byte_patch import BytePatch

PALETTE_BANK = 12
BANNER_COLOR_ADDR = 0xADCB  # signpost palette $ADC4 + 7: subpalette 1, color 3
VANILLA_BANNER_COLOR = 0x15

#: the curated colors, grouped by the color each family reads as; draw one with
#: palettes.draw_family_color
SIGNPOST_COLOR_FAMILIES: tuple[ColorFamily, ...] = (
    ColorFamily("green", (0x0B, 0x19)),
    ColorFamily("blue", (0x01, 0x11, 0x12)),
    ColorFamily("purple", (0x03, 0x04, 0x13, 0x14)),
    ColorFamily("pink", (0x05, 0x15, 0x25)),
    ColorFamily("orange", (0x07, 0x17)),
    ColorFamily("teal", (0x0C, 0x1C)),
    ColorFamily("gray", (0x00, 0x2D)),
    ColorFamily("red", (0x06, 0x16)),
    ColorFamily("yellow", (0x08, 0x18)),
)

#: every curated color, the families concatenated in order
SIGNPOST_COLORS = family_colors(SIGNPOST_COLOR_FAMILIES)

#: set aside on feedback, not accepted by the patch; kept for a later look
SIGNPOST_DANGEROUS_COLORS: tuple[int, ...] = (0x22, 0x23, 0x24, 0x26, 0x27, 0x28)


def signpost_color_patch(color: int) -> BytePatch:
    """Set the signpost banner's brick to `color`, one of `SIGNPOST_COLORS`."""
    if color not in SIGNPOST_COLORS:
        raise ValueError(
            f"${color:02X} is not a curated signpost color; see SIGNPOST_COLOR_FAMILIES"
        )
    return BytePatch(
        name="signpost_color",
        description=f"Color the signpost banner's brick ${color:02X}",
        prg_offset=rom_utils.cpu_to_prg_switched(BANNER_COLOR_ADDR, PALETTE_BANK),
        original=bytes([VANILLA_BANNER_COLOR]),
        patched=bytes([color]),
    )
