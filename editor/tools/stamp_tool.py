"""
Stamp tool for browsing and placing stamp patterns.
"""

import pygame

from editor.data import StampData

from .base_tool import ToolContext, ToolResult, write_tiles


class StampToolState:
    """State for stamp tool."""

    def __init__(self):
        self.current_stamp: StampData | None = None

    def set_stamp(self, stamp: StampData | None):
        """Set current stamp."""
        self.current_stamp = stamp

    def clear(self):
        """Clear stamp selection."""
        self.current_stamp = None

    def reset(self):
        """Reset tool state."""
        self.clear()


class StampTool:
    """Stamp tool - browse and place stamp patterns."""

    def __init__(self):
        self.state = StampToolState()

    def handle_mouse_down(self, pos, button, modifiers, context):
        # Right click: Deselect stamp
        if button == 3:
            if self.state.current_stamp is not None:
                self.state.clear()
                context.highlight_state.stamp_preview_pos = None
                context.highlight_state.current_stamp = None
                return ToolResult(is_handled=True, message="Stamp: Deselected")
            return ToolResult.handled()

        if button != 1:  # Only left click
            return ToolResult.not_handled()

        # If no stamp selected, do nothing
        if self.state.current_stamp is None:
            return ToolResult(
                is_handled=True, message="Stamp: Select a stamp from the browser"
            )

        # Check mode compatibility
        if self.state.current_stamp.mode != context.state.mode:
            return ToolResult(
                is_handled=True,
                message=f"Stamp: Cannot place {self.state.current_stamp.mode} stamp in {context.state.mode} mode",
            )

        view_state = context.view_state

        tile = view_state.screen_to_tile(pos)
        if not tile:
            return ToolResult.handled()

        # Place stamp
        return self._place_stamp(tile, context)

    def handle_mouse_up(self, pos, button, context):
        return ToolResult.not_handled()

    def handle_mouse_motion(self, pos, context):
        # Update preview position
        if self.state.current_stamp is not None:
            view_state = context.view_state

            tile = view_state.screen_to_tile(pos)
            if tile:
                # Update highlight state for rendering
                context.highlight_state.stamp_preview_pos = tile
                context.highlight_state.current_stamp = self.state.current_stamp
                return ToolResult.handled()

        return ToolResult.not_handled()

    def handle_key_down(self, key, modifiers, context):
        # Esc: Clear stamp selection
        if key == pygame.K_ESCAPE:
            self.state.clear()
            context.highlight_state.stamp_preview_pos = None
            context.highlight_state.current_stamp = None
            return ToolResult(is_handled=True, message="Stamp: Cleared selection")

        return ToolResult.not_handled()

    def handle_key_up(self, key, context):
        return ToolResult.not_handled()

    def on_activated(self, context):
        """Called when tool becomes active."""
        # Clear any previous preview
        context.highlight_state.stamp_preview_pos = None
        context.highlight_state.current_stamp = None

    def on_deactivated(self, context):
        """Called when tool is deactivated."""
        self.reset()
        # Clear highlight state
        context.highlight_state.stamp_preview_pos = None
        context.highlight_state.current_stamp = None

    def reset(self):
        """Reset tool state."""
        self.state.reset()

    def get_hotkey(self) -> int | None:
        """Return 'K' key for Stamp tool (K for "library")."""
        return pygame.K_k

    def set_stamp(self, stamp: StampData):
        """
        Set current stamp for placement.

        This is called by the stamp browser when user selects a stamp.
        """
        self.state.set_stamp(stamp)

    def _place_stamp(
        self, tile_pos: tuple[int, int], context: ToolContext
    ) -> ToolResult:
        """
        Place stamp at tile position.

        Args:
            tile_pos: (row, col) position to place stamp
            context: Tool context

        Returns:
            ToolResult indicating success
        """
        if self.state.current_stamp is None:
            return ToolResult(is_handled=True, message="Stamp: No stamp selected")

        stamp = self.state.current_stamp
        place_row, place_col = tile_pos

        tiles_placed = write_tiles(
            context,
            (
                (place_row + row_offset, place_col + col_offset, value)
                for row_offset in range(stamp.height)
                for col_offset in range(stamp.width)
                # None marks a transparent tile
                if (value := stamp.get_tile(row_offset, col_offset)) is not None
            ),
        )

        stamp_name = stamp.get_display_name()
        message = f"Stamp: Placed '{stamp_name}' ({tiles_placed} tiles)"
        if not tiles_placed:
            return ToolResult(is_handled=True, message=message)
        return ToolResult.modified(message=message)
