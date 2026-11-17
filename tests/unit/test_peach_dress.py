"""Peach's dress color patch: the curated colors and the byte it writes."""

import random
from pathlib import Path

import pytest

from golf.core import rom_utils
from golf.core.palettes import NES_BLACK_ENTRIES, draw_family_color
from golf.core.patches import (
    DRESS_COLOR_FAMILIES,
    DRESS_COLORS,
    parse_step_arg,
    peach_dress_patch,
)
from golf.core.patches.peach_dress import VANILLA_DRESS_COLOR

HAIR, SKIN = 0x28, 0x36


class TestColors:
    def test_flat_list_is_the_families_concatenated(self):
        assert (
            tuple(color for family in DRESS_COLOR_FAMILIES for color in family.colors)
            == DRESS_COLORS
        )

    def test_no_color_is_in_two_families(self):
        assert len(set(DRESS_COLORS)) == len(DRESS_COLORS) == 43

    def test_family_names_are_unique(self):
        names = [family.name for family in DRESS_COLOR_FAMILIES]
        assert len(set(names)) == len(names)

    def test_vanilla_is_curated(self):
        (pink,) = [f for f in DRESS_COLOR_FAMILIES if f.name == "pink"]
        assert VANILLA_DRESS_COLOR in pink.colors

    def test_no_black_hair_or_skin(self):
        assert not set(DRESS_COLORS) & (NES_BLACK_ENTRIES | {HAIR, SKIN})

    def test_white_is_its_own_family_at_half_weight(self):
        weights = {family.name: family.weight for family in DRESS_COLOR_FAMILIES}
        (white,) = [f for f in DRESS_COLOR_FAMILIES if f.name == "white"]
        assert white.colors == (0x30,)
        assert weights.pop("white") == 0.5
        assert set(weights.values()) == {1.0}

    def test_draw_family_color_draws_a_curated_color(self):
        rng = random.Random(0)
        draws = [draw_family_color(DRESS_COLOR_FAMILIES, rng) for _ in range(2000)]
        assert set(draws) <= set(DRESS_COLORS)
        # nine families at weight 1 and white at 0.5: white 0.5/9.5, red 1/9.5
        white = draws.count(0x30) / len(draws)
        red = sum(draws.count(color) for color in (0x06, 0x16, 0x26)) / len(draws)
        assert 0.035 < white < 0.07
        assert 0.085 < red < 0.125
        assert white < red

    def test_every_color_is_an_nes_color(self):
        assert all(0 <= color <= 0x3F for color in DRESS_COLORS)


class TestPatch:
    def test_writes_the_dress_entry(self):
        patch = peach_dress_patch(0x16)
        assert patch.prg_offset == rom_utils.cpu_to_prg_switched(0x960F, 13)
        assert patch.original == bytes([0x25])
        assert patch.patched == bytes([0x16])

    @pytest.mark.parametrize("color", [0x0F, 0x0D, HAIR, SKIN, 0x0A, 0x40])
    def test_rejects_uncurated_colors(self, color):
        with pytest.raises(ValueError, match="not a curated dress color"):
            peach_dress_patch(color)

    def test_inline_step(self):
        assert parse_step_arg("peach_dress:color=0x2A", Path(".")).params.color == 0x2A
