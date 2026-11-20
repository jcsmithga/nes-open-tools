"""
HUD course abbreviation: show "RC" (Random Course) in place of JP, US or UK.

The block at the top left of the in-game HUD reads, for example, `US 1H` over
`PAR 4`. `LD_9BE1` (bank 13) copies the course's two letters into the HUD buffer
at `$0414`/`$0415` from two three-entry tables indexed by `CurrCourse`: the first
letters at `$9C47` (`CourseCodeFirstLetterTable`) and the second letters at
`$9C4A`, so the six bytes read `JUUPSK` (`docs/hud_course_abbrev.md`).

The HUD draws in the font with A-Z from `$0A`, so R is `$1B` and C is `$0C`.
Every course slot plays course 1 in a randomizer ROM, but all three entries are
rewritten, which costs nothing.
"""

from golf.core import rom_utils

from .byte_patch import BytePatch

HUD_BANK = 13
COURSE_LETTERS_ADDR = 0x9C47  # CourseCodeFirstLetterTable, then the second letters

#: J U U / P S K in the A-Z-from-$0A font
VANILLA_COURSE_LETTERS = bytes([0x13, 0x1E, 0x1E, 0x19, 0x1C, 0x14])
#: R R R / C C C
RANDOM_COURSE_LETTERS = bytes([0x1B, 0x1B, 0x1B, 0x0C, 0x0C, 0x0C])

HUD_COURSE_ABBREV_PATCH = BytePatch(
    name="hud_course_abbrev",
    description="Show RC (Random Course) for the course on the in-game HUD",
    prg_offset=rom_utils.cpu_to_prg_switched(COURSE_LETTERS_ADDR, HUD_BANK),
    original=VANILLA_COURSE_LETTERS,
    patched=RANDOM_COURSE_LETTERS,
)
