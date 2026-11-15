"""Unit tests for EventHandler keyboard shortcut handling."""

from unittest.mock import Mock, patch

import pygame
import pytest

from editor.controllers.editor_state import EditorState, GridMode
from editor.controllers.event_handler import EventHandler
from editor.controllers.highlight_state import HighlightState
from editor.core.constants import CANVAS_OFFSET_X, CANVAS_OFFSET_Y, STATUS_HEIGHT
from editor.tools.carpet_paint_tool import CarpetPaintTool
from editor.tools.cycle_tool import CycleTool
from editor.tools.feature_brush_tool import (
    BoundaryBrushTool,
    FeatureBrushTool,
    GreenBrushTool,
)
from editor.tools.forest_fill_tool import ForestFillTool
from editor.tools.paint_tool import PaintTool
from editor.tools.tool_manager import ToolManager
from golf.formats.hole_data import HoleData


@pytest.fixture
def mock_pygame():
    """Initialize pygame for tests."""
    pygame.init()
    yield
    pygame.quit()


@pytest.fixture
def editor_state():
    """Create a fresh EditorState instance."""
    return EditorState()


@pytest.fixture
def hole_data():
    """Create simple hole data for testing."""
    hole = HoleData()
    hole.terrain = [[0x10, 0x11, 0x12] for _ in range(5)]
    hole.attributes = [[1, 1] for _ in range(3)]
    hole.greens = [[0x20, 0x21, 0x22] for _ in range(4)]
    return hole


@pytest.fixture
def tool_manager():
    """Create ToolManager with registered tools."""
    manager = ToolManager()
    manager.register_tool("paint", PaintTool())
    manager.register_tool("forest_fill", ForestFillTool())
    return manager


@pytest.fixture
def event_handler(editor_state, hole_data, tool_manager):
    """Create EventHandler with minimal setup."""
    # Create mock pickers
    mock_terrain_picker = Mock()
    mock_greens_picker = Mock()
    mock_tool_picker = Mock()
    mock_stamp_browser = Mock()

    # Create mock callbacks
    mock_on_load = Mock()
    mock_on_load_file = Mock()
    mock_on_save = Mock()
    mock_on_select_flag = Mock()
    mock_on_resize = Mock()
    mock_on_tool_change = Mock()

    handler = EventHandler(
        state=editor_state,
        hole_data=hole_data,
        terrain_picker=mock_terrain_picker,
        greens_picker=mock_greens_picker,
        buttons=[],
        screen_width=1200,
        screen_height=1200,
        tool_manager=tool_manager,
        tool_picker=mock_tool_picker,
        stamp_browser=mock_stamp_browser,
        on_new=Mock(),
        on_load=mock_on_load,
        on_load_file=mock_on_load_file,
        on_save=mock_on_save,
        on_save_as=Mock(),
        on_toggle_mode=Mock(),
        on_select_flag=mock_on_select_flag,
        on_set_palette=Mock(),
        on_resize=mock_on_resize,
        on_tool_change=mock_on_tool_change,
        on_create_stamp=Mock(),
    )

    # Set tool context properties (normally done by Application)

    # Mock forest filler with proper return values
    mock_filler = Mock()
    mock_filler.detect_regions.return_value = []  # Default: no regions
    mock_filler.fill_region.return_value = {}  # Default: no changes
    handler.tool_context.forest_filler = mock_filler

    # Activate paint tool
    tool_manager.set_active_tool("paint", handler.tool_context)

    return handler


class MockEvent:
    """Mock pygame event."""

    def __init__(self, type, **kwargs):
        self.type = type
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.mark.parametrize(
    "key, palette", [(pygame.K_1, 1), (pygame.K_2, 2), (pygame.K_3, 3)]
)
def test_number_keys_select_the_palette_through_the_application(
    mock_pygame, event_handler, key, palette
):
    """The 1-3 keys go through on_set_palette, so the toolbar buttons follow."""
    with patch("pygame.key.get_mods", return_value=0):
        event_handler.handle_events([MockEvent(pygame.KEYDOWN, key=key)])

    event_handler.on_set_palette.assert_called_once_with(palette)


class TestToolHotkeys:
    """Tests for tool activation hotkeys."""

    def test_f_key_activates_forest_fill_tool(
        self, mock_pygame, event_handler, tool_manager
    ):
        """'F' key should activate the forest fill tool."""
        # Create F key event (no modifiers)
        event = MockEvent(pygame.KEYDOWN, key=pygame.K_f)

        # Mock pygame.key.get_mods to return no modifiers
        with patch("pygame.key.get_mods", return_value=0):
            # Handle the event
            running = event_handler.handle_events([event])

            # Verify forest fill tool is now active
            active_tool = tool_manager.get_active_tool()
            forest_fill_tool = tool_manager.get_tool("forest_fill")
            assert active_tool is forest_fill_tool, "Forest fill should be active tool"
            assert running is True, "Event handler should continue running"

    def test_p_key_activates_paint_tool(self, mock_pygame, event_handler, tool_manager):
        """'P' key should activate the paint tool."""
        # First activate a different tool
        tool_manager.set_active_tool("forest_fill", event_handler.tool_context)

        # Create P key event
        event = MockEvent(pygame.KEYDOWN, key=pygame.K_p)

        with patch("pygame.key.get_mods", return_value=0):
            running = event_handler.handle_events([event])

            # Verify paint tool is now active
            active_tool = tool_manager.get_active_tool()
            paint_tool = tool_manager.get_tool("paint")
            assert active_tool is paint_tool, "Paint should be active tool"
            assert running is True

    def test_f_key_works_in_any_mode(self, mock_pygame, event_handler, tool_manager):
        """'F' key should activate forest fill regardless of mode."""
        for mode in ["terrain", "palette", "greens"]:
            event_handler.state.set_mode(mode)
            event = MockEvent(pygame.KEYDOWN, key=pygame.K_f)

            with patch("pygame.key.get_mods", return_value=0):
                event_handler.handle_events([event])

                # Verify forest fill is active
                active_tool = tool_manager.get_active_tool()
                forest_fill_tool = tool_manager.get_tool("forest_fill")
                assert active_tool is forest_fill_tool, (
                    f"Forest fill should be active in {mode} mode"
                )

    def test_unknown_hotkey_not_handled(self, mock_pygame, event_handler):
        """Unknown hotkey should not be handled by global handler."""
        # Use a key that's not registered as a tool hotkey
        event = MockEvent(pygame.KEYDOWN, key=pygame.K_q)

        with patch("pygame.key.get_mods", return_value=0):
            # Spy on the active tool's handle_key_down
            active_tool = event_handler.tool_manager.get_active_tool()
            call_count = 0
            original = active_tool.handle_key_down

            def tracked(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                return original(*args, **kwargs)

            active_tool.handle_key_down = tracked

            event_handler.handle_events([event])

            # Active tool should receive the unhandled Q key
            assert call_count == 1, "Active tool should receive unhandled Q key"


class TestOtherGlobalShortcuts:
    """Tests for other global keyboard shortcuts to ensure they still work."""

    def test_ctrl_s_triggers_save(self, mock_pygame, event_handler):
        """Ctrl+S should trigger save callback."""
        event = MockEvent(pygame.KEYDOWN, key=pygame.K_s)

        with patch("pygame.key.get_mods", return_value=pygame.KMOD_CTRL):
            event_handler.handle_events([event])
            event_handler.on_save.assert_called_once()

    @pytest.mark.parametrize(
        "key, mods, callback",
        [
            (pygame.K_s, pygame.KMOD_CTRL | pygame.KMOD_SHIFT, "on_save_as"),
            (pygame.K_n, pygame.KMOD_CTRL, "on_new"),
            (pygame.K_c, pygame.KMOD_CTRL | pygame.KMOD_SHIFT, "on_create_stamp"),
        ],
    )
    def test_file_and_stamp_shortcuts(
        self, mock_pygame, event_handler, key, mods, callback
    ):
        others = {"on_save", "on_save_as", "on_new", "on_create_stamp"} - {callback}
        with patch("pygame.key.get_mods", return_value=mods):
            event_handler.handle_events([MockEvent(pygame.KEYDOWN, key=key)])
        getattr(event_handler, callback).assert_called_once()
        for other in others:
            getattr(event_handler, other).assert_not_called()

    def test_ctrl_shift_c_creates_a_stamp_rather_than_copying(
        self, mock_pygame, event_handler, tool_manager
    ):
        """The Selection tool sees keys first; it leaves Ctrl+Shift+C to the editor."""
        from editor.tools.selection_tool import SelectionTool

        tool_manager.register_tool("selection", SelectionTool())
        tool_manager.set_active_tool("selection", event_handler.tool_context)
        mods = pygame.KMOD_CTRL | pygame.KMOD_SHIFT
        with (
            patch("pygame.key.get_mods", return_value=mods),
            patch.object(
                tool_manager.get_active_tool(), "_copy_selection"
            ) as copy_selection,
        ):
            event_handler.handle_events([MockEvent(pygame.KEYDOWN, key=pygame.K_c)])
        copy_selection.assert_not_called()
        event_handler.on_create_stamp.assert_called_once()

    def test_ctrl_z_triggers_undo(self, mock_pygame, event_handler, hole_data):
        """Ctrl+Z should trigger undo if available."""
        # Push a state to enable undo
        event_handler.state.undo_manager.push_state(hole_data)

        event = MockEvent(pygame.KEYDOWN, key=pygame.K_z)

        with patch("pygame.key.get_mods", return_value=pygame.KMOD_CTRL):
            assert event_handler.state.undo_manager.can_undo()
            event_handler.handle_events([event])
            # After undo, should not be able to undo again (only had 1 state)
            assert not event_handler.state.undo_manager.can_undo()

    def test_f1_to_f4_select_the_flag_position(self, mock_pygame, event_handler):
        """F1-F4 select flag positions 0-3, as the toolbar's buttons of those names do."""
        keys = (pygame.K_F1, pygame.K_F2, pygame.K_F3, pygame.K_F4)
        with patch("pygame.key.get_mods", return_value=0):
            for index, key in enumerate(keys):
                event_handler.handle_events([MockEvent(pygame.KEYDOWN, key=key)])
                event_handler.on_select_flag.assert_called_with(index)
            event_handler.on_select_flag.reset_mock()
            for key in (pygame.K_LEFTBRACKET, pygame.K_RIGHTBRACKET):
                event_handler.handle_events([MockEvent(pygame.KEYDOWN, key=key)])
            event_handler.on_select_flag.assert_not_called()

    def test_g_cycles_grid_mode(self, mock_pygame, event_handler):
        """G key should cycle through grid modes: TILE -> SUPERTILE -> OFF -> TILE."""
        # Start at TILE (default)
        assert event_handler.state.grid_mode == GridMode.TILE
        event = MockEvent(pygame.KEYDOWN, key=pygame.K_g)

        with patch("pygame.key.get_mods", return_value=0):
            # First press: TILE -> SUPERTILE
            event_handler.handle_events([event])
            assert event_handler.state.grid_mode == GridMode.SUPERTILE

            # Second press: SUPERTILE -> OFF
            event_handler.handle_events([event])
            assert event_handler.state.grid_mode == GridMode.OFF

            # Third press: OFF -> TILE
            event_handler.handle_events([event])
            assert event_handler.state.grid_mode == GridMode.TILE


class TestEventHandlerDelegation:
    """Tests for event handler delegation to tools."""

    def test_active_tool_receives_unhandled_keys(self, mock_pygame, event_handler):
        """Active tool should receive keys not handled by global handler."""
        active_tool = event_handler.tool_manager.get_active_tool()

        # Mock the tool's handle_key_down to track calls
        original = active_tool.handle_key_down
        call_args = []

        def tracked(*args, **kwargs):
            call_args.append((args, kwargs))
            return original(*args, **kwargs)

        active_tool.handle_key_down = tracked

        # Send a key that's not handled globally (e.g., 'A')
        event = MockEvent(pygame.KEYDOWN, key=pygame.K_a)

        with patch("pygame.key.get_mods", return_value=0):
            event_handler.handle_events([event])

        # Tool should have received the key
        assert len(call_args) == 1, "Tool should receive unhandled key event"
        assert call_args[0][0][0] == pygame.K_a, "Tool should receive correct key"

    def test_quit_event_stops_application(self, mock_pygame, event_handler):
        """QUIT event should make handle_events return False."""
        event = MockEvent(pygame.QUIT)
        running = event_handler.handle_events([event])
        assert running is False, "QUIT event should return False to stop application"


def test_tab_asks_the_application_to_switch_modes(mock_pygame, event_handler):
    """Tab goes through on_toggle_mode, so the application can swap mode-bound tools."""
    with patch("pygame.key.get_mods", return_value=0):
        event_handler.handle_events([MockEvent(pygame.KEYDOWN, key=pygame.K_TAB)])

    event_handler.on_toggle_mode.assert_called_once_with()
    assert event_handler.state.mode == "terrain"


@pytest.mark.parametrize("allowed", [True, False])
def test_quit_asks_on_quit_first(mock_pygame, event_handler, allowed):
    """on_quit can keep the editor open, e.g. when the user cancels over unsaved work."""
    event_handler.on_quit = Mock(return_value=allowed)

    running = event_handler.handle_events([MockEvent(pygame.QUIT)])

    assert running is not allowed
    event_handler.on_quit.assert_called_once_with()


def test_update_screen_size_reaches_tool_context(event_handler):
    """Tools read the screen size from the context, so a resize must update it."""
    event_handler.update_screen_size(1500, 900)

    assert event_handler.screen_width == 1500
    assert event_handler.tool_context.screen_width == 1500
    assert event_handler.tool_context.screen_height == 900
    assert (
        event_handler.tool_context.view_state.canvas_rect.bottom == 900 - STATUS_HEIGHT
    )


def click_canvas(event_handler, button=1):
    """A press, drag and release on the canvas, with no picker taking the events."""
    for picker in (
        event_handler.terrain_picker,
        event_handler.greens_picker,
        event_handler.tool_picker,
    ):
        picker.handle_event.return_value = False
    pos = (CANVAS_OFFSET_X + 40, CANVAS_OFFSET_Y + 40)
    moved = (pos[0] + 40, pos[1] + 40)
    event_handler.handle_events(
        [
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=button),
            pygame.event.Event(pygame.MOUSEMOTION, pos=moved, rel=(40, 40), buttons=()),
            pygame.event.Event(pygame.MOUSEBUTTONUP, pos=moved, button=button),
        ]
    )


@pytest.mark.parametrize(
    ("tool", "mode"),
    [
        (PaintTool, "greens"),
        (CarpetPaintTool, "greens"),
        (CycleTool, "greens"),
        (FeatureBrushTool, "terrain"),
        (BoundaryBrushTool, "terrain"),
        (GreenBrushTool, "greens"),
    ],
)
def test_with_no_hole_open_the_canvas_tools_get_no_mouse_events(
    mock_pygame, event_handler, tool_manager, editor_state, tool, mode
):
    event_handler.hole_data.__init__()
    editor_state.mode = mode
    event_handler.tool_context.highlight_state = HighlightState()
    tool_manager.hotkey_map.clear()  # the fixture's Paint Tool has P already
    tool_manager.register_tool("under_test", tool())
    tool_manager.set_active_tool("under_test", event_handler.tool_context)

    click_canvas(event_handler)
    click_canvas(event_handler, button=3)

    assert event_handler.hole_data.terrain == []
    assert event_handler.hole_data.greens == []


def test_with_no_hole_open_a_tool_with_an_overlay_still_gets_the_mouse(
    mock_pygame, event_handler, tool_manager
):
    event_handler.hole_data.__init__()
    tool = Mock(
        spec=[
            "handle_mouse_down",
            "handle_mouse_up",
            "handle_mouse_motion",
            "render_overlay",
            "on_activated",
            "get_hotkey",
        ]
    )
    tool.get_hotkey.return_value = None
    for handler in (
        tool.handle_mouse_down,
        tool.handle_mouse_up,
        tool.handle_mouse_motion,
    ):
        handler.return_value = Mock(message=None)
    tool_manager.register_tool("dialog", tool)
    tool_manager.set_active_tool("dialog", event_handler.tool_context)

    click_canvas(event_handler)

    tool.handle_mouse_down.assert_called_once()
    tool.handle_mouse_motion.assert_called_once()
    tool.handle_mouse_up.assert_called_once()
