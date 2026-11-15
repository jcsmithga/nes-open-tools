"""The editor application end to end, under SDL's dummy video driver."""

import json
from unittest.mock import patch

import pygame
import pytest

from editor.application import EditorApplication
from editor.ui.confirm_dialog import UnsavedChoice
from golf.formats.hole_data import HoleData
from tests.synthetic_holes import synthetic_hole


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    application = EditorApplication("data/chr-ram.bin", "data/green-ram.bin")
    application.clock = pygame.time.Clock()
    yield application
    pygame.quit()


@pytest.fixture
def hole_file(tmp_path):
    path = tmp_path / "hole.json"
    synthetic_hole().save(str(path))
    return path


def press(app, key):
    with patch("pygame.key.get_mods", return_value=0):
        return app.event_handler.handle_events(
            [pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="")]
        )


def test_the_metadata_dialog_opened_by_hotkey_returns_to_the_tool_before_it(app):
    press(app, pygame.K_d)
    assert app.tool_manager.active_tool_name == "metadata_editor"

    press(app, pygame.K_ESCAPE)

    assert app.tool_manager.active_tool_name == "paint"
    assert app.tool_picker.selected_tool == "paint"
    press(app, pygame.K_f)
    assert app.tool_manager.active_tool_name == "forest_fill"


def test_tab_to_terrain_leaves_carpet_paint_for_paint(app):
    app._set_mode("greens")
    app._on_tool_change("carpet_paint")

    press(app, pygame.K_TAB)

    assert app.state.mode == "terrain"
    assert app.tool_manager.active_tool_name == "paint"
    assert app.toolbar.get_mode_buttons()[0].active


def test_a_hole_loads(app, hole_file):
    assert app.load_hole(str(hole_file))
    assert app.hole_data.filepath == str(hole_file)
    assert app.hole_data.terrain == synthetic_hole().terrain


@pytest.mark.parametrize(
    "content",
    ["{not json", json.dumps({"holes": []}), json.dumps([1, 2])],
    ids=["bad json", "not a hole", "not an object"],
)
def test_a_file_that_is_not_a_hole_keeps_the_open_hole(
    app, hole_file, tmp_path, content
):
    app.load_hole(str(hole_file))
    bad = tmp_path / "bad.json"
    bad.write_text(content)

    assert not app.load_hole(str(bad))

    assert app.hole_data.filepath == str(hole_file)
    assert app.hole_data.terrain == synthetic_hole().terrain
    assert app.state.tool_message.startswith("Could not load bad.json")


def test_a_missing_file_does_not_crash(app, tmp_path):
    assert not app.load_hole(str(tmp_path / "gone.json"))
    assert "gone.json" in app.state.tool_message


def modify(app):
    app.hole_data.set_terrain_tile(0, 0, 0x30)
    assert app.hole_data.modified


@pytest.mark.parametrize(
    "choice, quits",
    [(UnsavedChoice.DISCARD, True), (UnsavedChoice.CANCEL, False)],
)
def test_quitting_with_unsaved_changes_asks_first(app, hole_file, choice, quits):
    app.load_hole(str(hole_file))
    modify(app)

    with patch("editor.application.UnsavedChangesDialog.show", return_value=choice):
        running = app.event_handler.handle_events([pygame.event.Event(pygame.QUIT)])

    assert running is not quits


def test_save_from_the_unsaved_changes_dialog_saves_then_quits(app, hole_file):
    app.load_hole(str(hole_file))
    modify(app)

    with patch(
        "editor.application.UnsavedChangesDialog.show",
        return_value=UnsavedChoice.SAVE,
    ):
        running = app.event_handler.handle_events([pygame.event.Event(pygame.QUIT)])

    assert running is False
    assert json.loads(hole_file.read_text())["terrain"]["rows"][0].startswith("30")


def test_quitting_without_changes_does_not_ask(app, hole_file):
    app.load_hole(str(hole_file))
    with patch("editor.application.UnsavedChangesDialog.show") as show:
        running = app.event_handler.handle_events([pygame.event.Event(pygame.QUIT)])
    assert running is False
    show.assert_not_called()


def test_canceling_keeps_the_hole_when_another_is_dropped(app, hole_file, tmp_path):
    app.load_hole(str(hole_file))
    modify(app)
    other = tmp_path / "other.json"
    synthetic_hole(2).save(str(other))

    with patch(
        "editor.application.UnsavedChangesDialog.show",
        return_value=UnsavedChoice.CANCEL,
    ):
        app._on_load_file(str(other))

    assert app.hole_data.filepath == str(hole_file)
    assert app.hole_data.terrain[0][0] == 0x30


def test_the_side_pickers_follow_a_window_resize(app):
    app._on_resize(1200, 700)

    for picker in (app.terrain_picker, app.greens_picker, app.stamp_browser):
        assert picker.rect.bottom == app.tool_picker.rect.bottom
        assert picker.rect.bottom == app._get_canvas_rect().bottom


def test_the_window_fits_the_desktop(app):
    desktop_w, desktop_h = pygame.display.get_desktop_sizes()[0]
    assert app.screen_width <= desktop_w
    assert app.screen_height <= desktop_h


def press_ctrl(app, key):
    with patch("pygame.key.get_mods", return_value=pygame.KMOD_CTRL):
        app.event_handler.handle_events(
            [pygame.event.Event(pygame.KEYDOWN, key=key, mod=pygame.KMOD_CTRL)]
        )


def test_undo_back_to_the_saved_hole_leaves_nothing_to_save(app, hole_file):
    app.load_hole(str(hole_file))
    app.state.undo_manager.push_state(app.hole_data)
    modify(app)

    press_ctrl(app, pygame.K_z)

    assert not app.hole_data.modified


def test_undo_after_saving_leaves_something_to_save(app, hole_file):
    app.load_hole(str(hole_file))
    app.state.undo_manager.push_state(app.hole_data)
    modify(app)
    app._on_save()

    press_ctrl(app, pygame.K_z)

    assert app.hole_data.modified


def test_creating_a_stamp_without_a_selection_says_so(app):
    app._on_create_stamp()
    assert (app.state.tool_message or "").startswith("Create stamp:")


def test_green_fix_in_terrain_mode_says_so(app, hole_file):
    app.load_hole(str(hole_file))
    press(app, pygame.K_u)
    assert app.state.tool_message == "Green Fix: Only works in greens mode"


def test_add_row_at_the_limit_says_so(app, hole_file):
    app.load_hole(str(hole_file))
    app.hole_data.terrain_height = 60

    press(app, pygame.K_EQUALS)

    assert app.state.tool_message == "Cannot exceed 60 rows"


def test_messages_from_a_tools_update_reach_the_status_bar(app):
    from editor.tools.base_tool import ToolResult

    app._process_tool_result(ToolResult(is_handled=True, message="hello"))
    assert app.state.tool_message == "hello"


def test_stamps_that_fail_to_load_are_reported(app, tmp_path):
    (tmp_path / "broken.json").write_text("{")
    app.stamp_library.user_path = tmp_path

    app._load_stamps()

    assert "broken.json" in (app.state.tool_message or "")


def press_ctrl_shift(app, key):
    mods = pygame.KMOD_CTRL | pygame.KMOD_SHIFT
    with patch("pygame.key.get_mods", return_value=mods):
        app.event_handler.handle_events(
            [pygame.event.Event(pygame.KEYDOWN, key=key, mod=mods)]
        )


def test_ctrl_n_starts_a_blank_hole_with_no_file(app):
    press_ctrl(app, pygame.K_n)

    assert app.hole_data.to_dict() == HoleData.blank().to_dict()
    assert app.hole_data.filepath is None
    assert not app.hole_data.modified
    assert not app.state.undo_manager.can_undo()


def test_new_asks_about_unsaved_changes_first(app, hole_file):
    app.load_hole(str(hole_file))
    modify(app)

    with patch(
        "editor.application.UnsavedChangesDialog.show",
        return_value=UnsavedChoice.CANCEL,
    ):
        app._on_new()

    assert app.hole_data.filepath == str(hole_file)
    assert app.hole_data.terrain[0][0] == 0x30


def test_saving_a_new_hole_asks_where(app, tmp_path):
    app._on_new()
    path = tmp_path / "mine.json"

    with patch("editor.application.save_file_dialog", return_value=str(path)):
        press_ctrl(app, pygame.K_s)

    assert app.hole_data.filepath == str(path)
    assert HoleData.from_dict(json.loads(path.read_text())).terrain == (
        HoleData.blank().terrain
    )
    assert app.state.tool_message == "Hole saved!"


def test_save_as_saves_a_copy_and_works_on_it(app, hole_file, tmp_path):
    app.load_hole(str(hole_file))
    before = hole_file.read_text()
    modify(app)
    copy = tmp_path / "copy.json"

    with patch("editor.application.save_file_dialog", return_value=str(copy)) as ask:
        press_ctrl_shift(app, pygame.K_s)

    ask.assert_called_once()
    assert hole_file.read_text() == before
    assert app.hole_data.filepath == str(copy)
    assert not app.hole_data.modified
    assert json.loads(copy.read_text())["terrain"]["rows"][0].startswith("30")


def test_canceling_save_as_saves_nothing(app, hole_file):
    app.load_hole(str(hole_file))
    modify(app)

    with patch("editor.application.save_file_dialog", return_value=None):
        app._on_save_as()

    assert app.hole_data.filepath == str(hole_file)
    assert app.hole_data.modified


def test_saving_a_hole_a_rom_cannot_hold_warns(app, hole_file):
    app._on_new()
    app.hole_data.metadata["par"] = 6
    app.hole_data.filepath = str(hole_file)

    app._on_save()

    assert app.state.tool_message == "Hole saved, but Check finds 1 problem(s)"


def test_check_names_every_problem(app):
    app._on_new()
    app.hole_data.metadata["par"] = 6
    app.hole_data.set_terrain_tile(0, 0, 0x100)

    with patch("editor.application.MessageDialog") as dialog:
        app._on_check()

    _, _, title, paragraphs, _ = dialog.call_args.args
    assert title == "Check: 2 problem(s)"
    assert any("placeholder" in line for line in paragraphs)
    assert any("par must be" in line for line in paragraphs)
    assert app.state.tool_message == title


def test_check_passes_a_blank_hole(app):
    app._on_new()
    with patch("editor.application.MessageDialog") as dialog:
        app._on_check()
    assert dialog.call_args.args[2] == "Check: no problems"


def test_the_toolbar_has_the_file_commands(app):
    labels = [button.text for button in app.toolbar.file_buttons]
    assert labels == ["New", "Load", "Save", "Save As", "Check"]
