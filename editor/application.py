"""
NES Open Tournament Golf - Editor Application

Main application class that orchestrates all editor components.
"""

import json
from pathlib import Path

import pygame
from pygame import Rect

from golf.algorithms.forest_fill import ForestFiller
from golf.formats.hole_check import editable_problem, rom_problems
from golf.formats.hole_data import MAX_TERRAIN_ROWS, HoleData

from .controllers.editor_state import EditorState, GridMode
from .controllers.event_handler import EventHandler
from .controllers.highlight_state import HighlightState
from .controllers.stamp_library import StampLibrary
from .controllers.view_state import ViewState, canvas_rect
from .core.constants import (
    COLOR_BG,
    COLOR_STATUS,
    COLOR_TEXT,
    GREENS_HEIGHT,
    GREENS_WIDTH,
    PICKER_WIDTH,
    STATUS_HEIGHT,
    TERRAIN_WIDTH,
    TILE_SIZE,
    TOOL_PICKER_WIDTH,
    TOOLBAR_HEIGHT,
)
from .core.pygame_rendering import Sprite, Tileset
from .rendering.font_cache import get_font
from .rendering.greens_renderer import GreensRenderer
from .rendering.render_context import RenderContext
from .rendering.terrain_renderer import TerrainRenderer
from .resources import get_resource_path
from .tools.add_row_tool import AddRowTool
from .tools.carpet_paint_tool import CarpetPaintTool
from .tools.cycle_tool import CycleTool
from .tools.export_png_tool import ExportPngTool
from .tools.eyedropper_tool import EyedropperTool
from .tools.feature_brush_tool import (
    BoundaryBrushTool,
    FeatureBrushTool,
    GreenBrushTool,
)
from .tools.forest_fill_tool import ForestFillTool
from .tools.green_fix_tool import GreenFixTool
from .tools.measure_tool import MeasureTool
from .tools.metadata_editor_tool import MetadataEditorTool
from .tools.paint_tool import PaintTool
from .tools.palette_tool import PaletteTool
from .tools.position_tool import PositionTool
from .tools.quick_test_rom_tool import QuickTestRomTool
from .tools.remove_row_tool import RemoveRowTool
from .tools.row_operations_tool import RowOperationsTool
from .tools.selection_tool import SelectionTool
from .tools.stamp_tool import StampTool
from .tools.tool_manager import ToolManager
from .ui.confirm_dialog import UnsavedChangesDialog, UnsavedChoice
from .ui.dialogs import open_file_dialog, save_file_dialog
from .ui.message_dialog import MessageDialog
from .ui.pickers import GreensTilePicker, TilePicker, ToolPicker
from .ui.stamp_browser import StampBrowser
from .ui.toolbar import Toolbar, ToolbarCallbacks

#: the window size the editor asks for, shrunk to fit a smaller desktop
PREFERRED_WINDOW_SIZE = (1280, 1200)
#: room left on the desktop for the title bar and taskbar
DESKTOP_MARGIN = (40, 100)
#: empty rows the course view can scroll up into, above the hole's first row
ROWS_ABOVE_COURSE = 20


def _initial_window_size() -> tuple[int, int]:
    """The preferred window size, shrunk to fit the primary desktop."""
    width, height = PREFERRED_WINDOW_SIZE
    desktops = pygame.display.get_desktop_sizes()
    if desktops and desktops[0][0] > 0 and desktops[0][1] > 0:
        desktop_w, desktop_h = desktops[0]
        width = min(width, desktop_w - DESKTOP_MARGIN[0])
        height = min(height, desktop_h - DESKTOP_MARGIN[1])
    return width, height


def _clamp_span(offset: int, overhang: int, room_before: int = 0) -> int:
    """Clamp a scroll offset between 0 and how far the grid overhangs the canvas.

    The overhang is negative when the grid is smaller than the canvas, which lets
    the grid move away from the canvas's near edge until it meets the far one.
    `room_before` lets it move that many pixels further from the near edge.
    """
    return min(max(min(0, overhang) - room_before, offset), max(0, overhang))


class EditorApplication:
    """Main editor application."""

    def __init__(self, terrain_chr: str, greens_chr: str):
        pygame.init()

        self.screen_width, self.screen_height = _initial_window_size()
        self.screen = pygame.display.set_mode(
            (self.screen_width, self.screen_height), pygame.RESIZABLE
        )
        pygame.display.set_caption("NES Open Golf Course Editor")

        self.font = get_font("monospace", 14)
        self.font_small = get_font("monospace", 12)

        # Load tilesets
        self.terrain_tileset = Tileset(terrain_chr)
        self.greens_tileset = Tileset(greens_chr)

        # Load sprites
        self.sprites: dict[str, Sprite | None] = {}
        sprite_files = {
            "flag": get_resource_path("data/sprites/flag.json"),
            "tee": get_resource_path("data/sprites/tee-block.json"),
            "ball": get_resource_path("data/sprites/ball.json"),
            "green-cup": get_resource_path("data/sprites/green-cup.json"),
            "green-flag": get_resource_path("data/sprites/green-flag.json"),
        }

        for sprite_name, sprite_path in sprite_files.items():
            try:
                self.sprites[sprite_name] = Sprite(str(sprite_path))
            except FileNotFoundError:
                print(f"Warning: Sprite file not found: {sprite_path}")
                self.sprites[sprite_name] = None
            except Exception as e:
                print(f"Warning: Failed to load sprite {sprite_name}: {e}")
                self.sprites[sprite_name] = None

        # Load Forest Filler algorithm
        self.forest_filler = ForestFiller()

        # Create application state
        self.state = EditorState()
        self.hole_data = HoleData()

        # Create highlight state (for visual feedback)
        self.highlight_state = HighlightState()

        # Create pickers first (needed by event handler)
        picker_rect = self._picker_rect()
        self.terrain_picker = TilePicker(
            self.terrain_tileset,
            picker_rect,
            on_hover_change=self._on_terrain_hover_change,
            on_tile_selected=self._on_terrain_tile_selected,
        )
        self.greens_picker = GreensTilePicker(
            self.greens_tileset,
            picker_rect,
            on_hover_change=self._on_greens_hover_change,
            on_tile_selected=self._on_greens_tile_selected,
        )

        # Create tool picker (right sidebar)
        self.tool_picker = ToolPicker(
            self._tool_picker_rect(),
            on_tool_change=self._on_tool_change,
        )
        self.tool_picker.register_tool("paint", "Paint", "🖌")
        self.tool_picker.register_tool("palette", "Palette", "🎨")
        self.tool_picker.register_tool("selection", "Select", "✂")
        self.tool_picker.register_tool("stamp", "Stamp", "📋")
        self.tool_picker.register_tool("feature_brush", "Feature", "🖍")
        self.tool_picker.register_tool("boundary_brush", "OOB", "🚧")
        self.tool_picker.register_tool("forest_fill", "Forest Fill", "🌲")
        self.tool_picker.register_tool("cycle", "Cycle", "🔄")
        self.tool_picker.register_tool("green_brush", "Green", "🟢")
        self.tool_picker.register_tool("green_fix", "Green Fix", "🌿", is_action=True)
        self.tool_picker.register_tool("carpet_paint", "Carpet", "⛳")
        self.tool_picker.register_tool("measure", "Measure", "📏")
        self.tool_picker.register_tool("metadata_editor", "Metadata", "📝")
        self.tool_picker.register_tool("position", "Position", "🎯")
        self.tool_picker.register_tool("add_row", "Add Row", "➕", is_action=True)
        self.tool_picker.register_tool("remove_row", "Remove Row", "➖", is_action=True)
        self.tool_picker.register_tool("export_png", "Export PNG", "🖼", is_action=True)
        self.tool_picker.register_tool("quick_test_rom", "Test", "🎮", is_action=True)

        # Create stamp library and browser
        self.stamp_library = StampLibrary()
        self._load_stamps()

        self.stamp_browser = StampBrowser(
            picker_rect,
            self.stamp_library,
            self.font,
            self.terrain_tileset,  # Use terrain tileset for stamp previews
            on_stamp_selected=self._on_stamp_selected,
        )

        # Create tool manager and register tools
        self.tool_manager = ToolManager()
        self.tool_manager.register_tool("paint", PaintTool())
        self.tool_manager.register_tool("carpet_paint", CarpetPaintTool())
        self.tool_manager.register_tool("selection", SelectionTool())
        self.tool_manager.register_tool("stamp", StampTool())
        self.tool_manager.register_tool("feature_brush", FeatureBrushTool())
        self.tool_manager.register_tool("boundary_brush", BoundaryBrushTool())
        self.tool_manager.register_tool("palette", PaletteTool())
        self.tool_manager.register_tool("eyedropper", EyedropperTool())
        self.tool_manager.register_tool("forest_fill", ForestFillTool())
        self.tool_manager.register_tool("green_brush", GreenBrushTool())
        self.tool_manager.register_tool("green_fix", GreenFixTool())
        self.tool_manager.register_tool("cycle", CycleTool())
        self.tool_manager.register_tool("measure", MeasureTool())
        self.tool_manager.register_tool("row_operations", RowOperationsTool())
        self.tool_manager.register_tool("metadata_editor", MetadataEditorTool())
        self.tool_manager.register_tool("position", PositionTool())
        self.tool_manager.register_tool("add_row", AddRowTool())
        self.tool_manager.register_tool("remove_row", RemoveRowTool())
        self.tool_manager.register_tool("export_png", ExportPngTool())
        self.tool_manager.register_tool("quick_test_rom", QuickTestRomTool())

        # Create event handler early (buttons will reference its methods)
        self.event_handler = EventHandler(
            self.state,
            self.hole_data,
            self.terrain_picker,
            self.greens_picker,
            [],  # Empty buttons list initially
            self.screen_width,
            self.screen_height,
            self.tool_manager,
            self.tool_picker,
            self.stamp_browser,
            on_new=self._on_new,
            on_load=self._on_load,
            on_load_file=self._on_load_file,
            on_save=self._on_save,
            on_save_as=self._on_save_as,
            on_toggle_mode=self._toggle_mode,
            on_select_flag=self._select_flag,
            on_set_palette=self._set_palette,
            on_resize=self._on_resize,
            on_tool_change=self._on_tool_change,
            on_create_stamp=self._on_create_stamp,
            on_quit=self._confirm_discard,
            on_scroll_canvas=self._scroll_canvas,
            on_pan_canvas=self._pan_canvas,
        )

        # Set forest_filler, tool_manager, highlight_state, stamp_library, and revert callback on tool_context (needed by tools)
        self.event_handler.tool_context.forest_filler = self.forest_filler
        self.event_handler.tool_context.tool_manager = self.tool_manager
        self.event_handler.tool_context.highlight_state = self.highlight_state
        self.event_handler.tool_context.stamp_library = self.stamp_library
        self.event_handler.tool_context._on_revert_to_previous_tool = (
            self._revert_to_previous_tool
        )
        self.event_handler.tool_context._on_select_flag = self._select_flag
        self.event_handler.tool_context._on_set_palette = self._set_palette

        # Activate paint tool now that we have context
        self.tool_manager.set_active_tool("paint", self.event_handler.tool_context)

        # Create toolbar with callbacks
        toolbar_callbacks = ToolbarCallbacks(
            on_new=self._on_new,
            on_load=self._on_load,
            on_save=self._on_save,
            on_save_as=self._on_save_as,
            on_check=self._on_check,
            on_set_mode=self._set_mode,
            on_toggle_grid=self.state.cycle_grid_mode,
            on_select_flag=self._select_flag,
            on_set_palette=self._set_palette,
        )
        self.toolbar = Toolbar(self.screen_width, toolbar_callbacks)

        # Update event handler with toolbar buttons
        self.event_handler.buttons = self.toolbar.buttons

        self.running = True
        self.clock = pygame.time.Clock()

        # Update button states
        self._update_mode_buttons()
        self._update_flag_buttons()
        self._update_palette_buttons()

    def _set_mode(self, mode: str):
        """Set editing mode."""
        self.state.set_mode(mode)

        # If switching to terrain mode with carpet_paint active, switch to paint tool
        if (
            mode == "terrain"
            and self.tool_manager.get_active_tool_name() == "carpet_paint"
        ):
            self.tool_manager.set_active_tool("paint", self.event_handler.tool_context)
            self.tool_picker.selected_tool = "paint"

        self._update_mode_buttons()

    def _toggle_mode(self):
        """Switch between terrain and greens mode (Tab)."""
        self._set_mode("greens" if self.state.mode == "terrain" else "terrain")

    def _on_tool_change(self, tool_name: str | None = None):
        """Handle tool selection change from picker or hotkey."""
        if tool_name is None:
            # Called from hotkey - sync picker selection
            tool_name = self.tool_manager.get_active_tool_name()
            if tool_name:
                self.tool_picker.selected_tool = tool_name
        else:
            # Called from picker - activate tool
            self.tool_manager.set_active_tool(
                tool_name, self.event_handler.tool_context
            )

        # Auto-switch to supertile grid when palette tool activates
        if tool_name == "palette":
            self.state.grid_mode = GridMode.SUPERTILE

    def _revert_to_previous_tool(self):
        """Revert to the previously active tool.

        Called by dialog tools (like Metadata Editor) when their dialog closes
        to automatically switch back to the tool that was active before.
        """
        tool_name = self.tool_manager.previous_tool_name or "paint"
        self.tool_manager.set_active_tool(tool_name, self.event_handler.tool_context)
        self.tool_picker.selected_tool = tool_name

    def _update_mode_buttons(self):
        """Update mode button active states."""
        mode_buttons = self.toolbar.get_mode_buttons()
        mode_buttons[0].active = self.state.mode == "terrain"
        mode_buttons[1].active = self.state.mode == "greens"
        self._update_flag_buttons()
        self._update_palette_buttons()

    def _select_flag(self, index: int):
        """Select which flag position to display."""
        self.state.select_flag(index)
        self._update_flag_buttons()

    def _update_flag_buttons(self):
        """Update flag button active states."""
        flag_buttons = self.toolbar.get_flag_buttons()
        for i, btn in enumerate(flag_buttons):
            btn.active = i == self.state.selected_flag_index

    def _set_palette(self, palette: int):
        """Set the selected palette."""
        self.state.selected_palette = palette
        self._update_palette_buttons()

    def _update_palette_buttons(self):
        """Update palette button active states."""
        palette_buttons = self.toolbar.get_palette_buttons()
        for i, btn in enumerate(palette_buttons, start=1):
            btn.active = i == self.state.selected_palette

    def _process_tool_result(self, result):
        """Process a tool result (same logic as EventHandler._process_tool_result)."""
        if result.message:
            self.state.tool_message = result.message

    def _on_new(self):
        """Start a new, blank hole (Ctrl+N and the toolbar's New)."""
        if not self._confirm_discard():
            return
        self._replace_hole(HoleData.blank().to_dict(), None)
        self.state.tool_message = None

    def _on_load(self):
        """Load a hole file."""
        if not self._confirm_discard():
            return
        path = open_file_dialog(
            "Load Hole", [("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.load_hole(path)

    def _on_load_file(self, path: str):
        """Load a hole file from a specific path (e.g., drag-and-drop)."""
        if not path.lower().endswith(".json"):
            return
        if self._confirm_discard():
            self.load_hole(path)

    def _on_save(self):
        """Save the current hole (Ctrl+S and the toolbar's Save)."""
        self._save_hole()

    def _on_save_as(self):
        """Save the current hole under a new name (Ctrl+Shift+S and Save As)."""
        self._save_hole(ask=True)

    def _save_hole(self, ask: bool = False) -> bool:
        """Save the current hole, asking for a path if it has none or `ask`; True if
        saved. A hole a ROM could not hold is saved all the same, with a warning."""
        if not self.hole_data.terrain:
            return False
        path = (None if ask else self.hole_data.filepath) or save_file_dialog(
            "Save Hole", ".json", [("JSON files", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return False
        try:
            self.hole_data.save(path)
        except OSError as e:
            self.state.tool_message = f"Could not save {Path(path).name}: {e}"
            return False
        self.state.undo_manager.mark_saved(self.hole_data)
        problems = rom_problems(self.hole_data)
        self.state.tool_message = (
            f"Hole saved, but Check finds {len(problems)} problem(s)"
            if problems
            else "Hole saved!"
        )
        return True

    def _on_check(self):
        """Show whether the hole can be published, and if not, why (the toolbar's Check)."""
        if not self.hole_data.terrain:
            return
        problems = rom_problems(self.hole_data)
        if problems:
            title = f"Check: {len(problems)} problem(s)"
            paragraphs = [f"- {problem}" for problem in problems]
        else:
            title = "Check: no problems"
            paragraphs = ["The hole can be published."]
        MessageDialog(
            self.screen_width, self.screen_height, title, paragraphs, self.font
        ).show(self.screen, self.clock)
        self.state.tool_message = title

    def _confirm_discard(self) -> bool:
        """Ask what to do with unsaved changes; True if the hole may be replaced.

        Save saves first (and counts only if the save succeeds), Don't Save discards,
        Cancel keeps the hole open.
        """
        if not self.hole_data.modified:
            return True
        name = (
            Path(self.hole_data.filepath).name
            if self.hole_data.filepath
            else "this hole"
        )
        dialog = UnsavedChangesDialog(
            self.screen_width, self.screen_height, name, self.font
        )
        choice = dialog.show(self.screen, self.clock)
        if choice is UnsavedChoice.SAVE:
            return self._save_hole()
        return choice is UnsavedChoice.DISCARD

    def _on_resize(self, width: int, height: int):
        """Handle window resize."""
        self.screen_width = width
        self.screen_height = height
        self.screen = pygame.display.set_mode(
            (self.screen_width, self.screen_height), pygame.RESIZABLE
        )
        self.toolbar.resize(width)

        # Left sidebar (tile pickers or stamp browser) and right sidebar (tools)
        picker_rect = self._picker_rect()
        self.terrain_picker.resize(picker_rect)
        self.greens_picker.resize(picker_rect)
        self.stamp_browser.resize(picker_rect)
        self.tool_picker.resize(self._tool_picker_rect())

        self.event_handler.update_screen_size(width, height)

    def _picker_rect(self) -> Rect:
        """The left sidebar, which holds the tile pickers and the stamp browser."""
        return Rect(
            0,
            TOOLBAR_HEIGHT,
            PICKER_WIDTH,
            self.screen_height - TOOLBAR_HEIGHT - STATUS_HEIGHT,
        )

    def _tool_picker_rect(self) -> Rect:
        """The right sidebar, which holds the tool picker."""
        return Rect(
            self.screen_width - TOOL_PICKER_WIDTH,
            TOOLBAR_HEIGHT,
            TOOL_PICKER_WIDTH,
            self.screen_height - TOOLBAR_HEIGHT - STATUS_HEIGHT,
        )

    def _on_terrain_hover_change(self, tile_value: int | None):
        """Called when terrain picker hover changes."""
        if self.state.mode == "terrain":
            if tile_value:
                self.highlight_state.set_picker_hover(tile_value)
            else:
                self.highlight_state.clear_picker_hover()

    def _on_terrain_tile_selected(self, tile_value: int):
        """Called when a tile is selected in terrain picker - auto-switch to paint tool."""
        self.tool_manager.set_active_tool("paint", self.event_handler.tool_context)
        self.tool_picker.selected_tool = "paint"

    def _on_greens_hover_change(self, tile_value: int | None):
        """Called when greens picker hover changes."""
        shift_held = pygame.key.get_mods() & pygame.KMOD_SHIFT
        if shift_held and self.state.mode == "greens":
            self.highlight_state.set_picker_hover(tile_value)
        else:
            self.highlight_state.clear_picker_hover()

    def _on_greens_tile_selected(self, tile_value: int):
        """Called when a tile is selected in greens picker - auto-switch to paint tool.

        If carpet_paint is active, keep it active instead of switching to paint.
        """
        active_tool = self.tool_manager.get_active_tool_name()
        if active_tool == "carpet_paint":
            # Keep carpet paint active
            return

        self.tool_manager.set_active_tool("paint", self.event_handler.tool_context)
        self.tool_picker.selected_tool = "paint"

    def _on_stamp_selected(self, stamp):
        """Called when a stamp is selected in stamp browser - set on stamp tool."""
        # Get stamp tool and set the selected stamp
        stamp_tool = self.tool_manager.get_tool("stamp")
        if stamp_tool:
            stamp_tool.set_stamp(stamp)

        # Ensure stamp tool is active
        if self.tool_manager.get_active_tool_name() != "stamp":
            self.tool_manager.set_active_tool("stamp", self.event_handler.tool_context)
            self.tool_picker.selected_tool = "stamp"

    def _on_create_stamp(self):
        """Called when user presses Ctrl+Shift+C to create stamp from selection."""
        from editor.data import ClipboardData
        from editor.ui.stamp_creation_dialog import StampCreationDialog

        # Check for active selection first (preferred)
        clipboard_data = None
        if (
            self.highlight_state.selection_rect
            and self.highlight_state.selection_mode == self.state.mode
        ):
            # Create ClipboardData from active selection
            # Convert from (row, col, width, height) to (start_row, start_col, end_row, end_col)
            row, col, width, height = self.highlight_state.selection_rect
            rect_for_copy = (row, col, row + height - 1, col + width - 1)

            clipboard_data = ClipboardData()
            clipboard_data.copy_region(
                self.hole_data,
                rect_for_copy,
                self.state.mode,
            )
        elif self.state.clipboard and not self.state.clipboard.is_empty():
            # Fall back to clipboard if no active selection
            clipboard_data = self.state.clipboard
        else:
            self.state.tool_message = (
                "Create stamp: select a region with the Selection tool first"
            )
            return

        # Get appropriate tileset based on mode
        tileset = (
            self.greens_tileset if self.state.mode == "greens" else self.terrain_tileset
        )

        # Create and show dialog
        dialog = StampCreationDialog(
            self.screen_width,
            self.screen_height,
            clipboard_data,
            tileset,
            self.font,
        )

        stamp_data = dialog.show(self.screen, self.clock)

        if stamp_data:
            # Save stamp to library
            try:
                save_path = self.stamp_library.save_stamp(stamp_data)
            except OSError as e:
                self.state.tool_message = f"Could not save stamp: {e}"
                return
            self.state.tool_message = f"Stamp saved to {save_path}"

            # Reload stamps to update browser
            self._load_stamps()

    def _load_stamps(self):
        """(Re)load the stamp library, saying in the status bar if any stamp failed."""
        self.stamp_library.load_stamps()
        errors = self.stamp_library.load_errors
        if errors:
            self.state.tool_message = (
                f"{len(errors)} stamp file(s) failed to load, first: {errors[0]}"
            )

    def _get_canvas_rect(self) -> Rect:
        """Get the canvas drawing area."""
        return canvas_rect(self.screen_width, self.screen_height)

    def _scroll_canvas(self, columns: int, rows: int):
        """Scroll the canvas by whole tiles, keeping the hole in view."""
        tile = TILE_SIZE * self.state.canvas_scale
        self._pan_canvas(columns * tile, rows * tile)

    def _pan_canvas(self, dx: int, dy: int):
        """Move the canvas by screen pixels, keeping the hole in view."""
        self.state.canvas_offset_x += dx
        self.state.canvas_offset_y += dy
        self._clamp_canvas_offset()

    def _clamp_canvas_offset(self):
        """Keep the hole's grid within the canvas.

        The green's grid is its own 24 by 24 tiles. The course's is its 22 columns
        and 60 rows, the tallest a hole can be, so a shorter hole can scroll past
        its bottom toward where added rows would go; it can also scroll up into
        `ROWS_ABOVE_COURSE` empty rows above its top. A grid bigger than the canvas
        scrolls until its far edge reaches the canvas's; one smaller than the
        canvas moves around inside it, from the canvas's near edge to its far one.
        """
        if not self.hole_data.terrain:
            self.state.canvas_offset_x = self.state.canvas_offset_y = 0
            return
        tile = TILE_SIZE * self.state.canvas_scale
        if self.state.mode == "greens":
            columns, rows, room_above = GREENS_WIDTH, GREENS_HEIGHT, 0
        else:
            columns, rows = TERRAIN_WIDTH, MAX_TERRAIN_ROWS
            room_above = ROWS_ABOVE_COURSE * tile
        canvas = self._get_canvas_rect()
        self.state.canvas_offset_x = _clamp_span(
            self.state.canvas_offset_x, columns * tile - canvas.width
        )
        self.state.canvas_offset_y = _clamp_span(
            self.state.canvas_offset_y, rows * tile - canvas.height, room_above
        )

    def _screen_to_tile(self, screen_pos: tuple[int, int]) -> tuple[int, int] | None:
        """Convert screen position to tile coordinates."""
        return self.event_handler.tool_context.view_state.screen_to_tile(screen_pos)

    def run(self):
        """Main loop."""
        while self.running:
            events = pygame.event.get()
            self.running = self.event_handler.handle_events(events)
            self.event_handler.repeat_held_scrolls(pygame.time.get_ticks())

            # Switching to the green or a taller window can leave the canvas
            # scrolled past the edge; bring it back
            self._clamp_canvas_offset()

            # Update active tool (for time-based behavior like key repeat)
            active_tool = self.tool_manager.get_active_tool()
            update = getattr(active_tool, "update", None)
            if update is not None:
                result = update(self.event_handler.tool_context)
                self._process_tool_result(result)

            self._render()
            self.clock.tick(60)

        pygame.quit()

    def _render(self):
        """Render the editor."""
        self.screen.fill(COLOR_BG)

        # Left sidebar - stamp browser or tile picker
        active_tool = self.tool_manager.get_active_tool_name()
        if active_tool == "stamp":
            # Show stamp browser when stamp tool is active
            self.stamp_browser.render(self.screen, self.state.selected_palette)
        else:
            # Show tile picker for other tools
            if self.state.mode == "greens":
                self.greens_picker.render(self.screen)
            else:
                palette_for_picker = (
                    self.state.selected_palette
                    if self.state.selected_palette > 0
                    else 1
                )
                self.terrain_picker.render(self.screen, palette_for_picker)

        # Canvas
        self._render_canvas()

        # Tool picker (right sidebar)
        self.tool_picker.render(self.screen, self.font)

        # Toolbar after Canvas so scrolling the Canvas
        # won't cause it to clip into the Toolbar
        self._render_toolbar()

        # Status bar
        self._render_status()

        # Tool overlays (metadata dialog, etc.)
        active_tool = self.tool_manager.get_active_tool()
        render_overlay = getattr(active_tool, "render_overlay", None)
        if render_overlay is not None:
            render_overlay(self.screen)

        pygame.display.flip()

    def _render_toolbar(self):
        """Render toolbar with buttons and palette selector."""
        self.toolbar.render(self.screen, self.font, self.font_small)

    def _render_canvas(self):
        """Render the main editing canvas."""
        canvas_rect = self._get_canvas_rect()
        pygame.draw.rect(self.screen, (0, 0, 0), canvas_rect)

        if not self.hole_data.terrain:
            text = self.font.render(
                "No hole open. Ctrl+N for a new hole, Ctrl+O to open one.",
                True,
                COLOR_TEXT,
            )
            self.screen.blit(
                text, (canvas_rect.centerx - text.get_width() // 2, canvas_rect.centery)
            )
            return

        # Create view state
        view_state = ViewState(
            canvas_rect,
            self.state.canvas_offset_x,
            self.state.canvas_offset_y,
            self.state.canvas_scale,
        )

        # Get measure points from measure tool
        measure_tool = self.tool_manager.get_tool("measure")
        is_measure_active = self.tool_manager.get_active_tool_name() == "measure"

        if measure_tool:
            # Always pass points for persistent rendering (terrain mode only)
            self.highlight_state.measure_points = (
                measure_tool.points
                if measure_tool.points and self.state.mode == "terrain"
                else None
            )

            # Preview only when tool is active and has points
            if (
                is_measure_active
                and measure_tool.points
                and self.state.mode == "terrain"
            ):
                self.highlight_state.measure_preview_point = measure_tool.preview_point
            else:
                self.highlight_state.measure_preview_point = None

            self.highlight_state.measure_tool_active = is_measure_active

        # Render based on mode
        if self.state.mode == "greens":
            render_ctx = RenderContext(
                self.greens_tileset,
                self.sprites,
                self.state.mode,
                self.state.grid_mode,
                self.state.selected_flag_index,
                self.state,  # Add state for clipboard/paste preview access
            )
            GreensRenderer.render(
                self.screen,
                view_state,
                self.hole_data,
                render_ctx,
                self.highlight_state,
            )
        else:
            render_ctx = RenderContext(
                self.terrain_tileset,
                self.sprites,
                self.state.mode,
                self.state.grid_mode,
                self.state.selected_flag_index,
                self.state,  # Add state for clipboard/paste preview access
            )
            TerrainRenderer.render(
                self.screen,
                view_state,
                self.hole_data,
                render_ctx,
                self.highlight_state,
            )

    def _render_status(self):
        """Render status bar."""
        status_rect = Rect(
            0, self.screen_height - STATUS_HEIGHT, self.screen_width, STATUS_HEIGHT
        )
        pygame.draw.rect(self.screen, COLOR_STATUS, status_rect)

        # Mouse position
        mouse_pos = pygame.mouse.get_pos()

        status_parts: list[str] = []

        # Check picker hover first (priority over canvas)
        picker_hover_tile = None
        if self.state.mode == "greens":
            picker_hover_tile = self.greens_picker.get_hovered_tile()
        else:
            picker_hover_tile = self.terrain_picker.get_hovered_tile()

        if picker_hover_tile is not None:
            # Show picker tile value
            status_parts.append(f"Picker Tile: ${picker_hover_tile:02X}")
        else:
            # Existing canvas hover logic
            tile = self._screen_to_tile(mouse_pos)

            if tile:
                row, col = tile
                status_parts.append(f"Tile: ({col}, {row})")

                if (
                    self.state.mode == "terrain"
                    and 0 <= row < len(self.hole_data.terrain)
                    and 0 <= col < TERRAIN_WIDTH
                ):
                    tile_val = self.hole_data.terrain[row][col]
                    attr_val = self.hole_data.get_attribute(row, col)
                    status_parts.append(f"Value: ${tile_val:02X}")
                    status_parts.append(f"Palette: {attr_val}")

                elif (
                    self.state.mode == "greens"
                    and 0 <= row < len(self.hole_data.greens)
                    and 0 <= col < GREENS_WIDTH
                ):
                    tile_val = self.hole_data.greens[row][col]
                    status_parts.append(f"Value: ${tile_val:02X}")

        if self.hole_data.terrain:
            name = (
                Path(self.hole_data.filepath).name
                if self.hole_data.filepath
                else "new hole"
            )
            modified = "*" if self.hole_data.modified else ""
            status_parts.append(f"File: {name}{modified}")

        # Show tool message (e.g., from Position Tool)
        # Clear "Hole saved!" if file has been modified since saving
        if (self.state.tool_message or "").startswith(
            "Hole saved"
        ) and self.hole_data.modified:
            self.state.tool_message = None
        if self.state.tool_message:
            status_parts.append(self.state.tool_message)

        # Show undo/redo availability
        if self.state.undo_manager.can_undo():
            status_parts.append("Undo:Ctrl+Z")
        if self.state.undo_manager.can_redo():
            status_parts.append("Redo:Ctrl+Y")

        status_text = "  |  ".join(status_parts)
        text_surf = self.font.render(status_text, True, COLOR_TEXT)
        self.screen.blit(text_surf, (10, self.screen_height - STATUS_HEIGHT + 8))

    def load_hole(self, path: str) -> bool:
        """Load a hole from file path; True if it loaded.

        A file that can't be read or isn't a hole leaves the current hole open and
        says why in the status bar.
        """
        try:
            with open(path) as f:
                data = json.load(f)
            # Parse into a scratch hole first: the editor's components all hold
            # self.hole_data, so it is only replaced once the file is known good
            problem = editable_problem(HoleData.from_dict(data))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            data, problem = None, f"{type(e).__name__}: {e}"
        if data is None or problem:
            self.state.tool_message = f"Could not load {Path(path).name}: {problem}"
            return False

        self._replace_hole(data, path)
        self.state.tool_message = None
        return True

    def _replace_hole(self, data: dict, path: str | None):
        """Make the hole a parsed hole file's `data`, saved at `path` (None if it has
        never been saved), with a fresh undo history."""
        # every component holds self.hole_data, so it is changed in place
        self.hole_data.load_dict(data)
        self.hole_data.filepath = path
        self.state.reset_canvas_position()
        self.state.undo_manager.set_initial_state(self.hole_data)
