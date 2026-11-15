"""The green brush: painting and erasing a green's putting surface."""

import copy

import numpy as np

from golf.algorithms.feature_brush import (
    GREEN_MARGIN,
    GreenChange,
    green_change,
    green_stroke_mask,
    paint_green,
)
from golf.algorithms.feature_fit import green_grid, load_fitters
from golf.algorithms.green_zones import (
    FLAT_TILE,
    FRINGE_TILES,
    GREEN,
    PLACEHOLDER,
    ROUGH,
    ROUGH_FAMILIES,
    ROUGH_TILES,
)
from golf.formats.hole_data import HoleData
from tests.synthetic_holes import synthetic_hole

SLOPE = 0x33


def line(start, end, steps=40):
    return [
        (
            round(start[0] + (end[0] - start[0]) * step / steps),
            round(start[1] + (end[1] - start[1]) * step / steps),
        )
        for step in range(steps + 1)
    ]


def rough_hole() -> HoleData:
    """A synthetic hole whose green is all rough, for the brush to paint."""
    hole = synthetic_hole()
    hole.greens = HoleData.blank().greens
    return hole


def zones(hole) -> np.ndarray:
    family = load_fitters()["green"].family
    return family.render(green_grid(family, hole))


def tiles(hole) -> np.ndarray:
    return np.array(hole.greens)


def a_green():
    """A hole with a green painted on it, about 80 by 50 pixels."""
    hole = rough_hole()
    paint_green(hole, green_stroke_mask(line((70, 96), (120, 96)), 25))
    return hole


def fringe_neighbors(hole) -> list[int]:
    """For each fringe cell, how many of the four cells beside it are fringe too."""
    fringe = np.pad(np.isin(tiles(hole), FRINGE_TILES), 1)
    beside = (
        fringe[:-2, 1:-1].astype(int)
        + fringe[2:, 1:-1]
        + fringe[1:-1, :-2]
        + fringe[1:-1, 2:]
    )
    return beside[fringe[1:-1, 1:-1]].tolist()


def test_stroke_mask_is_a_disc_in_the_greens_pixels():
    mask = green_stroke_mask([(100, 60)], 5)
    assert mask.shape == (192, 192)
    assert mask[60, 100] and mask[60, 105] and not mask[60, 106]
    assert not green_stroke_mask([(400, 400)], 5).any()


def test_painting_on_rough_draws_a_green_with_fringe_all_round():
    hole = rough_hole()
    stroke = green_stroke_mask(line((70, 96), (120, 96)), 25)
    assert paint_green(hole, stroke) > 40
    made = zones(hole)
    green = made == GREEN
    # the putting surface is the stroke, to within the fit
    assert (green != stroke).sum() < 0.08 * stroke.sum()
    # no putting surface touches rough, and the fringe is one closed ring of cells
    for a, b in ((made[:, :-1], made[:, 1:]), (made[:-1], made[1:])):
        assert not ((a == GREEN) & (b == ROUGH)).any()
        assert not ((a == ROUGH) & (b == GREEN)).any()
    assert set(fringe_neighbors(hole)) == {2}
    assert set(tiles(hole).flat) <= {FLAT_TILE, *FRINGE_TILES, *ROUGH_TILES}
    assert FLAT_TILE in tiles(hole)


def test_the_rough_keeps_its_checkerboard_and_gains_strips():
    hole = rough_hole()
    # its top edge four pixels into a row of cells, which `$64` draws
    paint_green(hole, green_stroke_mask(line((70, 96), (120, 96)), 28))
    grid = tiles(hole)
    for row in range(24):
        for col in range(24):
            if grid[row, col] in ROUGH_TILES:
                assert grid[row, col] in ROUGH_FAMILIES[(row + col) % 2]
    for row, col in zip(*np.nonzero(grid == 0x64), strict=True):
        assert grid[row - 1, col] in (0x71, 0x85)
    for row, col in zip(*np.nonzero(grid == 0x66), strict=True):
        assert grid[row, col - 1] in (0x70, 0x84)
    assert (grid == 0x64).sum() >= 3


def test_a_green_checkered_the_other_way_stays_so():
    hole = rough_hole()
    hole.greens = [[0x29 + 0x2C - tile for tile in row] for row in hole.greens]
    paint_green(hole, green_stroke_mask(line((70, 96), (120, 96)), 25))
    paint_green(hole, green_stroke_mask([(96, 96)], 12), erase=True)
    grid = tiles(hole)
    for row in range(24):
        for col in range(24):
            if grid[row, col] in ROUGH_TILES:
                assert grid[row, col] in ROUGH_FAMILIES[(row + col + 1) % 2]


def test_painting_onto_a_green_widens_it():
    hole = a_green()
    before = zones(hole) == GREEN
    stroke = green_stroke_mask(line((130, 96), (160, 80)), 10)
    assert paint_green(hole, stroke)
    after = zones(hole) == GREEN
    assert after.sum() > before.sum() + 0.7 * (stroke & ~before).sum()
    assert after[80, 158] and not before[80, 158]
    assert set(fringe_neighbors(hole)) <= {2, 3}


def test_erasing_cuts_the_stroke_out_and_erasing_everything_leaves_rough():
    hole = a_green()
    before = zones(hole) == GREEN
    stroke = green_stroke_mask(line((120, 70), (150, 110)), 12)
    assert paint_green(hole, stroke, erase=True)
    after = zones(hole) == GREEN
    # the cut's edge is the fit's to place, within a pixel or two
    assert (after & ~before).sum() < 20
    assert (after & stroke).sum() < 0.1 * (before & stroke).sum()

    paint_green(hole, np.ones((192, 192), bool), erase=True)
    assert set(tiles(hole).flat) == {0x29, 0x2C}
    assert hole.greens == rough_hole().greens


def test_only_cells_near_the_stroke_change():
    hole = a_green()
    before = tiles(hole)
    paint_green(hole, green_stroke_mask([(150, 96)], 10))
    changed = np.argwhere(tiles(hole) != before)
    assert len(changed)
    assert changed[:, 1].min() >= 150 // 8 - 4


def test_slopes_are_kept_except_where_the_fringe_moves_onto_them():
    hole = a_green()
    for row, col in ((11, 9), (11, 10), (12, 13), (12, 14)):
        assert hole.greens[row][col] == FLAT_TILE
        hole.greens[row][col] = SLOPE
    # widening the far end leaves them all
    paint_green(hole, green_stroke_mask([(60, 96)], 14))
    assert all(hole.greens[row][col] == SLOPE for row, col in ((11, 9), (12, 14)))
    # cutting through two of them loses those and keeps the others
    paint_green(hole, green_stroke_mask(line((116, 60), (116, 130)), 6), erase=True)
    assert hole.greens[11][9] == hole.greens[11][10] == SLOPE
    assert hole.greens[12][14] != SLOPE
    # and what a stroke adds is flat
    assert SLOPE not in {hole.greens[row][col] for row in range(24) for col in (4, 5)}


def test_a_stroke_that_changes_nothing_is_an_empty_change():
    hole = a_green()
    before = copy.deepcopy(hole.greens)
    assert not green_change(hole, green_stroke_mask([(96, 96)], 8))
    assert not green_change(hole, green_stroke_mask([(20, 20)], 8), erase=True)
    assert not green_change(hole, np.zeros((192, 192), bool))
    assert (
        green_change(hole, green_stroke_mask([(20, 20)], 8), erase=True)
        == GreenChange()
    )
    assert hole.greens == before


def test_a_change_holds_plain_ints_and_applies_to_the_green_only():
    hole = rough_hole()
    terrain = copy.deepcopy(hole.terrain)
    change = green_change(hole, green_stroke_mask(line((70, 96), (120, 96)), 20))
    assert change
    assert all(type(value) is int for cell in change.tiles for value in cell)
    change.apply(hole)
    assert hole.terrain == terrain
    assert all(hole.greens[row][col] == tile for row, col, tile in change.tiles)


def test_the_editors_placeholder_reads_as_rough_and_is_left_where_nothing_changes():
    hole = rough_hole()
    hole.greens = [[PLACEHOLDER] * 24 for _ in range(24)]
    paint_green(hole, green_stroke_mask(line((70, 96), (120, 96)), 25))
    grid = tiles(hole)
    assert grid[0, 0] == PLACEHOLDER
    assert (np.isin(grid, FRINGE_TILES)).sum() > 20
    assert set(fringe_neighbors(hole)) == {2}


def test_a_green_painted_to_the_edge_stops_short_of_it_with_its_fringe():
    hole = rough_hole()
    paint_green(hole, green_stroke_mask(line((150, 96), (191, 96)), 20))
    made = zones(hole)
    # the fringe's last tile is the fit's to choose, a pixel or two either way
    assert not (made[:, -(GREEN_MARGIN - 2) :] == GREEN).any()
    assert (made[90:102, -3:] != ROUGH).all()
    assert hole.greens[12][23] in FRINGE_TILES
    assert hole.greens[12][21] == FLAT_TILE


def test_putting_surface_already_at_the_edge_is_left_there():
    hole = rough_hole()
    for row in range(8, 16):
        hole.greens[row][18:24] = [FLAT_TILE] * 6
    paint_green(hole, green_stroke_mask([(150, 96)], 12))
    assert hole.greens[12][23] == FLAT_TILE
