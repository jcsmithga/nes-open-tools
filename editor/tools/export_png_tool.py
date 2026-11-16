"""Action tool that exports the hole as two PNGs: the terrain and the green."""

from pathlib import Path

import pygame

from editor.resources import get_resource_path
from editor.ui.dialogs import save_file_dialog
from golf.rendering.hole_export import EXPORT_SCALE, export_hole_pngs
from golf.rendering.rangefinder import HoleRenderer

from .base_tool import ToolContext, ToolResult


class ExportPngTool:
    """Asks for a file name and writes `<name>_terrain.png` and `<name>_green.png`."""

    def __init__(self):
        self._renderer: HoleRenderer | None = None

    def _get_renderer(self) -> HoleRenderer:
        # Loaded on first use, from the paths the bundled editor has them at
        if self._renderer is None:
            self._renderer = HoleRenderer(
                get_resource_path("data/chr-ram.bin"),
                get_resource_path("data/green-ram.bin"),
                get_resource_path("data/sprites"),
            )
        return self._renderer

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

    def on_activated(self, context: ToolContext):
        """Export when activated (action tool)."""
        hole = context.hole_data
        if not hole.terrain:
            return
        initial_dir = str(Path(hole.filepath).parent) if hole.filepath else None
        path = save_file_dialog(
            "Export PNG",
            ".png",
            [("PNG images", "*.png"), ("All files", "*.*")],
            initial_dir,
        )
        if not path:
            return
        try:
            terrain, green = export_hole_pngs(
                self._get_renderer(), hole.to_dict(), Path(path), EXPORT_SCALE
            )
        except OSError as e:
            context.show_message(f"Could not export {Path(path).name}: {e}")
            return
        context.show_message(f"Exported {terrain.name} and {green.name}")

    def on_deactivated(self, context: ToolContext):
        pass

    def reset(self):
        pass

    def get_hotkey(self):
        return pygame.K_e

    def is_action_tool(self):
        return True
