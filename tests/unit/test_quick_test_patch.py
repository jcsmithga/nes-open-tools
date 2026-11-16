"""Unit tests for the quick-test ROM's patches."""

from golf.core.patches import CoursePatch
from golf.core.patches.quick_test import (
    QUICK_TEST_BOOT_PATCH,
    VANILLA_OUTER_LOOP,
    quick_test_steps,
)
from golf.core.patches.registry import PATCH_SPECS, BuildContext, NoParams
from tests.synthetic_holes import synthetic_hole


def test_boot_patch_is_the_loop_over_bank_13s_outer_loop():
    assert QUICK_TEST_BOOT_PATCH.prg_offset == 13 * 0x4000
    assert QUICK_TEST_BOOT_PATCH.original == VANILLA_OUTER_LOOP
    # LDA #$00 / STA HoleNumber / JSR LD9C3_InitManualWindMode / JMP $8000
    assert QUICK_TEST_BOOT_PATCH.patched == bytes.fromhex("A900 8594 20C3D9 4C0080")


def test_steps_put_the_hole_in_every_slot_once():
    hole = synthetic_hole()
    steps = quick_test_steps(hole)
    assert [step.name for step in steps] == [
        "wram_expansion",
        "multi_bank_lookup",
        "course_mirrors",
        "course",
        "wind_fix",
        "green_shortcut",
        "swing_speed_hud",
        "quick_test_boot",
    ]
    course = steps[3]
    assert isinstance(course, CoursePatch)
    assert course.holes == [hole] * 18
    assert course.scorecard_writes == []
    assert len([w for w in course.writes if "terrain (" in w.name]) == 1


def test_boot_patch_is_registered():
    spec = PATCH_SPECS["quick_test_boot"]
    assert spec.build(BuildContext(b""), NoParams()) is QUICK_TEST_BOOT_PATCH
