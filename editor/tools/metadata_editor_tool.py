"""
Metadata editor tool for editing hole par and distance.

Opens a modal dialog when activated.
"""

import pygame

from editor.rendering import font_cache
from editor.ui.metadata_dialog import MetadataDialog
from golf.formats.hole_data import HoleData

from .base_tool import ToolContext, ToolResult


class MetadataEditorTool:
    """
    Metadata editor tool - opens dialog for editing par and distance.

    The tool owns a MetadataDialog that provides text entry for numeric metadata.
    The hole is snapshotted when the dialog opens and the snapshot pushed for undo
    only if a save changes something, so all changes revert with a single Undo and a
    cancel leaves no empty undo step.
    """

    def __init__(self):
        self.dialog: MetadataDialog | None = None
        #: the hole as it was when the dialog opened
        self.before: HoleData | None = None

    def handle_mouse_down(self, pos, button, modifiers, context):
        if self.dialog is None:
            return ToolResult.not_handled()
        # Delegate to dialog
        if self.dialog and self.dialog.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)
        ):
            # Dialog wants to close
            return self._close_dialog(context)
        return ToolResult.handled()

    def handle_mouse_up(self, pos, button, context):
        return ToolResult.not_handled()

    def handle_mouse_motion(self, pos, context):
        return ToolResult.not_handled()

    def handle_key_down(self, key, modifiers, context):
        if self.dialog is None:
            # Never swallow the editor's keys once the dialog has closed
            return ToolResult.not_handled()
        if self.dialog:
            # Delegate to dialog
            event = pygame.event.Event(
                pygame.KEYDOWN, key=key, mod=modifiers, unicode=pygame.key.name(key)
            )
            # Need to set unicode properly for printable keys
            if 32 <= key <= 126:  # Printable ASCII range
                event = pygame.event.Event(
                    pygame.KEYDOWN, key=key, mod=modifiers, unicode=chr(key)
                )

            if self.dialog.handle_event(event):
                # Dialog wants to close
                return self._close_dialog(context)
        return ToolResult.handled()

    def handle_key_up(self, key, context):
        return ToolResult.not_handled()

    def on_activated(self, context):
        """Called when tool becomes active - create dialog and snapshot the hole."""
        self.before = context.state.undo_manager.snapshot(context.hole_data)

        font = font_cache.get_font(None, 24)

        # Create dialog
        self.dialog = MetadataDialog(
            context.screen_width,
            context.screen_height,
            context.hole_data,
            font,  # Use default font
        )

    def on_deactivated(self, context):
        """Called when tool is deactivated - close dialog."""
        self.dialog = None
        self.before = None

    def reset(self):
        """Reset tool state."""
        self.dialog = None
        self.before = None

    def get_hotkey(self) -> int | None:
        """Return 'D' key for Data/metadata editing."""
        return pygame.K_d

    def _close_dialog(self, context: ToolContext) -> ToolResult:
        """Close dialog and return appropriate result."""
        if self.dialog:
            saved = self.dialog.saved
            par_value = self.dialog.par_value
            distance_value = self.dialog.distance_value

            before = self.before

            # Clean up dialog state
            self.dialog = None
            self.before = None

            # Request revert to previous tool
            context.request_revert_to_previous_tool()

            changed = (
                before is not None and before.metadata != context.hole_data.metadata
            )
            if saved and not changed:
                return ToolResult(is_handled=True, message="Metadata unchanged")
            if saved:
                assert before is not None
                context.state.undo_manager.push_snapshot(before)
                # Changes were saved
                message = (
                    f"Metadata updated: Par={par_value}, Distance={distance_value}"
                )
                return ToolResult.modified(message=message)
            else:
                # Dialog was canceled
                return ToolResult(is_handled=True, message="Metadata edit canceled")

        return ToolResult.handled()

    def render_overlay(self, screen):
        """Render dialog overlay if active."""
        if self.dialog:
            self.dialog.render(screen)
