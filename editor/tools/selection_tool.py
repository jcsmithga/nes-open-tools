"""
Selection tool for rectangular selection with cut/copy/paste operations.
"""

import pygame

from editor.controllers.view_state import ViewState
from editor.data import ClipboardData

from .base_tool import ToolContext, ToolResult, write_tiles

DEFAULT_TILE = 0x100


class SelectionToolState:
    """State for selection drag operation and paste mode."""

    def __init__(self):
        self.is_selecting = False
        self.selection_start: tuple[int, int] | None = None  # (row, col)
        self.selection_end: tuple[int, int] | None = None  # (row, col)
        #: the editor mode ("terrain" or "greens") the selection was made in
        self.selection_mode: str | None = None
        self.paste_mode = False

    def start_selection(self, tile_pos: tuple[int, int], mode: str):
        """Start a new selection in an editor mode."""
        self.is_selecting = True
        self.selection_start = tile_pos
        self.selection_end = tile_pos
        self.selection_mode = mode

    def update_selection(self, tile_pos: tuple[int, int]):
        """Update selection end point."""
        if self.is_selecting:
            self.selection_end = tile_pos

    def finalize_selection(self):
        """Finalize the selection."""
        self.is_selecting = False

    def get_selection_rect(self) -> tuple[int, int, int, int] | None:
        """
        Get selection rectangle as (start_row, start_col, end_row, end_col).

        Returns None if no selection.
        """
        if self.selection_start is None or self.selection_end is None:
            return None

        start_row, start_col = self.selection_start
        end_row, end_col = self.selection_end

        # Ensure correct order
        if start_row > end_row:
            start_row, end_row = end_row, start_row
        if start_col > end_col:
            start_col, end_col = end_col, start_col

        return (start_row, start_col, end_row, end_col)

    def clear_selection(self):
        """Clear the selection."""
        self.selection_start = None
        self.selection_end = None
        self.selection_mode = None
        self.is_selecting = False

    def reset(self):
        """Reset all state."""
        self.clear_selection()
        self.paste_mode = False


class SelectionTool:
    """Selection tool - rectangular selection with cut/copy/paste."""

    def __init__(self):
        self.state = SelectionToolState()

    def handle_mouse_down(self, pos, button, modifiers, context):
        # Right click: Clear selection/paste
        if button == 3:
            return self._clear_selection_or_paste(context)

        if button != 1:  # Only left click
            return ToolResult.not_handled()

        view_state = context.view_state

        # If in paste mode, commit paste
        if self.state.paste_mode:
            return self._commit_paste(view_state, pos, context)

        # Otherwise, start new selection
        tile = view_state.screen_to_tile(pos)
        if tile:
            # Clear previous selection
            context.highlight_state.selection_rect = None
            context.highlight_state.selection_mode = None

            self.state.start_selection(tile, context.state.mode)
            return ToolResult.handled()

        return ToolResult.not_handled()

    def handle_mouse_up(self, pos, button, context):
        if button == 1 and self.state.is_selecting:
            # Finalize selection and update highlight state
            self.state.finalize_selection()
            sel_rect = self.state.get_selection_rect()

            if sel_rect:
                start_row, start_col, end_row, end_col = sel_rect
                width = end_col - start_col + 1
                height = end_row - start_row + 1

                context.highlight_state.selection_rect = (
                    start_row,
                    start_col,
                    width,
                    height,
                )
                context.highlight_state.selection_mode = context.state.mode

            return ToolResult.handled()

        return ToolResult.not_handled()

    def handle_mouse_motion(self, pos, context):
        view_state = context.view_state

        # Update selection drag
        if self.state.is_selecting:
            tile = view_state.screen_to_tile(pos)
            if tile:
                self.state.update_selection(tile)

                # Update highlight state for preview
                sel_rect = self.state.get_selection_rect()
                if sel_rect:
                    start_row, start_col, end_row, end_col = sel_rect
                    width = end_col - start_col + 1
                    height = end_row - start_row + 1

                    context.highlight_state.selection_rect = (
                        start_row,
                        start_col,
                        width,
                        height,
                    )
                    context.highlight_state.selection_mode = context.state.mode

            return ToolResult.handled()

        # Update paste preview position
        if self.state.paste_mode:
            tile = view_state.screen_to_tile(pos)
            if tile:
                context.highlight_state.paste_preview_pos = tile
            return ToolResult.handled()

        return ToolResult.not_handled()

    def handle_key_down(self, key, modifiers, context):
        # Ctrl+C: Copy
        if key == pygame.K_c and (modifiers & pygame.KMOD_CTRL):
            return self._copy_selection(context)

        # Ctrl+X: Cut
        if key == pygame.K_x and (modifiers & pygame.KMOD_CTRL):
            return self._cut_selection(context)

        # Ctrl+V: Paste
        if key == pygame.K_v and (modifiers & pygame.KMOD_CTRL):
            return self._start_paste(context)

        # Delete: Delete selection
        if key == pygame.K_DELETE:
            return self._delete_selection(context)

        # Esc: Clear selection or cancel paste
        if key == pygame.K_ESCAPE:
            return self._clear_selection_or_paste(context)

        return ToolResult.not_handled()

    def handle_key_up(self, key, context):
        return ToolResult.not_handled()

    def on_activated(self, context):
        pass

    def on_deactivated(self, context):
        self.reset()
        # Clear highlight state
        context.highlight_state.selection_rect = None
        context.highlight_state.selection_mode = None
        context.highlight_state.paste_preview_pos = None
        context.state.paste_preview_active = False

    def reset(self):
        self.state.reset()

    def get_hotkey(self) -> int | None:
        """Return 'S' key for Selection tool."""
        return pygame.K_s

    def _selection_rect(self, context: ToolContext) -> tuple[int, int, int, int] | None:
        """The selection, if it was made in the mode now shown.

        A selection stays put across a Tab to the other mode, where it isn't drawn,
        so acting on it there would edit tiles the user can't see selected.
        """
        if self.state.selection_mode != context.state.mode:
            return None
        return self.state.get_selection_rect()

    def _copy_selection(self, context: ToolContext) -> ToolResult:
        """Copy selected region to clipboard."""
        sel_rect = self._selection_rect(context)

        if not sel_rect:
            return ToolResult(is_handled=True, message="No selection to copy")

        # Create clipboard if it doesn't exist
        if context.state.clipboard is None:
            context.state.clipboard = ClipboardData()

        # Copy region to clipboard
        success = context.state.clipboard.copy_region(
            context.hole_data, sel_rect, context.state.mode
        )

        if success:
            width = context.state.clipboard.width
            height = context.state.clipboard.height
            return ToolResult(
                is_handled=True, message=f"Copied {width}x{height} region to clipboard"
            )
        else:
            return ToolResult(is_handled=True, message="Failed to copy region")

    def _cut_selection(self, context: ToolContext) -> ToolResult:
        """Copy selected region to clipboard and fill with default tile."""
        # First, copy
        copy_result = self._copy_selection(context)

        if not copy_result.is_handled or "Failed" in (copy_result.message or ""):
            return copy_result

        # Then, clear it (fill with default tile)
        sel_rect = self._selection_rect(context)
        if not sel_rect:
            return ToolResult(is_handled=True, message="No selection to cut")
        self._clear_region(sel_rect, context)

        width = context.state.clipboard.width if context.state.clipboard else 0
        height = context.state.clipboard.height if context.state.clipboard else 0
        return ToolResult.modified(
            message=f"Cut {width}x{height} region to clipboard",
        )

    def _delete_selection(self, context: ToolContext) -> ToolResult:
        """Fill selected region with default tile."""
        sel_rect = self._selection_rect(context)

        if not sel_rect:
            return ToolResult(is_handled=True, message="No selection to delete")

        start_row, start_col, end_row, end_col = sel_rect
        width = end_col - start_col + 1
        height = end_row - start_row + 1
        if not self._clear_region(sel_rect, context):
            return ToolResult(
                is_handled=True, message=f"{width}x{height} region is already empty"
            )
        return ToolResult.modified(
            message=f"Deleted {width}x{height} region",
        )

    @staticmethod
    def _clear_region(sel_rect: tuple[int, int, int, int], context: ToolContext) -> int:
        """Fill a (start_row, start_col, end_row, end_col) region with the default
        tile; the number of tiles changed."""
        start_row, start_col, end_row, end_col = sel_rect
        return write_tiles(
            context,
            (
                (row, col, DEFAULT_TILE)
                for row in range(start_row, end_row + 1)
                for col in range(start_col, end_col + 1)
            ),
        )

    def _start_paste(self, context: ToolContext) -> ToolResult:
        """Enter paste preview mode."""
        if context.state.clipboard is None or context.state.clipboard.is_empty():
            return ToolResult(is_handled=True, message="Clipboard is empty")

        # Check mode compatibility
        if context.state.clipboard.mode != context.state.mode:
            return ToolResult(
                is_handled=True,
                message=f"Cannot paste {context.state.clipboard.mode} clipboard into {context.state.mode} mode",
            )

        # Enter paste mode
        self.state.paste_mode = True
        context.state.paste_preview_active = True

        # Clear selection
        self.state.clear_selection()
        if context.highlight_state:
            context.highlight_state.selection_rect = None

        width = context.state.clipboard.width
        height = context.state.clipboard.height
        return ToolResult(
            is_handled=True,
            message=f"Paste {width}x{height} region (click to place, Esc to cancel)",
        )

    def _commit_paste(
        self, view_state: ViewState, pos: tuple[int, int], context: ToolContext
    ) -> ToolResult:
        """Commit paste at cursor position."""
        if context.state.clipboard is None or context.state.clipboard.is_empty():
            self.state.paste_mode = False
            context.state.paste_preview_active = False
            return ToolResult(is_handled=True, message="Clipboard is empty")

        # Paste mode survives a Tab to the other mode; never paste across modes
        if context.state.clipboard.mode != context.state.mode:
            return ToolResult(
                is_handled=True,
                message=f"Cannot paste {context.state.clipboard.mode} clipboard into {context.state.mode} mode",
            )

        tile = view_state.screen_to_tile(pos)
        if not tile:
            return ToolResult.handled()

        paste_row, paste_col = tile
        clipboard = context.state.clipboard
        tiles_pasted = write_tiles(
            context,
            (
                (paste_row + row_offset, paste_col + col_offset, value)
                for row_offset in range(clipboard.height)
                for col_offset in range(clipboard.width)
                # None marks a transparent tile
                if (value := clipboard.get_tile(row_offset, col_offset)) is not None
            ),
        )

        # Stay in paste mode for multiple pastes
        # User can press Esc or right-click to exit paste mode
        message = f"Pasted {tiles_pasted} tiles (click to paste again, Esc/right-click to cancel)"
        if not tiles_pasted:
            return ToolResult(is_handled=True, message=message)
        return ToolResult.modified(message=message)

    def _cancel_paste(self, context: ToolContext):
        """Cancel paste mode."""
        self.state.paste_mode = False
        context.state.paste_preview_active = False
        if context.highlight_state:
            context.highlight_state.paste_preview_pos = None

    def _clear_selection_or_paste(self, context: ToolContext) -> ToolResult:
        """Clear selection or cancel paste mode."""
        if self.state.paste_mode:
            self._cancel_paste(context)
            return ToolResult(is_handled=True, message="Paste canceled")
        else:
            self.state.clear_selection()
            if context.highlight_state:
                context.highlight_state.selection_rect = None
                context.highlight_state.selection_mode = None
            return ToolResult(is_handled=True, message="Selection cleared")
