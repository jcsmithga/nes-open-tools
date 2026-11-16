"""
Quick-test ROM: one hole, booting straight onto its tee in practice mode.

Reset ends with `JMP $8000` in bank 13, the game's outer loop, whose first 10
bytes far-call the title screen and menus in bank 12. `QUICK_TEST_BOOT_PATCH`
replaces them with a loop of the same length that sets hole 1 and calls
`LD9C3_InitManualWindMode` (the fixed bank's Training entry), which plays the
hole in practice mode and returns when it ends. The loop then starts the hole
again from the tee.

The hole fills all 18 course slots; `CoursePatch` stores its data once. The
scorecard is never shown, so its totals are left alone.

See docs/planning/quick_test_rom.md.
"""

from golf.core import rom_utils
from golf.core.asm6502 import assemble
from golf.formats.hole_check import hidden_rows_trimmed
from golf.formats.hole_data import HoleData

from .base import ROMPatch
from .byte_patch import BytePatch
from .course import CoursePatch
from .green_shortcut import green_shortcut_patch
from .multi_bank import COURSE_MIRRORS_PATCH, MULTI_BANK_CODE_PATCH
from .stack import PatchStack
from .swing_speed_hud import SWING_SPEED_HUD_PATCH
from .wind_fix import WIND_FIX_PATCH
from .wram_expansion import WRAM_EXPANSION_PATCH

OUTER_LOOP_BANK = 13
OUTER_LOOP_ADDR = 0x8000

#: `LDA #$00 / STA CurrentPlayerIndex / JSR ExecuteFarCall / .db $0C, $00, $80`
VANILLA_OUTER_LOOP = bytes.fromhex("A900 8599 2072D3 0C0080")

_BOOT_SOURCE = """
    Loop:
        lda #$00
        sta HoleNumber
        jsr LD9C3_InitManualWindMode
        jmp Loop
"""


def _boot_code() -> bytes:
    code = assemble(
        _BOOT_SOURCE,
        OUTER_LOOP_ADDR,
        {"HoleNumber": 0x94, "LD9C3_InitManualWindMode": 0xD9C3},
    ).code
    assert len(code) == len(VANILLA_OUTER_LOOP)
    return code


QUICK_TEST_BOOT_PATCH = BytePatch(
    name="quick_test_boot",
    description="Boot into practice mode on hole 1, returning to the tee when it ends",
    prg_offset=rom_utils.cpu_to_prg_switched(OUTER_LOOP_ADDR, OUTER_LOOP_BANK),
    original=VANILLA_OUTER_LOOP,
    patched=_boot_code(),
)


def quick_test_steps(hole: HoleData) -> list[ROMPatch]:
    """The patches that make a quick-test ROM of `hole`, in build order."""
    return [
        WRAM_EXPANSION_PATCH,
        MULTI_BANK_CODE_PATCH,
        COURSE_MIRRORS_PATCH,
        CoursePatch([hole] * rom_utils.HOLES_PER_COURSE, scorecard_totals=False),
        WIND_FIX_PATCH,
        green_shortcut_patch(),
        SWING_SPEED_HUD_PATCH,
        QUICK_TEST_BOOT_PATCH,
    ]


def build_quick_test_rom(base: bytes, hole: HoleData) -> bytes:
    """
    A quick-test ROM of `hole`, as the editor holds it, built on the vanilla US
    ROM `base`. Rows the editor hid are dropped first; `hole` is not changed.

    Check the hole with `golf.formats.hole_check.rom_problems` first: a hole it
    finds problems in raises here. `BankOverflowError` is a hole too large for
    one ROM, and `StackError` a base that is not the vanilla US ROM.
    """
    trimmed, _ = hidden_rows_trimmed(hole)
    return PatchStack(quick_test_steps(trimmed)).build(base).rom
