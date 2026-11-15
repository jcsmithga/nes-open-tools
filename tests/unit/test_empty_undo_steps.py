"""An edit that changes nothing leaves no step on the undo stack."""

from unittest.mock import Mock

import pygame
import pytest

from editor.controllers.editor_state import EditorState
from editor.controllers.highlight_state import HighlightState
from editor.core.constants import CANVAS_OFFSET_X, CANVAS_OFFSET_Y, TILE_SIZE
from editor.data import StampData
from editor.tools.base_tool import ToolContext, write_tiles
from editor.tools.metadata_editor_tool import MetadataEditorTool
from editor.tools.selection_tool import DEFAULT_TILE, SelectionTool
from editor.tools.stamp_tool import StampTool
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
        on_revert_to_previous_tool=Mock(),
    )


def undo_steps(context) -> int:
    return len(context.state.undo_manager.undo_stack)


def screen(tile: tuple[int, int], context) -> tuple[int, int]:
    row, col = tile
    size = TILE_SIZE * context.state.canvas_scale
    return (CANVAS_OFFSET_X + col * size + 1, CANVAS_OFFSET_Y + row * size + 1)


def test_write_tiles_pushes_one_step_and_counts_only_changes(context):
    terrain = context.hole_data.terrain
    terrain[0][1] = terrain[0][2] = 0x31
    count = write_tiles(context, [(0, 0, terrain[0][0]), (0, 1, 0x30), (0, 2, 0x30)])

    assert count == 2
    assert terrain[0][1:3] == [0x30, 0x30]
    assert undo_steps(context) == 1


def test_write_tiles_that_change_nothing_push_nothing(context):
    terrain = context.hole_data.terrain
    count = write_tiles(context, [(0, 0, terrain[0][0]), (999, 0, 0x30), (0, -1, 0x30)])

    assert count == 0
    assert undo_steps(context) == 0
    assert not context.hole_data.modified


def test_a_stamp_over_the_same_tiles_pushes_nothing(context):
    stamp = StampData()
    stamp.tiles = [[context.hole_data.terrain[0][0]]]
    stamp.width = stamp.height = 1
    tool = StampTool()
    tool.set_stamp(stamp)

    tool.handle_mouse_down(screen((0, 0), context), 1, 0, context)

    assert undo_steps(context) == 0


def test_deleting_an_empty_region_pushes_nothing(context):
    tool = SelectionTool()
    for col in range(2):
        context.hole_data.terrain[0][col] = DEFAULT_TILE
    tool.handle_mouse_down(screen((0, 0), context), 1, 0, context)
    tool.handle_mouse_motion(screen((0, 1), context), context)
    tool.handle_mouse_up(screen((0, 1), context), 1, context)

    result = tool.handle_key_down(pygame.K_DELETE, 0, context)

    assert undo_steps(context) == 0
    assert result.message == "2x1 region is already empty"


@pytest.fixture
def metadata_tool(context):
    pygame.font.init()
    tool = MetadataEditorTool()
    tool.on_activated(context)
    return tool


def type_par(tool, par: str):
    """Replace the par field's text, as typing into it would."""
    assert tool.dialog is not None
    tool.dialog.par_value = par
    tool.dialog.par_valid = True


def test_canceling_the_metadata_dialog_pushes_nothing(metadata_tool, context):
    metadata_tool.handle_key_down(pygame.K_ESCAPE, 0, context)

    assert undo_steps(context) == 0
    assert not context.hole_data.modified


def test_saving_unchanged_metadata_pushes_nothing(metadata_tool, context):
    result = metadata_tool.handle_key_down(pygame.K_RETURN, 0, context)

    assert result.message == "Metadata unchanged"
    assert undo_steps(context) == 0
    assert not context.hole_data.modified


def test_saving_changed_metadata_pushes_one_undoable_step(metadata_tool, context):
    par = context.hole_data.metadata["par"]
    type_par(metadata_tool, str(par + 1 if par < 5 else par - 1))

    metadata_tool.handle_key_down(pygame.K_RETURN, 0, context)

    assert undo_steps(context) == 1
    assert context.hole_data.metadata["par"] != par
    restored = context.state.undo_manager.undo(context.hole_data)
    assert restored is not None
    assert restored.metadata["par"] == par
