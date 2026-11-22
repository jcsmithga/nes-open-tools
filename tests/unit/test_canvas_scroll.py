"""Scrolling the editor's canvas with the mouse wheel and the arrow keys."""

from unittest.mock import patch

import pygame
import pytest

from editor.application import ROWS_ABOVE_COURSE, EditorApplication
from editor.controllers.event_handler import (
    SCROLL_REPEAT_DELAY_MS,
    SCROLL_REPEAT_INTERVAL_MS,
)
from editor.core.constants import GREENS_HEIGHT, TERRAIN_WIDTH, TILE_SIZE
from golf.formats.hole_data import MAX_TERRAIN_ROWS
from tests.synthetic_holes import synthetic_hole

TILE = TILE_SIZE * 4  # the canvas draws tiles at 4x


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    application = EditorApplication("data/chr-ram.bin", "data/green-ram.bin")
    path = tmp_path / "hole.json"
    synthetic_hole().save(str(path))  # 30 rows by 22 columns
    assert application.load_hole(str(path))
    # a window smaller than the hole, so it scrolls both ways
    application._on_resize(1000, 700)
    yield application
    pygame.quit()


def send(app, *events):
    with patch("pygame.key.get_mods", return_value=0):
        app.event_handler.handle_events(list(events))


def wheel(app, button, notches=1):
    """Turn the wheel over the canvas: button 4 scrolls up, 5 down."""
    center = app._get_canvas_rect().center
    for _ in range(notches):
        send(app, pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=center))


def key_down(app, key, at=0):
    with patch("pygame.time.get_ticks", return_value=at):
        send(app, pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode=""))


def key_up(app, key):
    send(app, pygame.event.Event(pygame.KEYUP, key=key, mod=0, unicode=""))


def offset(app):
    return app.state.canvas_offset_x, app.state.canvas_offset_y


def furthest(app, columns, rows):
    canvas = app._get_canvas_rect()
    return (
        max(0, columns * TILE - canvas.width),
        max(0, rows * TILE - canvas.height),
    )


def test_one_wheel_notch_scrolls_one_tile(app):
    wheel(app, 5)
    assert offset(app) == (0, TILE)
    wheel(app, 4)
    assert offset(app) == (0, 0)


@pytest.mark.parametrize(
    ("key", "moved"),
    [
        (pygame.K_DOWN, (0, TILE)),
        (pygame.K_RIGHT, (TILE, 0)),
    ],
)
def test_one_arrow_press_scrolls_one_tile(app, key, moved):
    key_down(app, key)
    assert offset(app) == moved


def test_a_held_arrow_scrolls_again_after_a_pause_then_steadily(app):
    key_down(app, pygame.K_DOWN, at=1000)
    assert offset(app) == (0, TILE)

    app.event_handler.repeat_held_scrolls(1000 + SCROLL_REPEAT_DELAY_MS - 1)
    assert offset(app) == (0, TILE)
    app.event_handler.repeat_held_scrolls(1000 + SCROLL_REPEAT_DELAY_MS)
    assert offset(app) == (0, 2 * TILE)
    app.event_handler.repeat_held_scrolls(
        1000 + SCROLL_REPEAT_DELAY_MS + SCROLL_REPEAT_INTERVAL_MS
    )
    assert offset(app) == (0, 3 * TILE)


def test_letting_go_of_the_arrow_stops_the_scrolling(app):
    key_down(app, pygame.K_DOWN, at=1000)
    key_up(app, pygame.K_DOWN)
    app.event_handler.repeat_held_scrolls(10_000)
    assert offset(app) == (0, TILE)


def test_losing_the_window_stops_the_scrolling(app):
    key_down(app, pygame.K_DOWN, at=1000)
    send(app, pygame.event.Event(pygame.WINDOWFOCUSLOST))
    app.event_handler.repeat_held_scrolls(10_000)
    assert offset(app) == (0, TILE)


def test_scrolling_stops_at_row_60_and_the_right_edge(app):
    wheel(app, 5, notches=100)
    for _ in range(100):
        key_down(app, pygame.K_RIGHT)
    bottom_right = furthest(app, TERRAIN_WIDTH, MAX_TERRAIN_ROWS)
    assert bottom_right[0] > 0
    assert bottom_right[1] > 0
    assert offset(app) == bottom_right


def test_scrolling_stops_at_the_left(app):
    key_down(app, pygame.K_RIGHT)
    for _ in range(5):
        key_down(app, pygame.K_LEFT)
    assert offset(app) == (0, 0)


def test_the_course_scrolls_up_into_20_empty_rows_and_no_further(app):
    wheel(app, 4, notches=100)
    assert offset(app) == (0, -ROWS_ABOVE_COURSE * TILE)


def test_the_green_does_not_scroll_above_its_top(app):
    app._toggle_mode()
    wheel(app, 4, notches=100)
    assert app.state.canvas_offset_y == 0


def test_the_green_stops_at_its_own_edges(app):
    app._toggle_mode()
    assert app.state.mode == "greens"
    wheel(app, 5, notches=100)
    assert app.state.canvas_offset_y == furthest(app, 0, GREENS_HEIGHT)[1]


def test_a_taller_window_brings_a_scrolled_view_back_within_row_60(app):
    wheel(app, 5, notches=100)
    app._on_resize(1000, 1400)
    app._clamp_canvas_offset()
    assert app.state.canvas_offset_y == furthest(app, 0, MAX_TERRAIN_ROWS)[1]


def test_a_course_narrower_than_the_window_moves_across_it_and_no_further(app):
    app._on_resize(1800, 700)
    room = app._get_canvas_rect().width - TERRAIN_WIDTH * TILE
    assert room > 0
    for _ in range(100):
        key_down(app, pygame.K_LEFT)  # the view goes left, so the course goes right
    assert offset(app) == (-room, 0)  # its right edge meets the canvas's
    for _ in range(100):
        key_down(app, pygame.K_RIGHT)
    assert offset(app) == (0, 0)  # back against the left edge


def test_a_narrower_window_brings_a_moved_course_back(app):
    app._on_resize(1800, 700)
    for _ in range(100):
        key_down(app, pygame.K_LEFT)
    app._on_resize(1000, 700)
    app._clamp_canvas_offset()
    assert offset(app) == (0, 0)


def test_the_position_tool_keeps_the_arrows_for_itself(app):
    app.tool_manager.set_active_tool("position", app.event_handler.tool_context)
    key_down(app, pygame.K_DOWN, at=1000)
    app.event_handler.repeat_held_scrolls(10_000)
    assert offset(app) == (0, 0)


def test_other_keys_held_down_do_not_repeat(app):
    key_down(app, pygame.K_g, at=1000)
    assert app.event_handler._held_scroll_keys == {}


# Dragging with the middle button


def press_button(app, button, pos):
    send(app, pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=button, pos=pos))


def release_button(app, button, pos):
    send(app, pygame.event.Event(pygame.MOUSEBUTTONUP, button=button, pos=pos))


def move(app, pos, middle_held=True):
    buttons = (0, 1 if middle_held else 0, 0)
    send(
        app,
        pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0), buttons=buttons),
    )


def test_dragging_with_the_middle_button_moves_the_hole_with_the_mouse(app):
    x, y = app._get_canvas_rect().center
    press_button(app, 2, (x, y))
    move(
        app, (x - 50, y - 100)
    )  # pull the hole up and left: see further down and right
    assert offset(app) == (50, 100)
    move(app, (x - 30, y - 60))
    assert offset(app) == (30, 60)
    release_button(app, 2, (x - 30, y - 60))
    move(app, (x - 200, y - 200), middle_held=False)
    assert offset(app) == (30, 60)


def test_a_drag_stops_at_the_edges(app):
    x, y = app._get_canvas_rect().center
    press_button(app, 2, (x, y))
    move(app, (x - 5000, y - 5000))
    assert offset(app) == furthest(app, TERRAIN_WIDTH, MAX_TERRAIN_ROWS)
    move(app, (x + 5000, y + 5000))
    assert offset(app) == (0, -ROWS_ABOVE_COURSE * TILE)


def test_a_drag_let_go_outside_the_window_ends_at_the_next_move(app):
    x, y = app._get_canvas_rect().center
    press_button(app, 2, (x, y))
    move(app, (x, y - 40), middle_held=False)  # the button came up out of sight
    assert offset(app) == (0, 0)
    move(app, (x, y - 80))
    assert offset(app) == (0, 0)


def test_dragging_with_the_left_button_does_not_move_the_hole(app):
    x, y = app._get_canvas_rect().center
    press_button(app, 1, (x, y))
    send(
        app,
        pygame.event.Event(
            pygame.MOUSEMOTION, pos=(x, y - 100), rel=(0, 0), buttons=(1, 0, 0)
        ),
    )
    assert offset(app) == (0, 0)


def test_a_middle_click_off_the_hole_view_does_not_start_a_drag(app):
    toolbar = (app._get_canvas_rect().centerx, 5)
    press_button(app, 2, toolbar)
    move(app, (toolbar[0], toolbar[1] + 300))
    assert offset(app) == (0, 0)


def test_a_tool_that_takes_the_middle_click_keeps_it(app):
    # the metadata dialog takes every click while it is open
    key_down(app, pygame.K_d)
    assert app.tool_manager.active_tool_name == "metadata_editor"
    x, y = app._get_canvas_rect().center
    press_button(app, 2, (x, y))
    move(app, (x - 50, y - 100))
    assert offset(app) == (0, 0)