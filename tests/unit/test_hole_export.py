"""The editor's PNG export: a terrain image and a green image, scaled up."""

from pathlib import Path

import pytest
from PIL import Image

from editor.tools import export_png_tool
from editor.tools.export_png_tool import ExportPngTool
from golf.rendering.hole_export import (
    EXPORT_SCALE,
    export_hole_pngs,
    export_paths,
    green_image,
    terrain_image,
)
from golf.rendering.rangefinder import HoleRenderer
from tests.synthetic_holes import synthetic_hole


@pytest.fixture(scope="module")
def renderer():
    return HoleRenderer()


@pytest.fixture
def hole():
    return synthetic_hole(3).to_dict()


def test_the_paths_share_the_chosen_name(tmp_path):
    assert export_paths(tmp_path / "hole_05.png") == (
        tmp_path / "hole_05_terrain.png",
        tmp_path / "hole_05_green.png",
    )
    assert export_paths(tmp_path / "hole_05") == export_paths(tmp_path / "hole_05.png")


def test_each_nes_pixel_becomes_a_square(renderer, hole):
    plain = renderer.main(hole, 0)
    scaled = terrain_image(renderer, hole, scale=3)
    assert scaled.size == (plain.width * 3, plain.height * 3)
    for x, y in [(0, 0), (40, 17), (plain.width - 1, plain.height - 1)]:
        block = {
            scaled.getpixel((x * 3 + dx, y * 3 + dy))
            for dx in range(3)
            for dy in range(3)
        }
        assert block == {plain.getpixel((x, y))}


def test_the_terrain_shows_the_first_pin(renderer, hole):
    before = terrain_image(renderer, hole).tobytes()
    hole["flag_positions"][1]["x_offset"] = 160
    assert terrain_image(renderer, hole).tobytes() == before
    hole["flag_positions"][0]["x_offset"] = 160
    assert terrain_image(renderer, hole).tobytes() != before


def test_the_green_shows_every_pin(renderer, hole):
    green = green_image(renderer, hole, scale=1)
    assert green.size == (192, 192)
    for pin in range(4):
        fewer = dict(
            hole,
            flag_positions=[
                p for i, p in enumerate(hole["flag_positions"]) if i != pin
            ],
        )
        assert green.tobytes() != green_image(renderer, fewer, scale=1).tobytes()


def test_export_writes_both_images(tmp_path, renderer, hole):
    terrain, green = export_hole_pngs(renderer, hole, tmp_path / "out.png")
    assert Image.open(terrain).size == (176 * EXPORT_SCALE, 240 * EXPORT_SCALE)
    assert Image.open(green).size == (192 * EXPORT_SCALE, 192 * EXPORT_SCALE)


class FakeContext:
    def __init__(self, hole_data):
        self.hole_data = hole_data
        self.messages: list[str | None] = []

    def show_message(self, message):
        self.messages.append(message)


def test_the_tool_exports_where_the_dialog_says(tmp_path, monkeypatch):
    chosen = tmp_path / "mine.png"
    monkeypatch.setattr(export_png_tool, "save_file_dialog", lambda *a: str(chosen))
    context = FakeContext(synthetic_hole(3))
    ExportPngTool().on_activated(context)  # pyright: ignore[reportArgumentType]
    assert {p.name for p in Path(tmp_path).iterdir()} == {
        "mine_terrain.png",
        "mine_green.png",
    }
    assert context.messages == ["Exported mine_terrain.png and mine_green.png"]


def test_canceling_the_dialog_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(export_png_tool, "save_file_dialog", lambda *a: None)
    context = FakeContext(synthetic_hole(3))
    ExportPngTool().on_activated(context)  # pyright: ignore[reportArgumentType]
    assert context.messages == []
