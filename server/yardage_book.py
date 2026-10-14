"""A seed's yardage book: the rangefinder's metadata for its 18 holes as the seed plays them.

The book shows each hole with its transforms applied, at the seed's pin, with the wind of
its tee shot. Every hole is read from `hole_data`, where the seed's build left it
(ADR 0021, ADR 0024), so the book needs neither the catalog nor the hole store. The images
are rendered the first time a book asks for them (`golf/rendering/hole_renders.py`).

The wind after the tee shot is left out on purpose: every later swing's wind follows from
the manifest too, but a book that showed it would decide the tee shot.
"""

from typing import Any

from golf.core.rng import predict_hole
from golf.randomizer.manifest import Slot
from golf.randomizer.wind import compass
from golf.rendering.hole_renders import HoleRenders
from golf.rendering.rangefinder import RENDER_VERSION

from .db import Database
from .seeds import SeedRow, load_hole_data, load_seed_holes
from .static_files import VERSION_PARAM

#: the book's one course, as the rangefinder's metadata keys it
COURSE = "seed"


def tee_wind(slot: Slot) -> tuple[int, int]:
    """The (direction, speed) the game shows for the hole's first swing.

    The wind slot advances one step a swing from the hole's wind seed
    (`docs/seeded_wind.md`), so every player's tee shot has this wind.
    """
    return predict_hole(slot.wind_seed, swings=1, anchors=slot.wind).winds[0]


def yardage_book(
    db: Database, row: SeedRow, renders: HoleRenders, data_url: str
) -> dict[str, Any]:
    """The seed's rangefinder metadata, rendering whatever its holes still lack.

    `data_url` is where the rangefinder directory is served.
    """

    def url(path: str) -> str:
        # A render's path holds its hole's content hash, so with the renderer's version
        # the URL names its bytes, and the static mount serves it as immutable.
        return f"{data_url}/{path}?{VERSION_PARAM}={RENDER_VERSION}"

    course = row.manifest.course
    holes = []
    for hole, slot in zip(load_seed_holes(db, row.id), course.holes, strict=True):
        data = load_hole_data(db, hole.data_hash)
        render = renders.ensure(hole.data_hash, hole.pin_index, data)
        direction, speed = tee_wind(slot)
        holes.append(
            {
                "number": hole.position,
                "par": hole.par,
                "distance": data["distance"],
                "image": url(render.main),
                "width": render.width,
                "height": render.height,
                "green_image": url(render.green),
                # one pin, so the green shows it and has no other to change to
                "flag_images": [url(render.flag)],
                "wind": {
                    "direction": direction,
                    "compass": compass(direction),
                    "speed": speed,
                },
            }
        )
    return {"courses": {COURSE: {"name": " ".join(course.magic_words), "holes": holes}}}
