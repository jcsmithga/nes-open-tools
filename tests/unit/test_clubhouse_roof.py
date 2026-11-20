"""Club house roof patch: the curated colors and the bytes it writes."""

from pathlib import Path

import pytest

from golf.core import rom_utils
from golf.core.palettes import NES_BLACK_ENTRIES
from golf.core.patches import ROOF_COLORS, clubhouse_roof_patch, parse_step_arg
from golf.core.patches.clubhouse_roof import VANILLA_ROOF_COLOR

WHITE = {0x20, 0x30}
#: the walk-out screen's sky, hedge and grass
SCENERY = {0x3C, 0x22, 0x1A}
#: Japan, Australia, France, Hawaii, UK and Extra in Mario Open
MARIO_OPEN_ROOFS = {0x21, 0x26, 0x11, 0x27, 0x1B, 0x2C}


class TestColors:
    def test_twelve_distinct_colors(self):
        assert len(set(ROOF_COLORS)) == len(ROOF_COLORS) == 12

    def test_includes_the_vanilla_roof_and_mario_opens(self):
        assert VANILLA_ROOF_COLOR in ROOF_COLORS
        assert set(ROOF_COLORS) >= MARIO_OPEN_ROOFS

    def test_no_pale_row_black_white_or_scenery(self):
        excluded = NES_BLACK_ENTRIES | WHITE | SCENERY
        assert not set(ROOF_COLORS) & excluded
        assert all(color < 0x30 for color in ROOF_COLORS)


class TestPatch:
    def test_writes_the_roof_entry_and_the_attribute_fix(self):
        color, attribute = clubhouse_roof_patch(0x16).patches
        assert color.prg_offset == rom_utils.cpu_to_prg_switched(0xADAA, 12)
        assert (color.original, color.patched) == (b"\x27", b"\x16")
        assert attribute.prg_offset == rom_utils.cpu_to_prg_switched(0x8A20, 7)
        assert (attribute.original, attribute.patched) == (b"\x50", b"\x51")

    def test_the_vanilla_color_still_fixes_the_attribute(self):
        _, attribute = clubhouse_roof_patch(VANILLA_ROOF_COLOR).patches
        assert attribute.patched == b"\x51"

    @pytest.mark.parametrize("color", [0x0F, 0x30, 0x3C, 0x22, 0x1A, 0x37, 0x40])
    def test_rejects_uncurated_colors(self, color):
        with pytest.raises(ValueError, match="not a curated roof color"):
            clubhouse_roof_patch(color)

    def test_inline_step(self):
        step = parse_step_arg("clubhouse_roof:color=0x2C", Path("."))
        assert step.params.color == 0x2C
