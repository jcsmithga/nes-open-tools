"""
Club house roof: recolor the roof in the club house walk-out scene.

`LC_AC01_PlaceClubHouseGolfer` (bank 12) loads the scene's palette from `$ADA4`
(`docs/cutscene_sprites.md`). The roof is background subpalette 1, color 2, the single
byte at `$ADAA`, vanilla `$27`. Only the roof's tiles draw that entry; the subpalette's
other colors are the walls' white and the sky.

The patch also fixes a vanilla attribute error. Roof tile (4, 1) sits in a quadrant of
attribute byte `$23C1` set to subpalette 0, so its one roof pixel shows the hedge's
`$22`, whatever color the roof is. That byte comes from a literal in the scene's
nametable stream, bank 7 `$8A20`, and `$51` puts the quadrant in subpalette 1; its
other three tiles are sky that looks the same in either.

Only the colors below are accepted, picked by eye, and a seed draws one uniformly. None
is from the pale `$3x` row, which looks washed out on a roof, or is the sky's, hedge's or
grass's color. The list holds all six roofs Mario Open gives its courses: Japan `$21`,
Australia `$26`, France `$11`, Hawaii `$27` (this game's), UK `$1B` and Extra `$2C`.
"""

from golf.core import rom_utils

from .byte_patch import BytePatch
from .composite import CompositePatch

PALETTE_BANK = 12
ROOF_COLOR_ADDR = 0xADAA  # walk-out palette $ADA4 + 6: subpalette 1, color 2
VANILLA_ROOF_COLOR = 0x27

NAMETABLE_BANK = 7
ROOF_ATTRIBUTE_ADDR = 0x8A20  # the literal that becomes attribute byte $23C1
VANILLA_ROOF_ATTRIBUTE = 0x50
FIXED_ROOF_ATTRIBUTE = 0x51  # tile (4, 1)'s quadrant to subpalette 1

#: the curated colors, each as likely as the others
ROOF_COLORS: tuple[int, ...] = (
    0x06,
    0x07,
    0x11,
    0x16,
    0x1B,
    0x1C,
    0x21,
    0x25,
    0x26,
    0x27,
    0x2C,
    0x2D,
)


def roof_attribute_fix_patch() -> BytePatch:
    """Put roof tile (4, 1) in the roof's subpalette."""
    return BytePatch(
        name="clubhouse_roof_attribute",
        description="Draw club house roof tile (4, 1) in the roof's subpalette",
        prg_offset=rom_utils.cpu_to_prg_switched(ROOF_ATTRIBUTE_ADDR, NAMETABLE_BANK),
        original=bytes([VANILLA_ROOF_ATTRIBUTE]),
        patched=bytes([FIXED_ROOF_ATTRIBUTE]),
    )


def clubhouse_roof_patch(color: int) -> CompositePatch[BytePatch]:
    """Set the club house roof to `color`, one of `ROOF_COLORS`, and fix tile (4, 1)."""
    if color not in ROOF_COLORS:
        raise ValueError(f"${color:02X} is not a curated roof color; see ROOF_COLORS")
    color_patch = BytePatch(
        name="clubhouse_roof_color",
        description=f"Color the club house roof ${color:02X}",
        prg_offset=rom_utils.cpu_to_prg_switched(ROOF_COLOR_ADDR, PALETTE_BANK),
        original=bytes([VANILLA_ROOF_COLOR]),
        patched=bytes([color]),
    )
    return CompositePatch(
        "clubhouse_roof",
        f"Color the club house roof ${color:02X} in the walk-out scene",
        [color_patch, roof_attribute_fix_patch()],
    )
