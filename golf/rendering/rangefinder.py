"""Render every dumped hole for the randomizer site's rangefinder.

The rangefinder reads `metadata.json` and the PNGs under `images/<course id>/` from its
static directory. `render_rangefinder` rebuilds both from a courses root, taking the
courses whose directories hold holes, so a root with only the NES Open courses dumped
gives a rangefinder of those three.

The same directory holds `variants/`, the renders a seed's yardage book asks for
(`golf/rendering/hole_renders.py`). They are made on demand, so `render_rangefinder` only
clears them.
"""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from golf.core.chr_tile import TilesetData
from golf.rendering.pil_renderer import (
    render_flag_to_image,
    render_greens_to_image,
    render_hole_to_image,
)
from golf.rendering.pil_sprite import SPRITE_DIR, load_sprites

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
DEFAULT_TILESET = DATA_DIR / "chr-ram.bin"
DEFAULT_GREENS_TILESET = DATA_DIR / "green-ram.bin"
#: where the rangefinder's renders go unless GOLF_RANGEFINDER_DIR says otherwise; the
#: site serves them apart from its checked-in static files
DEFAULT_OUTPUT = REPO_ROOT / "rangefinder"

# The group label becomes an <optgroup> in the rangefinder's course selector, so the two
# games' courses stay visually separated even though both have a course named "Japan".
NES_OPEN_GROUP = "NES Open Tournament Golf"
MARIO_OPEN_GROUP = "Mario Open Golf (JP)"

#: the courses in dropdown order: (course id, path under the courses root, group label)
COURSES = [
    ("japan", "japan", NES_OPEN_GROUP),
    ("us", "us", NES_OPEN_GROUP),
    ("uk", "uk", NES_OPEN_GROUP),
    ("jp_japan", "jp/jp_japan", MARIO_OPEN_GROUP),
    ("jp_australia", "jp/jp_australia", MARIO_OPEN_GROUP),
    ("jp_france", "jp/jp_france", MARIO_OPEN_GROUP),
    ("jp_hawaii", "jp/jp_hawaii", MARIO_OPEN_GROUP),
    ("jp_uk", "jp/jp_uk", MARIO_OPEN_GROUP),
]

METADATA = "metadata.json"
IMAGES = "images"
#: the yardage books' renders, beside the rangefinder's own
VARIANTS = "variants"
#: how many pins a hole has
PINS = 4

#: Names what a hole renders as. Bump it when a change to the renderer, the tilesets, the
#: palettes or the sprites alters any image, or a change alters what `metadata.json`
#: says of a hole: `metadata.json` records it, so
#: `golf-rehydrate --check` fails on renders an older renderer made, and the yardage
#: books' image URLs carry it.
RENDER_VERSION = 2


class HoleRenderer:
    """Renders a hole's rangefinder images from its data, as a hole file's JSON holds it."""

    def __init__(
        self,
        tileset_path: Path = DEFAULT_TILESET,
        greens_tileset_path: Path = DEFAULT_GREENS_TILESET,
        sprite_dir: Path = SPRITE_DIR,
    ):
        self.tileset = TilesetData(str(tileset_path))
        self.greens_tileset = TilesetData(str(greens_tileset_path))
        self.sprites = load_sprites(sprite_dir)

    @property
    def has_flags(self) -> bool:
        return "green-flag" in self.sprites

    def main(self, hole: dict[str, Any], pin: int) -> Image.Image:
        """The whole hole, with its tee, ball and the flag at `pin`."""
        return render_hole_to_image(
            hole,
            self.tileset,
            sprites=self.sprites or None,
            render_sprites=True,
            selected_flag_index=pin,
        )

    def green(self, hole: dict[str, Any]) -> Image.Image:
        return render_greens_to_image(hole, self.greens_tileset)

    def flag(self, hole: dict[str, Any], pin: int) -> Image.Image:
        """The flag and cup at `pin`, transparent elsewhere, to lay over `green`."""
        return render_flag_to_image(
            hole,
            self.sprites["green-flag"],
            pin,
            cup_sprite=self.sprites.get("green-cup"),
        )


def render_rangefinder(
    courses_root: Path,
    output_dir: Path,
    tileset_path: Path = DEFAULT_TILESET,
    greens_tileset_path: Path = DEFAULT_GREENS_TILESET,
    flag_index: int = 0,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Replace the rangefinder's images and metadata with renders of `courses_root`.

    Returns the metadata written. Images left from an earlier render are removed first,
    so a course that is no longer dumped does not linger, and so are the yardage books'
    renders, which an earlier renderer may have made.
    """
    renderer = HoleRenderer(tileset_path, greens_tileset_path)

    shutil.rmtree(output_dir / IMAGES, ignore_errors=True)
    shutil.rmtree(output_dir / VARIANTS, ignore_errors=True)
    metadata = {"render_version": RENDER_VERSION, "courses": {}}

    for course_id, course_subpath, group in COURSES:
        course_dir = courses_root / course_subpath
        hole_files = sorted(course_dir.glob("hole_*.json"))
        if not hole_files:
            continue

        course_json_path = course_dir / "course.json"
        course_data = (
            json.loads(course_json_path.read_text())
            if course_json_path.exists()
            else {}
        )
        course_output_dir = output_dir / IMAGES / course_id
        course_output_dir.mkdir(parents=True, exist_ok=True)
        holes = []
        metadata["courses"][course_id] = {
            "name": course_data.get("name", course_id.capitalize()),
            "group": group,
            "holes": holes,
        }

        for hole_file in hole_files:
            hole_name = hole_file.stem
            hole_data = json.loads(hole_file.read_text())

            img = renderer.main(hole_data, flag_index)
            image_filename = f"{hole_name}.png"
            img.save(course_output_dir / image_filename)

            green_filename = f"{hole_name}_green.png"
            renderer.green(hole_data).save(course_output_dir / green_filename)

            flag_images = []
            if renderer.has_flags:
                for i in range(PINS):
                    flag_filename = f"{hole_name}_flag_{i}.png"
                    renderer.flag(hole_data, i).save(course_output_dir / flag_filename)
                    flag_images.append(f"{IMAGES}/{course_id}/{flag_filename}")

            holes.append(
                {
                    "number": hole_data.get("hole", 1),
                    "par": hole_data.get("par", 4),
                    "distance": hole_data.get("distance", 0),
                    # the hole's handicap on its own course, which ranks that course's 18
                    "stroke_index": hole_data.get("handicap"),
                    "image": f"{IMAGES}/{course_id}/{image_filename}",
                    "width": img.width,
                    "height": img.height,
                    "green_image": f"{IMAGES}/{course_id}/{green_filename}",
                    "flag_images": flag_images,
                }
            )
        if progress is not None:
            progress(f"{course_id}: {len(holes)} holes")

    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / METADATA, "w") as f:
        json.dump(metadata, f, indent=2)
    return metadata
