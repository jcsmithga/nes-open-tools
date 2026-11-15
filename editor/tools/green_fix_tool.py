"""
Green fix tool - redoes the rough round a green and fills placeholder: rough outside the
fringe, flat putting surface inside it.
"""

import pygame

from editor.algorithms.green_fix import GreenFix
from golf.algorithms.green_zones import rough_phase

from .base_tool import ToolResult


class GreenFixTool:
    """Green fix tool - redoes the rough outside the fringe and fills placeholder."""

    def __init__(self):
        self._filler = GreenFix()

    def handle_mouse_down(self, pos, button, modifiers, context):
        return ToolResult.not_handled()

    def handle_mouse_up(self, pos, button, context):
        return ToolResult.not_handled()

    def handle_mouse_motion(self, pos, context):
        return ToolResult.not_handled()

    def handle_key_down(self, key, modifiers, context):
        return ToolResult.not_handled()

    def handle_key_up(self, key, context):
        return ToolResult.not_handled()

    def on_activated(self, context):
        """Execute green fix when tool is activated (action tool)."""
        # Only works in greens mode
        if context.state.mode != "greens":
            context.show_message("Green Fix: Only works in greens mode")
            return

        greens = context.hole_data.greens
        if not greens:
            return

        # Replace rough tiles with placeholders
        with_placeholders = self._replace_rough_with_placeholder(greens)

        # Run fill algorithm, keeping the rough checkered the way it is
        filled = self._filler.fill(with_placeholders, rough_phase(greens))

        # Find changes
        changes = []
        for row_idx, (orig_row, filled_row) in enumerate(
            zip(greens, filled, strict=True)
        ):
            for col_idx, (orig_tile, filled_tile) in enumerate(
                zip(orig_row, filled_row, strict=True)
            ):
                if orig_tile != filled_tile:
                    changes.append((row_idx, col_idx, filled_tile))

        if not changes:
            context.show_message("Green Fix: Nothing to change")
            return

        # Push undo state before applying
        context.state.undo_manager.push_state(context.hole_data)

        # Apply changes
        for row, col, tile in changes:
            context.hole_data.set_greens_tile(row, col, tile)
        context.show_message(f"Green Fix: Changed {len(changes)} tiles")

    def on_deactivated(self, context):
        pass

    def reset(self):
        pass

    def get_hotkey(self) -> int | None:
        """Return 'U' key for Green Fix tool."""
        return pygame.K_u

    def is_action_tool(self) -> bool:
        """Identify this as an action tool."""
        return True

    def _replace_rough_with_placeholder(
        self, greens: list[list[int]]
    ) -> list[list[int]]:
        """Replace all rough tiles with placeholder value."""
        result = []
        for row in greens:
            new_row = [
                GreenFix.PLACEHOLDER if tile in GreenFix.ROUGH_TILES else tile
                for tile in row
            ]
            result.append(new_row)
        return result
