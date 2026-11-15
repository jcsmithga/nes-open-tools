"""The checks a hole passes to be opened in the editor and to reach a ROM."""

import re

import pytest

from golf.formats import hole_check
from golf.formats.hole_check import (
    compression_problem,
    editable_problem,
    hidden_rows_trimmed,
    hole_problems,
)
from golf.formats.hole_data import HoleData
from tests.synthetic_holes import synthetic_hole


def good_hole() -> HoleData:
    """A synthetic hole with the scroll limit and tee its 30 rows allow."""
    hole = synthetic_hole(2)
    hole.metadata["scroll_limit"] = 1
    hole.metadata["tee"] = {"x": 88, "y": 220}
    return hole


def test_a_good_hole_has_no_problems():
    hole = good_hole()
    assert hole_problems(hole) == []
    assert compression_problem(hole) is None
    assert editable_problem(hole) is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda hole: hole.metadata.update(scroll_limit=4), "scroll_limit must be 1"),
        (lambda hole: hole.metadata.update(par=6), "par must be one of"),
        (lambda hole: hole.metadata.update(par=4.0), "par must be one of"),
        (lambda hole: hole.metadata.update(scroll_limit=1.0), "scroll_limit must be 1"),
        (lambda hole: hole.metadata.update(tee={"x": 5, "y": 240}), "tee must be"),
        (lambda hole: hole.metadata.update(tee={"x": 176, "y": 20}), "tee must be"),
        (lambda hole: hole.metadata.update(tee={"x": 5}), "tee must be"),
        (lambda hole: hole.terrain[3].__setitem__(3, 0x100), r"terrain cells .* \$100"),
        (lambda hole: hole.terrain[3].pop(), "terrain must be 22 cells wide"),
        (lambda hole: hole.attributes[3].__setitem__(3, 4), "attributes cells"),
        (lambda hole: hole.greens.pop(), "greens must be 24 cells wide and 24 rows"),
        (lambda hole: hole.greens[2].__setitem__(2, -1), "greens cells"),
        (lambda hole: hole.metadata.update(handicap=256), "handicap must be 1-18"),
        (lambda hole: hole.metadata.update(distance=1000), "distance must be 1-999"),
        (lambda hole: hole.metadata.update(distance="190"), "distance must be 1-999"),
        (lambda hole: setattr(hole, "green_x", 256), "green's position"),
        (lambda hole: hole.metadata["flag_positions"].pop(), "flag_positions"),
        (
            lambda hole: hole.metadata["flag_positions"][0].update(x_offset=300),
            "flag_positions",
        ),
        (lambda hole: hole.attributes.pop(), "need 15 attribute rows"),
        (lambda hole: hole.attributes.append([1] * 11), "need 15 attribute rows"),
        (lambda hole: setattr(hole, "terrain_height", 29), "even and 30-60"),
        (lambda hole: setattr(hole, "terrain_height", 62), "even and 30-60"),
    ],
)
def test_a_value_a_rom_cannot_hold_is_a_problem(change, message):
    hole = good_hole()
    change(hole)
    problems = hole_problems(hole)
    assert len(problems) == 1
    assert re.search(message, problems[0])


def test_every_problem_is_named():
    hole = good_hole()
    hole.metadata.update(par=6, handicap=0)
    hole.green_x = -1
    assert len(hole_problems(hole)) == 3


def test_hidden_rows_are_trimmed_and_noted():
    hole = good_hole()
    hole.terrain += [[0x99] * 22, [0x99] * 22]
    hole.attributes.append([2] * 11)
    assert hole_problems(hole)  # the hidden attribute row would reach a ROM
    trimmed, notes = hidden_rows_trimmed(hole)
    assert len(trimmed.terrain) == 30
    assert len(trimmed.attributes) == 15
    assert notes == ("dropped 1 attribute rows below the visible 30 terrain rows",)
    assert hole_problems(trimmed) == []
    # the hole itself is left as it was
    assert len(hole.terrain) == 32 and len(hole.attributes) == 16
    assert hidden_rows_trimmed(trimmed)[1] == ()


def test_a_compressor_failure_is_the_problem(monkeypatch):
    def refuse(self, rows):
        raise ValueError("no pattern for this row")

    monkeypatch.setattr(hole_check.TerrainCompressor, "compress", refuse)
    assert compression_problem(good_hole()) == "no pattern for this row"


def test_the_editor_opens_any_hole_its_grids_cover():
    hole = good_hole()
    hole.greens.pop()
    assert (editable_problem(hole) or "").startswith("greens")
    hole = good_hole()
    hole.attributes = hole.attributes[:10]
    assert editable_problem(hole) == "attributes don't cover the terrain"
    hole = good_hole()
    hole.terrain_height = 40
    assert editable_problem(hole) == "terrain height doesn't match its rows"
