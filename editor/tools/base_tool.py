"""
Tool protocol and base definitions for editor tools.
"""

from typing import TYPE_CHECKING, Protocol

from editor.controllers.view_state import ViewState, canvas_rect

if TYPE_CHECKING:
    from editor.controllers.highlight_state import HighlightState
    from editor.tools.tool_manager import ToolManager


class Tool(Protocol):
    """Protocol defining the tool interface.

    Tools don't need to inherit from this - they just need to implement these methods.
    This provides duck typing with type checking support.
    """

    def handle_mouse_down(
        self, pos: tuple[int, int], button: int, modifiers: int, context: "ToolContext"
    ) -> "ToolResult":
        """Handle mouse button down event."""
        ...

    def handle_mouse_up(
        self, pos: tuple[int, int], button: int, context: "ToolContext"
    ) -> "ToolResult":
        """Handle mouse button up event."""
        ...

    def handle_mouse_motion(
        self, pos: tuple[int, int], context: "ToolContext"
    ) -> "ToolResult":
        """Handle mouse motion event."""
        ...

    def handle_key_down(
        self, key: int, modifiers: int, context: "ToolContext"
    ) -> "ToolResult":
        """Handle key down event (for tool-specific shortcuts)."""
        ...

    def handle_key_up(self, key: int, context: "ToolContext") -> "ToolResult":
        """Handle key up event."""
        ...

    def on_activated(self, context: "ToolContext") -> None:
        """Called when tool becomes active."""
        ...

    def on_deactivated(self, context: "ToolContext") -> None:
        """Called when tool is deactivated."""
        ...

    def reset(self) -> None:
        """Reset tool state."""
        ...

    def get_hotkey(self) -> int | None:
        """Return pygame key constant for this tool's activation hotkey.

        Returns None if tool has no hotkey.
        """
        ...


class ToolContext:
    """Context object providing tools access to application state.

    This acts as a facade, limiting what tools can access and preventing
    tight coupling to Application internals.
    """

    def __init__(
        self,
        hole_data,
        state,
        terrain_picker,
        greens_picker,
        forest_filler,
        screen_width: int,
        screen_height: int,
        tool_manager: "ToolManager | None" = None,
        highlight_state: "HighlightState | None" = None,
        stamp_library=None,
        on_revert_to_previous_tool=None,
        on_select_flag=None,
        on_set_palette=None,
    ):
        self.hole_data = hole_data
        self.state = state
        self.terrain_picker = terrain_picker
        self.greens_picker = greens_picker
        self.forest_filler = forest_filler
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.tool_manager = tool_manager
        self.highlight_state = highlight_state
        self.stamp_library = stamp_library
        self._on_revert_to_previous_tool = on_revert_to_previous_tool
        self._on_select_flag = on_select_flag
        self._on_set_palette = on_set_palette

    @property
    def view_state(self) -> "ViewState":
        """The camera as it is now, built fresh from the screen size and the
        editor state's scroll offsets and zoom."""
        return ViewState(
            canvas_rect(self.screen_width, self.screen_height),
            self.state.canvas_offset_x,
            self.state.canvas_offset_y,
            self.state.canvas_scale,
        )

    def get_eyedropper_tool(self):
        """Get eyedropper tool for delegation (used by Paint tool)."""
        if self.tool_manager:
            return self.tool_manager.get_tool("eyedropper")
        return None

    def request_revert_to_previous_tool(self):
        """Request that the application revert to the previously active tool.

        Used by dialog tools (like Metadata Editor) to automatically switch
        back to the previous tool after the dialog closes.
        """
        if self._on_revert_to_previous_tool:
            self._on_revert_to_previous_tool()

    def select_flag(self, index: int):
        """Request that the application change the visible flag.

        Used by tools (like PositionTool) to sync the visible flag with
        the currently selected position being edited.
        """
        if self._on_select_flag:
            self._on_select_flag(index)

    def select_palette(self, palette: int):
        """Request that the application change the selected palette (1-3).

        Used by tools (like EyedropperTool) so the toolbar's palette buttons
        follow the selection.
        """
        if self._on_set_palette:
            self._on_set_palette(palette)
        else:
            self.state.selected_palette = palette


class ToolResult:
    """Result of a tool operation."""

    def __init__(self, is_handled: bool = False, message: str | None = None):
        self.is_handled = is_handled
        self.message = message

    @staticmethod
    def handled() -> "ToolResult":
        """Event handled but no action needed."""
        return ToolResult(is_handled=True)

    @staticmethod
    def not_handled() -> "ToolResult":
        """Event not handled."""
        return ToolResult(is_handled=False)

    @staticmethod
    def modified(message: str | None = None) -> "ToolResult":
        """Content was modified."""
        return ToolResult(is_handled=True, message=message)
