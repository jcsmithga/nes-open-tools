"""The Selection tool only edits the mode its selection or clipboard came from."""

import pygame
import pytest

from editor.controllers.editor_state import EditorState
from editor.controllers.highlight_state import HighlightState
from editor.core.constants import CANVAS_OFFSET_X, CANVAS_OFFSET_Y, TILE_SIZE
from editor.tools.base_tool import ToolContext
from editor.tools.selection_tool import DEFAULT_TILE, SelectionTool
from tests.synthetic_holes import synthetic_hole


@pytest.fixture
def context():
    return ToolContext(
        hole_data=synthetic_hole(),
        state=EditorState(),
        terrain_picker=None,
        greens_picker=None,
        forest_filler=None,
        screen_width=1280,
        screen_height=1200,
        highlight_state=HighlightState(),
    )


def screen(tile: tuple[int, int], context) -> tuple[int, int]:
    """The screen position of a tile's top left corner, plus a pixel."""
    row, col = tile
    size = TILE_SIZE * context.state.canvas_scale
    return (CANVAS_OFFSET_X + col * size + 1, CANVAS_OFFSET_Y + row * size + 1)


def select(tool, context, start, end):
    tool.handle_mouse_down(screen(start, context), 1, 0, context)
    tool.handle_mouse_motion(screen(end, context), context)
    tool.handle_mouse_up(screen(end, context), 1, context)


def test_delete_ignores_a_selection_made_in_the_other_mode(context):
    tool = SelectionTool()
    select(tool, context, (0, 0), (1, 1))
    greens = [row[:] for row in context.hole_data.greens]
    context.state.set_mode("greens")

    result = tool.handle_key_down(pygame.K_DELETE, 0, context)

    assert context.hole_data.greens == greens
    assert result.message == "No selection to delete"
    assert not context.state.undo_manager.can_undo()


def test_delete_clears_a_selection_in_its_own_mode(context):
    tool = SelectionTool()
    select(tool, context, (0, 0), (1, 1))

    tool.handle_key_down(pygame.K_DELETE, 0, context)

    assert context.hole_data.terrain[1][1] == DEFAULT_TILE


def test_a_terrain_clipboard_is_never_pasted_into_greens(context):
    tool = SelectionTool()
    select(tool, context, (0, 0), (1, 1))
    tool.handle_key_down(pygame.K_c, pygame.KMOD_CTRL, context)
    tool.handle_key_down(pygame.K_v, pygame.KMOD_CTRL, context)
    greens = [row[:] for row in context.hole_data.greens]
    context.state.set_mode("greens")

    result = tool.handle_mouse_down(screen((0, 0), context), 1, 0, context)

    assert context.hole_data.greens == greens
    assert (result.message or "").startswith("Cannot paste terrain")
