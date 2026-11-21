"""Scrolling the editor's tile pickers with the mouse wheel."""

import pygame
import pytest

from editor.application import EditorApplication


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    application = EditorApplication("data/chr-ram.bin", "data/green-ram.bin")
    yield application
    pygame.quit()


@pytest.fixture(params=["terrain", "greens"])
def picker(request, app):
    return app.terrain_picker if request.param == "terrain" else app.greens_picker


def wheel(picker, button, notches=1):
    """Turn the wheel over the picker: button 4 scrolls up, 5 down."""
    for _ in range(notches):
        event = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=button, pos=picker.rect.center
        )
        assert picker.handle_event(event)


def test_one_notch_scrolls_one_labeled_row_of_tiles(picker):
    # a sub-bank's gap and label (4 + 14) and a row of 8-pixel tiles at 4x with 2 apart
    wheel(picker, 5)
    assert picker.scroll_y == 52
    wheel(picker, 4)
    assert picker.scroll_y == 0


def test_scrolling_down_stops_with_the_last_bank_at_the_bottom(picker):
    wheel(picker, 5, notches=200)
    bottom = picker._content_height - picker.rect.height
    assert bottom > 0
    assert picker.scroll_y == bottom
    wheel(picker, 5)
    assert picker.scroll_y == bottom


def test_scrolling_up_stops_at_the_top(picker):
    wheel(picker, 5, notches=2)
    wheel(picker, 4, notches=5)
    assert picker.scroll_y == 0


def test_a_taller_picker_gives_back_what_it_can_no_longer_scroll(picker):
    wheel(picker, 5, notches=200)
    picker.resize(picker.rect.inflate(0, 400))
    assert picker.scroll_y == max(0, picker._content_height - picker.rect.height)