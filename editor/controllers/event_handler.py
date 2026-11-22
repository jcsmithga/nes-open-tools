"""
NES Open Tournament Golf - Event Handler

Handles user input events including mouse, keyboard, and window events.
"""

from collections.abc import Callable

import pygame

from editor.ui.pickers import GreensTilePicker, TilePicker, ToolPicker
from editor.ui.widgets import Button
from golf.formats.hole_data import HoleData

from .editor_state import EditorState

#: the keys that show each flag position, in order
FLAG_KEYS = (pygame.K_F1, pygame.K_F2, pygame.K_F3, pygame.K_F4)

#: the keys that select palettes 1-3, in order
PALETTE_KEYS = (pygame.K_1, pygame.K_2, pygame.K_3)

#: the arrow keys, and how far each scrolls the canvas in tiles (columns, rows)
ARROW_SCROLL = {
    pygame.K_LEFT: (-1, 0),
    pygame.K_RIGHT: (1, 0),
    pygame.K_UP: (0, -1),
    pygame.K_DOWN: (0, 1),
}

#: a held arrow key scrolls again after this long, then at this interval, as the
#: Position tool's held arrows move a position
SCROLL_REPEAT_DELAY_MS = 500
SCROLL_REPEAT_INTERVAL_MS = 50

#: pygame's number for the middle mouse button, which drags the canvas
MIDDLE_BUTTON = 2


class EventHandler:
    """Handles all user input events."""

    def __init__(
        self,
        state: EditorState,
        hole_data: HoleData,
        terrain_picker: TilePicker,
        greens_picker: GreensTilePicker,
        buttons: list[Button],
        screen_width: int,
        screen_height: int,
        tool_manager,
        tool_picker: ToolPicker,
        stamp_browser,
        on_new: Callable[[], None],
        on_load: Callable[[], None],
        on_load_file: Callable[[str], None],
        on_save: Callable[[], None],
        on_save_as: Callable[[], None],
        on_toggle_mode: Callable[[], None],
        on_select_flag: Callable[[int], None],
        on_set_palette: Callable[[int], None],
        on_resize: Callable[[int, int], None],
        on_tool_change: Callable[[], None],
        on_create_stamp: Callable[[], None] | None = None,
        on_quit: Callable[[], bool] | None = None,
        on_scroll_canvas: Callable[[int, int], None] | None = None,
        on_pan_canvas: Callable[[int, int], None] | None = None,
        on_zoom_canvas: Callable[[int, tuple[int, int]], None] | None = None,
    ):
        """
        Initialize event handler.

        Args:
            state: Editor state
            hole_data: Hole data
            terrain_picker: Terrain tile picker
            greens_picker: Greens tile picker
            buttons: List of UI buttons
            screen_width: Screen width
            screen_height: Screen height
            tool_manager: Tool manager for handling tools
            stamp_browser: Stamp browser widget
            on_new: Callback for starting a new hole
            on_load: Callback for load action
            on_load_file: Callback for loading a specific file (drag-and-drop)
            on_save: Callback for save action
            on_save_as: Callback for saving under a new name
            on_toggle_mode: Callback to switch between terrain and greens mode
            on_select_flag: Callback to select a flag position by index (0-3)
            on_set_palette: Callback to select a terrain palette (1-3)
            on_resize: Callback for window resize (width, height)
            on_create_stamp: Callback for creating stamp from selection
            on_quit: Callback asked before quitting; returns False to stay open
            on_scroll_canvas: Callback to scroll the canvas by (columns, rows) of tiles
            on_pan_canvas: Callback to move the canvas by (x, y) screen pixels
            on_zoom_canvas: Callback to zoom the canvas in (1) or out (-1) a
                level, keeping the spot at a screen position in place
        """
        self.state = state
        self.hole_data = hole_data
        self.terrain_picker = terrain_picker
        self.greens_picker = greens_picker
        self.tool_picker = tool_picker
        self.stamp_browser = stamp_browser
        self.buttons = buttons
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.tool_manager = tool_manager
        self.on_new = on_new
        self.on_load = on_load
        self.on_load_file = on_load_file
        self.on_save = on_save
        self.on_save_as = on_save_as
        self.on_toggle_mode = on_toggle_mode
        self.on_create_stamp = on_create_stamp
        self.on_quit = on_quit
        self.on_scroll_canvas = on_scroll_canvas
        # each arrow key held down for scrolling, and when it next scrolls (ms ticks)
        self._held_scroll_keys: dict[int, int] = {}
        self.on_pan_canvas = on_pan_canvas
        # where the mouse was when last seen while middle-dragging the canvas
        self._drag_from: tuple[int, int] | None = None
        self.on_zoom_canvas = on_zoom_canvas
        self.on_select_flag = on_select_flag
        self.on_set_palette = on_set_palette
        self.on_resize = on_resize
        self.on_tool_change = on_tool_change

        # Create tool context (will be updated by Application with forest_filler)
        from editor.tools.base_tool import ToolContext

        self.tool_context = ToolContext(
            hole_data=hole_data,
            state=state,
            terrain_picker=terrain_picker,
            greens_picker=greens_picker,
            forest_filler=None,  # Will be set by Application
            screen_width=screen_width,
            screen_height=screen_height,
        )

    def update_screen_size(self, width: int, height: int):
        """Update screen dimensions."""
        self.screen_width = width
        self.screen_height = height
        self.tool_context.screen_width = width
        self.tool_context.screen_height = height

    def _scroll_canvas(self, columns: int, rows: int):
        """Ask the application to scroll the canvas by whole tiles."""
        if self.on_scroll_canvas:
            self.on_scroll_canvas(columns, rows)

    def _drag_to(self, event: pygame.event.Event):
        """Move the canvas with the mouse while the middle button holds it.

        The view follows the hand: dragging up shows what is further down.
        """
        drag_from = self._drag_from
        if drag_from is None or not event.buttons[MIDDLE_BUTTON - 1]:
            # let go somewhere its MOUSEBUTTONUP did not reach us
            self._drag_from = None
            return
        dx = drag_from[0] - event.pos[0]
        dy = drag_from[1] - event.pos[1]
        self._drag_from = event.pos
        if self.on_pan_canvas and (dx or dy):
            self.on_pan_canvas(dx, dy)

    def repeat_held_scrolls(self, now: int):
        """Scroll again for each arrow key held long enough, as a held key repeats.

        Called once a frame with ``pygame.time.get_ticks()``. Pygame's own key repeat
        is left off, since it would repeat every key, tool hotkeys among them.
        """
        for key, due in list(self._held_scroll_keys.items()):
            if now >= due:
                self._scroll_canvas(*ARROW_SCROLL[key])
                self._held_scroll_keys[key] = now + SCROLL_REPEAT_INTERVAL_MS

    def handle_events(self, events: list[pygame.event.Event]) -> bool:
        """
        Handle all pygame events.

        Args:
            events: List of pygame events to process

        Returns:
            True if application should continue running, False if quit requested
        """
        for event in events:
            if event.type == pygame.QUIT:
                if self.on_quit is None or self.on_quit():
                    return False
                continue

            if event.type == pygame.WINDOWFOCUSLOST:
                # a key let go in another window never sends its KEYUP here
                self._held_scroll_keys.clear()
                self._drag_from = None

            if event.type == pygame.MOUSEBUTTONUP and event.button == MIDDLE_BUTTON:
                # wherever it is let go, over the canvas or not, the drag ends
                self._drag_from = None

            if event.type == pygame.KEYDOWN:
                # Let active tool handle key events first (for tool-specific shortcuts)
                tool = self.tool_manager.get_active_tool()
                tool_handled = False
                if tool:
                    modifiers = pygame.key.get_mods()
                    result = tool.handle_key_down(
                        event.key, modifiers, self.tool_context
                    )
                    self._process_tool_result(result)
                    tool_handled = result.is_handled

                # Only process global keys if tool didn't handle it
                if not tool_handled:
                    self._handle_global_keys(event)

            elif event.type == pygame.KEYUP:
                # A released arrow stops scrolling, whoever had it
                self._held_scroll_keys.pop(event.key, None)

                # Let active tool handle key up
                tool = self.tool_manager.get_active_tool()
                if tool:
                    result = tool.handle_key_up(event.key, self.tool_context)
                    self._process_tool_result(result)

            # Handle button events
            button_handled = False
            for button in self.buttons:
                if button.handle_event(event):
                    button_handled = True
                    break  # Stop after first button handles it

            # Handle picker/browser events (depends on active tool)
            picker_handled = False
            active_tool = self.tool_manager.get_active_tool_name()
            if active_tool == "stamp":
                # Stamp browser is active
                picker_handled = self.stamp_browser.handle_event(event)
            elif self.state.mode == "greens":
                picker_handled = self.greens_picker.handle_event(event)
            else:
                picker_handled = self.terrain_picker.handle_event(event)

            # Handle tool picker events
            tool_picker_handled = self.tool_picker.handle_event(event)
            if tool_picker_handled:
                continue

            # Handle canvas events (only if button/picker didn't handle)
            if event.type == pygame.MOUSEBUTTONDOWN and not (
                button_handled or picker_handled
            ):
                modifiers = pygame.key.get_mods()

                # The wheel scrolls the canvas, or zooms it with Ctrl held
                if event.button in (4, 5):
                    up = event.button == 4
                    if modifiers & pygame.KMOD_CTRL:
                        if self.on_zoom_canvas:
                            self.on_zoom_canvas(1 if up else -1, event.pos)
                    else:
                        self._scroll_canvas(0, -1 if up else 1)
                    continue

                tool = self._canvas_tool()
                tool_handled = False
                if tool:
                    result = tool.handle_mouse_down(
                        event.pos, event.button, modifiers, self.tool_context
                    )
                    self._process_tool_result(result)
                    tool_handled = result.is_handled

                # A middle click no tool took on the hole starts dragging it
                if (
                    event.button == MIDDLE_BUTTON
                    and not tool_handled
                    and self.hole_data.terrain
                    and self.tool_context.view_state.canvas_rect.collidepoint(event.pos)
                ):
                    self._drag_from = event.pos

            elif event.type == pygame.MOUSEBUTTONUP and not picker_handled:
                tool = self._canvas_tool()
                if tool:
                    result = tool.handle_mouse_up(
                        event.pos, event.button, self.tool_context
                    )
                    self._process_tool_result(result)

            elif event.type == pygame.MOUSEMOTION:
                if self._drag_from is not None:
                    self._drag_to(event)
                tool = self._canvas_tool()
                if tool:
                    result = tool.handle_mouse_motion(event.pos, self.tool_context)
                    self._process_tool_result(result)

            elif event.type == pygame.VIDEORESIZE:
                self.on_resize(event.w, event.h)

            elif event.type == pygame.DROPFILE:
                # Handle drag-and-drop file loading
                if event.file.lower().endswith(".json"):
                    self.on_load_file(event.file)

        return True

    def _canvas_tool(self):
        """The active tool, to be given a mouse event, or None.

        With no hole open the canvas has nothing to edit, so only a tool with an
        overlay of its own (the metadata dialog) gets the mouse.
        """
        tool = self.tool_manager.get_active_tool()
        if tool is None:
            return None
        if self.hole_data.terrain or hasattr(tool, "render_overlay"):
            return tool
        return None

    def _process_tool_result(self, result):
        """Process a tool result and trigger necessary callbacks."""
        if result.message:
            self.state.tool_message = result.message

    def _handle_global_keys(self, event) -> bool:
        """Handle keyboard input."""
        if event.key == pygame.K_g:
            self.state.cycle_grid_mode()

        elif event.key in PALETTE_KEYS:
            self.on_set_palette(PALETTE_KEYS.index(event.key) + 1)

        elif event.key == pygame.K_TAB:
            self.on_toggle_mode()

        elif event.key == pygame.K_s and pygame.key.get_mods() & pygame.KMOD_CTRL:
            if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                # Ctrl+Shift+S: Save As
                self.on_save_as()
            else:
                # Ctrl+S: Save
                self.on_save()
        elif event.key == pygame.K_n and pygame.key.get_mods() & pygame.KMOD_CTRL:
            self.on_new()
        elif event.key == pygame.K_o and pygame.key.get_mods() & pygame.KMOD_CTRL:
            self.on_load()
        elif event.key == pygame.K_z and pygame.key.get_mods() & pygame.KMOD_CTRL:
            if pygame.key.get_mods() & pygame.KMOD_SHIFT:
                # Ctrl+Shift+Z = Redo (alternative to Ctrl+Y)
                self._redo()
            else:
                # Ctrl+Z = Undo
                self._undo()
        elif event.key == pygame.K_y and pygame.key.get_mods() & pygame.KMOD_CTRL:
            # Ctrl+Y = Redo
            self._redo()

        # Selection tool shortcuts (delegate to active tool if it's Selection)
        elif (
            event.key == pygame.K_c
            and pygame.key.get_mods() & pygame.KMOD_CTRL
            and pygame.key.get_mods() & pygame.KMOD_SHIFT
        ):
            # Ctrl+Shift+C: Create stamp from selection, beside Copy
            if self.on_create_stamp:
                self.on_create_stamp()
        elif event.key == pygame.K_c and pygame.key.get_mods() & pygame.KMOD_CTRL:
            # Ctrl+C = Copy (if Selection tool is active)
            active_tool = self.tool_manager.get_active_tool()
            if active_tool and hasattr(active_tool, "_copy_selection"):
                # Selection tool handles this
                return False  # Let tool handle it
            return False  # Not handled
        elif event.key == pygame.K_x and pygame.key.get_mods() & pygame.KMOD_CTRL:
            # Ctrl+X = Cut (if Selection tool is active)
            active_tool = self.tool_manager.get_active_tool()
            if active_tool and hasattr(active_tool, "_cut_selection"):
                # Selection tool handles this
                return False  # Let tool handle it
            return False  # Not handled
        elif event.key == pygame.K_v and pygame.key.get_mods() & pygame.KMOD_CTRL:
            # Ctrl+V = Paste (if Selection tool is active)
            active_tool = self.tool_manager.get_active_tool()
            if active_tool and hasattr(active_tool, "_start_paste"):
                # Selection tool handles this
                return False  # Let tool handle it
            return False  # Not handled

        elif event.key in ARROW_SCROLL:
            self._scroll_canvas(*ARROW_SCROLL[event.key])
            self._held_scroll_keys[event.key] = (
                pygame.time.get_ticks() + SCROLL_REPEAT_DELAY_MS
            )

        elif event.key in FLAG_KEYS:
            # F1-F4: show that flag position, as the toolbar's F1-F4 buttons do
            self.on_select_flag(FLAG_KEYS.index(event.key))

        else:
            # Try tool hotkeys
            if self.tool_manager.activate_by_hotkey(event.key, self.tool_context):
                self.on_tool_change()  # Sync picker state
                return True
            # Key not handled by global handler
            return False

        # Key was handled
        return True

    def _undo(self):
        """Undo last action."""
        if self.state.undo_manager.can_undo():
            previous_state = self.state.undo_manager.undo(self.hole_data)
            if previous_state:
                self._restore_hole_data(previous_state)

    def _redo(self):
        """Redo last undone action."""
        if self.state.undo_manager.can_redo():
            next_state = self.state.undo_manager.redo(self.hole_data)
            if next_state:
                self._restore_hole_data(next_state)

    def _restore_hole_data(self, snapshot: HoleData):
        """Restore hole data from snapshot."""
        self.hole_data.terrain = snapshot.terrain
        self.hole_data.terrain_height = snapshot.terrain_height
        self.hole_data.attributes = snapshot.attributes
        self.hole_data.greens = snapshot.greens
        self.hole_data.green_x = snapshot.green_x
        self.hole_data.green_y = snapshot.green_y
        self.hole_data.metadata = snapshot.metadata
        # Undoing back to the saved hole leaves nothing to save
        self.hole_data.modified = not self.state.undo_manager.matches_saved(
            self.hole_data
        )
