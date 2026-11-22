"""Zooming the editor's canvas with Ctrl and the mouse wheel."""

from unittest.mock import patch

import pygame
import pytest

from editor.application import USUAL_SCALE, ZOOM_SCALES, EditorApplication
from editor.core.constants import TILE_SIZE
from tests.synthetic_holes import synthetic_hole


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    application = EditorApplication("data/chr-ram.bin", "data/green-ram.bin")
    path = tmp_path / "hole.json"
    synthetic_hole().save(str(path))
    assert application.load_hole(str(path))
    application._on_resize(1000, 700)
    yield application
    pygame.quit()


def wheel(app, button, pos=None, ctrl=True, notches=1):
    """Turn the wheel: button 4 is up (zoom in), 5 down (zoom out)."""
    pos = pos or app._get_canvas_rect().center
    mods = pygame.KMOD_LCTRL if ctrl else 0
    with patch("pygame.key.get_mods", return_value=mods):
        for _ in range(notches):
            app.event_handler.handle_events(
                [pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos)]
            )


def spot_under(app, pos):
    """The NES pixel of the hole under a screen position."""
    return app.event_handler.tool_context.view_state.screen_to_game_pixels(pos)


def test_a_hole_opens_at_the_usual_size(app):
    assert app.state.canvas_scale == USUAL_SCALE == 4


def test_ctrl_and_the_wheel_step_through_the_zoom_levels(app):
    seen = []
    wheel(app, 5, notches=10)
    for _ in ZOOM_SCALES:
        seen.append(app.state.canvas_scale)
        wheel(app, 4)
    assert seen == [2, 3, 4, 8, 12, 16]


@pytest.mark.parametrize(
    ("button", "scale", "message"), [(4, 16, "Zoom 4x"), (5, 2, "Zoom 0.5x")]
)
def test_zooming_stops_at_the_last_level(app, button, scale, message):
    wheel(app, button, notches=10)
    assert app.state.canvas_scale == scale
    assert app.state.tool_message == message


def test_the_status_bar_says_the_zoom_level(app):
    wheel(app, 4)
    assert app.state.tool_message == "Zoom 2x"


def test_the_wheel_without_ctrl_still_scrolls(app):
    wheel(app, 5, ctrl=False)
    assert app.state.canvas_scale == USUAL_SCALE
    assert app.state.canvas_offset_y == TILE_SIZE * USUAL_SCALE


def test_the_spot_under_the_mouse_stays_put(app):
    canvas = app._get_canvas_rect()
    pos = (canvas.x + 150, canvas.y + 200)
    before = spot_under(app, pos)
    wheel(app, 4, pos=pos)
    assert spot_under(app, pos) == before
    wheel(app, 4, pos=pos)
    assert spot_under(app, pos) == before


def test_one_notch_out_from_the_usual_size_is_three_quarters(app):
    wheel(app, 5)
    assert app.state.canvas_scale == 3
    assert app.state.tool_message == "Zoom 0.75x"


def test_scrolling_moves_one_tile_at_any_zoom(app):
    wheel(app, 4)  # 2x
    before = app.state.canvas_offset_y
    wheel(app, 5, ctrl=False)
    assert app.state.canvas_offset_y - before == TILE_SIZE * 8


def test_the_course_and_the_green_keep_their_own_zoom(app):
    wheel(app, 4)
    app._toggle_mode()
    assert app.state.canvas_scale == USUAL_SCALE
    wheel(app, 5)
    app._toggle_mode()
    assert app.state.canvas_scale == 8


def test_ctrl_and_the_wheel_over_the_tile_picker_scroll_the_picker(app):
    picker = app.terrain_picker
    wheel(app, 5, pos=picker.rect.center)
    assert app.state.canvas_scale == USUAL_SCALE
    assert picker.scroll_y > 0


@pytest.mark.parametrize("mode", ["terrain", "greens"])
@pytest.mark.parametrize("window_size", [(1000, 700), (1500, 1000), (1900, 1100)])
@pytest.mark.parametrize("notches", [-2, -1, 0, 1, 2, 3])  # 0.5x to 4x
def test_at_every_zoom_a_grid_goes_half_off_or_its_edge_to_the_middle(
    app, notches, window_size, mode
):
    app._on_resize(*window_size)
    if mode == "greens":
        app._toggle_mode()
    app._zoom_canvas(notches, app._get_canvas_rect().center)
    canvas = app._get_canvas_rect()
    tile = TILE_SIZE * app.state.canvas_scale
    columns, rows = (24, 24) if mode == "greens" else (22, 30)
    for size, view, axis in (
        (columns * tile, canvas.width, 0),
        (rows * tile, canvas.height, 1),
    ):
        app._pan_canvas(*((100_000, 0) if axis == 0 else (0, 100_000)))
        hidden_before = (app.state.canvas_offset_x, app.state.canvas_offset_y)[axis]
        app._pan_canvas(*((-100_000, 0) if axis == 0 else (0, -100_000)))
        start = (app.state.canvas_offset_x, app.state.canvas_offset_y)[axis]
        hidden_after = size - (start + view)
        # half of it off, or its edge in to the middle if that is further
        assert hidden_before == hidden_after == max(size // 2, size - view // 2)
        # either way, its edge comes in at least to the middle of the view
        assert size - hidden_before <= view - view // 2
