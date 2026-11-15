"""The editor's Feature Brush tool, and the Out of Bounds Brush built on it."""

from unittest.mock import Mock

import pygame
import pytest

from editor.controllers.highlight_state import HighlightState
from editor.core.constants import CANVAS_OFFSET_X, CANVAS_OFFSET_Y
from editor.tools.base_tool import ToolContext
from editor.tools.feature_brush_tool import (
    DEFAULT_RADIUS,
    MAX_RADIUS,
    MIN_RADIUS,
    BoundaryBrushTool,
    FeatureBrushTool,
    GreenBrushTool,
)
from golf.algorithms.boundary import LINE_TILES, PLACEHOLDER
from golf.algorithms.green_zones import FLAT_TILE, FRINGE_TILES
from golf.core.palettes import TERRAIN_WIDTH
from golf.formats.hole_data import HoleData
from tests.synthetic_holes import synthetic_hole

ROUGH = 0xDF
SCALE = 4


@pytest.fixture
def hole():
    hole = synthetic_hole()
    hole.terrain = [[ROUGH] * TERRAIN_WIDTH for _ in range(30)]
    hole.attributes = [[1] * 11 for _ in range(15)]
    # a green all rough, for the Green Brush to paint
    hole.greens = HoleData.blank().greens
    return hole


@pytest.fixture
def context(hole):
    state = Mock()
    state.mode = "terrain"
    state.selected_palette = 1
    state.canvas_offset_x = 0
    state.canvas_offset_y = 0
    state.canvas_scale = SCALE
    return ToolContext(
        hole_data=hole,
        state=state,
        terrain_picker=Mock(),
        greens_picker=Mock(),
        forest_filler=Mock(),
        screen_width=1280,
        screen_height=1200,
        highlight_state=HighlightState(),
    )


def screen(x: int, y: int) -> tuple[int, int]:
    """The screen position of a game pixel."""
    return (CANVAS_OFFSET_X + x * SCALE + 1, CANVAS_OFFSET_Y + y * SCALE + 1)


def drag(tool, context, points, button=1):
    tool.handle_mouse_down(screen(*points[0]), button, 0, context)
    for point in points[1:]:
        tool.handle_mouse_motion(screen(*point), context)
    return tool.handle_mouse_up(screen(*points[-1]), button, context)


def placed(hole) -> int:
    return sum(tile != ROUGH for row in hole.terrain for tile in row)


def test_hotkey_is_b():
    assert FeatureBrushTool().get_hotkey() == pygame.K_b


def test_a_drag_paints_on_release_and_pushes_undo_once(hole, context):
    tool = FeatureBrushTool()
    tool.handle_mouse_down(screen(60, 100), 1, 0, context)
    tool.handle_mouse_motion(screen(100, 110), context)
    assert placed(hole) == 0
    context.state.undo_manager.push_state.assert_not_called()

    result = tool.handle_mouse_up(screen(100, 110), 1, context)
    assert "Painted" in (result.message or "")
    assert placed(hole) > 0
    context.state.undo_manager.push_state.assert_called_once_with(hole)


def test_a_fast_drag_leaves_no_gaps(hole, context):
    tool = FeatureBrushTool()
    drag(tool, context, [(40, 100), (120, 100)])
    # every column between the two ends is painted along the stroke's middle
    assert all(hole.terrain[12][col] != ROUGH for col in range(5, 16))


def test_the_selected_palette_decides_what_is_painted(hole, context):
    context.state.selected_palette = 3
    drag(FeatureBrushTool(), context, [(60, 100), (100, 110)])
    assert hole.get_attribute(13, 10) == 3


def test_right_drag_erases(hole, context):
    tool = FeatureBrushTool()
    drag(tool, context, [(60, 100), (100, 110)])
    tool.radius = MAX_RADIUS
    result = drag(tool, context, [(50, 95), (110, 115)], button=3)
    assert "Erased" in (result.message or "")
    assert placed(hole) == 0
    assert context.state.undo_manager.push_state.call_count == 2


def test_a_stroke_that_changes_nothing_pushes_no_undo(hole, context):
    result = drag(FeatureBrushTool(), context, [(60, 100), (100, 110)], button=3)
    assert result.is_handled and "Nothing to change" in (result.message or "")
    context.state.undo_manager.push_state.assert_not_called()


def test_the_other_button_does_not_end_a_stroke(hole, context):
    tool = FeatureBrushTool()
    tool.handle_mouse_down(screen(60, 100), 1, 0, context)
    assert not tool.handle_mouse_up(screen(60, 100), 3, context).is_handled
    assert tool.stroke_button == 1


def test_escape_cancels_the_stroke(hole, context):
    tool = FeatureBrushTool()
    tool.handle_mouse_down(screen(60, 100), 1, 0, context)
    assert tool.handle_key_down(pygame.K_ESCAPE, 0, context).is_handled
    tool.handle_mouse_up(screen(60, 100), 1, context)
    assert placed(hole) == 0
    assert context.highlight_state.feature_brush_points is None


def test_brackets_are_left_to_change_the_flag(context):
    tool = FeatureBrushTool()
    for key in (pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET):
        assert not tool.handle_key_down(key, 0, context).is_handled
    assert tool.radius == DEFAULT_RADIUS


def test_comma_and_period_change_the_radius_within_limits(context):
    tool = FeatureBrushTool()
    assert tool.radius == DEFAULT_RADIUS
    tool.handle_key_down(pygame.K_PERIOD, 0, context)
    assert tool.radius == DEFAULT_RADIUS + 1
    assert context.highlight_state.feature_brush_radius == DEFAULT_RADIUS + 1
    for _ in range(40):
        tool.handle_key_down(pygame.K_COMMA, 0, context)
    assert tool.radius == MIN_RADIUS
    for _ in range(40):
        tool.handle_key_down(pygame.K_PERIOD, 0, context)
    assert tool.radius == MAX_RADIUS


def test_other_keys_are_left_to_the_editor(context):
    assert not FeatureBrushTool().handle_key_down(pygame.K_g, 0, context).is_handled
    assert (
        not FeatureBrushTool().handle_key_down(pygame.K_ESCAPE, 0, context).is_handled
    )


def test_greens_mode_is_refused(hole, context):
    context.state.mode = "greens"
    tool = FeatureBrushTool()
    result = tool.handle_mouse_down(screen(60, 100), 1, 0, context)
    assert result.is_handled and "terrain" in (result.message or "")
    assert tool.stroke_button is None


def test_highlight_follows_the_cursor_and_the_stroke(context):
    tool = FeatureBrushTool()
    highlight = context.highlight_state
    tool.handle_mouse_motion(screen(60, 100), context)
    assert highlight.feature_brush_cursor == (60, 100)
    assert highlight.feature_brush_points is None

    tool.handle_mouse_down(screen(60, 100), 3, 0, context)
    tool.handle_mouse_motion(screen(63, 100), context)
    assert highlight.feature_brush_points == [
        (60, 100),
        (61, 100),
        (62, 100),
        (63, 100),
    ]
    assert highlight.feature_brush_erasing

    tool.handle_mouse_up(screen(63, 100), 3, context)
    assert highlight.feature_brush_points is None


def test_deactivating_clears_the_overlay(context):
    tool = FeatureBrushTool()
    tool.handle_mouse_down(screen(60, 100), 1, 0, context)
    tool.on_deactivated(context)
    assert tool.stroke_button is None
    assert context.highlight_state.feature_brush_cursor is None
    assert context.highlight_state.feature_brush_points is None


def test_the_out_of_bounds_brush_is_on_o():
    assert BoundaryBrushTool().get_hotkey() == pygame.K_o


def test_the_out_of_bounds_brush_draws_a_line_round_placeholder(hole, context):
    context.state.selected_palette = 2  # the palette makes no difference to it
    result = drag(BoundaryBrushTool(), context, [(40, 100), (120, 110)])
    assert "Out of Bounds Brush: Painted" in (result.message or "")
    tiles = {tile for row in hole.terrain for tile in row}
    assert PLACEHOLDER in tiles
    assert tiles - {ROUGH, PLACEHOLDER} <= set(LINE_TILES)
    context.state.undo_manager.push_state.assert_called_once_with(hole)


def test_the_green_brush_is_on_n():
    assert GreenBrushTool().get_hotkey() == pygame.K_n


def test_the_green_brush_paints_the_green_in_greens_mode(hole, context):
    context.state.mode = "greens"
    terrain = [row[:] for row in hole.terrain]
    tool = GreenBrushTool()
    tool.radius = MAX_RADIUS
    result = drag(tool, context, [(70, 96), (120, 96)])
    assert result.is_handled
    assert "Green Brush: Painted" in (result.message or "")
    tiles = {tile for row in hole.greens for tile in row}
    assert FLAT_TILE in tiles and tiles & set(FRINGE_TILES)
    assert hole.terrain == terrain
    context.state.undo_manager.push_state.assert_called_once_with(hole)

    tool.radius = MAX_RADIUS
    result = drag(tool, context, [(60, 96), (130, 96)], button=3)
    assert "Green Brush: Erased" in (result.message or "")
    assert FLAT_TILE not in {tile for row in hole.greens for tile in row}


def test_the_green_brush_is_refused_in_terrain_mode(hole, context):
    tool = GreenBrushTool()
    result = tool.handle_mouse_down(screen(60, 100), 1, 0, context)
    assert result.is_handled and "greens" in (result.message or "")
    assert tool.stroke_button is None


def test_a_brush_in_the_other_mode_drops_its_cursor(context):
    tool = FeatureBrushTool()
    tool.handle_mouse_motion(screen(60, 100), context)
    assert context.highlight_state.feature_brush_cursor == (60, 100)
    context.state.mode = "greens"
    assert not tool.handle_mouse_motion(screen(64, 100), context).is_handled
    assert context.highlight_state.feature_brush_cursor is None
