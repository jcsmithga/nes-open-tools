"""PNG exports of a hole, as the editor's Export PNG tool writes them.

Two images, scaled up by a whole number so each NES pixel stays a sharp square: the
terrain with its tee, ball and first pin's flag, and the green with all its pins.
"""

from pathlib import Path
from typing import Any

from PIL import Image

from golf.rendering.rangefinder import PINS, HoleRenderer

#: how many screen pixels square each NES pixel becomes
EXPORT_SCALE = 3


def _scaled(image: Image.Image, scale: int) -> Image.Image:
    return image.resize(
        (image.width * scale, image.height * scale), Image.Resampling.NEAREST
    )


def terrain_image(
    renderer: HoleRenderer, hole: dict[str, Any], scale: int = EXPORT_SCALE
) -> Image.Image:
    """The whole hole with its tee, ball and the flag at the first pin."""
    return _scaled(renderer.main(hole, 0), scale)


def green_image(
    renderer: HoleRenderer, hole: dict[str, Any], scale: int = EXPORT_SCALE
) -> Image.Image:
    """The green with the flag and cup at every pin."""
    green = renderer.green(hole).convert("RGBA")
    if renderer.has_flags:
        for pin in range(PINS):
            green.alpha_composite(renderer.flag(hole, pin))
    return _scaled(green.convert("RGB"), scale)


def export_paths(base: Path) -> tuple[Path, Path]:
    """Where the terrain and green images go for a chosen file name: the name's stem
    with `_terrain` and `_green`, so `hole_05.png` gives `hole_05_terrain.png` and
    `hole_05_green.png`."""
    stem = base.with_suffix("")
    return (
        stem.with_name(f"{stem.name}_terrain.png"),
        stem.with_name(f"{stem.name}_green.png"),
    )


def export_hole_pngs(
    renderer: HoleRenderer,
    hole: dict[str, Any],
    base: Path,
    scale: int = EXPORT_SCALE,
) -> tuple[Path, Path]:
    """Write the terrain and green images beside `base` (see `export_paths`) and return
    their paths."""
    terrain_path, green_path = export_paths(base)
    terrain_image(renderer, hole, scale).save(terrain_path)
    green_image(renderer, hole, scale).save(green_path)
    return terrain_path, green_path
