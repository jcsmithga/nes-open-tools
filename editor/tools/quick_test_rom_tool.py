"""Action tool that builds a quick-test ROM of the hole: it boots onto the hole's tee.

The ROM is built on the vanilla US ROM, which the editor cannot ship: the tool asks for
it the first time and remembers where it is. See docs/planning/quick_test_rom.md.
"""

import hashlib
import json
import sys
from pathlib import Path

import pygame

from editor.ui.dialogs import get_app_directory, open_file_dialog
from golf.core import rom_utils
from golf.core.patches import PatchError
from golf.core.patches.quick_test import build_quick_test_rom
from golf.core.rom_writer import BankOverflowError
from golf.formats.hole_check import rom_problems

from .base_tool import ToolContext, ToolResult

SETTINGS_PATH = Path.home() / ".config" / "golf-editor" / "settings.json"
ROM_SETTING = "vanilla_us_rom"
OUTPUT_NAME = "quick_test.nes"


def output_directory() -> Path:
    """Where the ROM goes: beside the bundled editor's executable, or the working
    directory when the editor runs from source, whose script is in `.venv/bin/`."""
    if getattr(sys, "frozen", False):
        return Path(get_app_directory())
    return Path.cwd()


def is_vanilla_us_rom(data: bytes) -> bool:
    return hashlib.sha1(data).hexdigest() == rom_utils.US_ROM_SHA1


class QuickTestRomTool:
    """Builds `quick_test.nes` from the hole as it is in the editor, saved or not."""

    def __init__(
        self,
        settings_path: Path = SETTINGS_PATH,
        output_dir: Path | None = None,
    ):
        self.settings_path = settings_path
        self.output_dir = output_dir

    # -- the vanilla ROM --------------------------------------------------

    def _read_settings(self) -> dict:
        try:
            settings = json.loads(self.settings_path.read_text())
        except (OSError, ValueError):
            return {}
        return settings if isinstance(settings, dict) else {}

    def _remembered_rom(self) -> bytes | None:
        path = self._read_settings().get(ROM_SETTING)
        if not isinstance(path, str):
            return None
        try:
            data = Path(path).read_bytes()
        except OSError:
            return None
        return data if is_vanilla_us_rom(data) else None

    def _remember_rom(self, path: Path) -> None:
        settings = self._read_settings()
        settings[ROM_SETTING] = str(path.resolve())
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            self.settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        except OSError:
            pass  # asked for again next time

    def _vanilla_rom(self, context: ToolContext) -> bytes | None:
        """The remembered vanilla ROM, or one the user picks; None if there is none."""
        data = self._remembered_rom()
        if data is not None:
            return data
        chosen = open_file_dialog(
            "Choose the vanilla NES Open Tournament Golf (USA) ROM",
            [("NES ROMs", "*.nes"), ("All files", "*.*")],
        )
        if not chosen:
            return None
        path = Path(chosen)
        try:
            data = path.read_bytes()
        except OSError as e:
            context.show_message(f"Could not read {path.name}: {e}")
            return None
        if not is_vanilla_us_rom(data):
            context.show_message(
                f"{path.name} is not the vanilla NES Open Tournament Golf (USA) ROM"
            )
            return None
        self._remember_rom(path)
        return data

    # -- the action -------------------------------------------------------

    def on_activated(self, context: ToolContext):
        """Build the ROM when activated (action tool)."""
        hole = context.hole_data
        if not hole.terrain:
            return
        problems = rom_problems(hole)
        if problems:
            context.show_message(f"Can't build a quick test ROM: {problems[0]}")
            return
        base = self._vanilla_rom(context)
        if base is None:
            return
        try:
            rom = build_quick_test_rom(base, hole)
        except (ValueError, BankOverflowError, PatchError) as e:
            context.show_message(f"Can't build a quick test ROM: {e}")
            return
        path = (self.output_dir or output_directory()) / OUTPUT_NAME
        try:
            path.write_bytes(rom)
        except OSError as e:
            context.show_message(f"Could not write {path}: {e}")
            return
        context.show_message(f"Wrote {path}")

    # -- the Tool protocol -----------------------------------------------

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

    def on_deactivated(self, context: ToolContext):
        pass

    def reset(self):
        pass

    def get_hotkey(self):
        return pygame.K_t

    def is_action_tool(self):
        return True
