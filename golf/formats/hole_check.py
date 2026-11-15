"""Checking a hole: whether the editor can open it, and whether it can be played.

Pure functions over `HoleData`, shared by the editor's Check and by publishing derived
and community holes (`golf/randomizer/derive.py`, `golf/randomizer/community.py`). Each
returns what is wrong rather than raising, so a caller words the refusal its own way.

The checks a published hole has to pass run in order: `hidden_rows_trimmed` drops the
rows the editor keeps below the visible height, `hole_problems` checks every value
`CoursePatch` writes against what its table entry holds and the green against the
rules every vanilla green keeps (`green_problems`), and `compression_problem` compresses
the hole as a build would.
"""

import copy
from typing import Any

from golf.algorithms.green_zones import FRINGE_TILES, ROUGH_TILES
from golf.core.compressor import GreensCompressor, TerrainCompressor
from golf.core.packing import pack_attributes
from golf.core.palettes import GREENS_HEIGHT, GREENS_WIDTH, TERRAIN_WIDTH

from .hole_data import (
    ATTRIBUTES_WIDTH,
    HOLE_PARS,
    MAX_TERRAIN_ROWS,
    MIN_TERRAIN_ROWS,
    PINS,
    HoleData,
)

ATTRIBUTE_PALETTES = range(4)
TILES = range(0x100)
#: the editor's placeholder, which marks a cell still to fill
PLACEHOLDER = 0x100
#: the fewest rough or fringe tiles between a green's putting surface and each edge of
#: its grid: the fewest any vanilla green of either ROM has. The top and right edges
#: are the screen's, so they keep the surface out of a CRT's overscan; the left and
#: bottom ones meet the HUD. Fringe all round keeps a ball a pixel off the surface from
#: being sent to chip. Publishing refuses a green that breaks it (ADR 0032).
GREEN_SAFE_ZONE = {"top": 3, "right": 2, "bottom": 2, "left": 2}
#: the rings of putting surface tiles round a pin's tile, on every side and corner: the
#: fewest any vanilla pin has
PIN_CLEARANCE = 1


def editable_problem(hole: HoleData) -> str | None:
    """Why a parsed hole has a shape the editor can't edit, or None if it can."""
    if any(len(row) != TERRAIN_WIDTH for row in hole.terrain):
        return f"terrain rows must be {TERRAIN_WIDTH} tiles wide"
    if not 0 < hole.terrain_height <= len(hole.terrain):
        return "terrain height doesn't match its rows"
    if len(hole.attributes) < (hole.terrain_height + 1) // 2 or any(
        len(row) < ATTRIBUTES_WIDTH for row in hole.attributes
    ):
        return "attributes don't cover the terrain"
    if len(hole.greens) != GREENS_HEIGHT or any(
        len(row) != GREENS_WIDTH for row in hole.greens
    ):
        return f"greens must be {GREENS_WIDTH}x{GREENS_HEIGHT} tiles"
    return None


def hidden_rows_trimmed(hole: HoleData) -> tuple[HoleData, tuple[str, ...]]:
    """A copy of `hole` without the rows the editor hid, and a note of what went.

    The editor removes rows softly, keeping the terrain and attribute rows below the
    visible height so they can be restored. Terrain past the height never reaches a ROM,
    but every attribute row does, so the hidden ones have to go before a hole is checked
    or published.
    """
    trimmed = copy.deepcopy(hole)
    trimmed.terrain = trimmed.terrain[: hole.terrain_height]
    wanted = (hole.terrain_height + 1) // 2
    notes = []
    if len(hole.attributes) > wanted:
        notes.append(
            f"dropped {len(hole.attributes) - wanted} attribute rows below the visible "
            f"{hole.terrain_height} terrain rows"
        )
        trimmed.attributes = trimmed.attributes[:wanted]
    return trimmed, tuple(notes)


def hole_problems(hole: HoleData) -> list[str]:
    """Every value of `hole` a ROM's hole tables cannot hold; empty if there is none.

    Hidden rows count against it: check `hidden_rows_trimmed(hole)` for a hole from the
    editor.
    """
    problems = []
    metadata = hole.metadata
    height = hole.terrain_height
    if not _is_int(height, MIN_TERRAIN_ROWS, MAX_TERRAIN_ROWS) or height % 2:
        # the rest is measured against the height
        return [
            f"terrain height must be even and {MIN_TERRAIN_ROWS}-{MAX_TERRAIN_ROWS} "
            f"rows, got {height}"
        ]
    limit = metadata.get("scroll_limit")
    if not _is_int(limit, (height - 28) // 2, (height - 28) // 2):
        problems.append(
            f"scroll_limit must be {(height - 28) // 2} for {height} rows, got {limit}"
        )
    if len(hole.attributes) != (height + 1) // 2:
        problems.append(
            f"{height} terrain rows need {(height + 1) // 2} attribute rows, got "
            f"{len(hole.attributes)}"
        )
    else:
        problems += _grid_problems(
            "attributes",
            hole.attributes,
            ATTRIBUTES_WIDTH,
            (height + 1) // 2,
            ATTRIBUTE_PALETTES,
        )
    problems += _grid_problems(
        "terrain", hole.terrain[:height], TERRAIN_WIDTH, height, TILES
    )
    greens = _grid_problems("greens", hole.greens, GREENS_WIDTH, GREENS_HEIGHT, TILES)
    problems += greens
    if not _is_int(metadata.get("par"), min(HOLE_PARS), max(HOLE_PARS)):
        problems.append(
            f"par must be one of {list(HOLE_PARS)}, got {metadata.get('par')!r}"
        )
    for name, low, high in (("distance", 1, 999), ("handicap", 1, 18)):
        if not _is_int(metadata.get(name), low, high):
            problems.append(f"{name} must be {low}-{high}, got {metadata.get(name)!r}")
    if not (_is_int(hole.green_x, 0, 0xFF) and _is_int(hole.green_y, 0, 0xFF)):
        problems.append(
            f"the green's position must be bytes, got {hole.green_x!r}, {hole.green_y!r}"
        )
    tee = metadata.get("tee")
    if not (
        isinstance(tee, dict)
        and set(tee) == {"x", "y"}
        and _is_int(tee["x"], 0, TERRAIN_WIDTH * 8 - 1)
        and _is_int(tee["y"], 0, height * 8 - 1)
    ):
        problems.append(
            f"the tee must be an x and y inside the hole's {TERRAIN_WIDTH * 8} x "
            f"{height * 8} pixels, got {tee!r}"
        )
    flags = metadata.get("flag_positions")
    if not (
        isinstance(flags, list)
        and len(flags) == PINS
        and all(
            isinstance(flag, dict)
            and set(flag) == {"x_offset", "y_offset"}
            and all(_is_int(value, 0, 0xFF) for value in flag.values())
            for flag in flags
        )
    ):
        problems.append(
            f"flag_positions must be {PINS} pins with byte x_offset and y_offset"
        )
        flags = []
    if not greens:
        problems += green_problems(hole.greens, flags)
    return problems


def is_putting_surface(tile: int) -> bool:
    """Whether a greens tile is putting surface: neither the rough nor the fringe."""
    return tile not in ROUGH_TILES and tile not in FRINGE_TILES


def green_problems(greens: list[list[int]], flags: list[dict[str, int]]) -> list[str]:
    """How a 24 x 24 green and its pins break the rules every vanilla green keeps.

    The putting surface keeps `GREEN_SAFE_ZONE` from the grid's edges, and each pin
    (`x_offset`, `y_offset`: the cup's pixel in the green) is on a putting surface
    tile with `PIN_CLEARANCE` rings of putting surface round it.
    """
    surface = [
        (row, col)
        for row, tiles in enumerate(greens)
        for col, tile in enumerate(tiles)
        if is_putting_surface(tile)
    ]
    if not surface:
        return ["the green has no putting surface"]
    rows = [row for row, _ in surface]
    cols = [col for _, col in surface]
    margins = {
        "top": min(rows),
        "right": GREENS_WIDTH - 1 - max(cols),
        "bottom": GREENS_HEIGHT - 1 - max(rows),
        "left": min(cols),
    }
    problems = [
        f"the putting surface must keep {need} tiles of fringe or rough from the "
        f"green's {side} edge, got {margins[side]}"
        for side, need in GREEN_SAFE_ZONE.items()
        if margins[side] < need
    ]
    reach = PIN_CLEARANCE
    for number, flag in enumerate(flags, start=1):
        row, col = flag["y_offset"] // 8, flag["x_offset"] // 8
        clear = all(
            0 <= near_row < GREENS_HEIGHT
            and 0 <= near_col < GREENS_WIDTH
            and is_putting_surface(greens[near_row][near_col])
            for near_row in range(row - reach, row + reach + 1)
            for near_col in range(col - reach, col + reach + 1)
        )
        if not clear:
            problems.append(
                f"pin {number} (x_offset {flag['x_offset']}, y_offset "
                f"{flag['y_offset']}) must be on the putting surface with "
                f"{reach} tile(s) of it on every side"
            )
    return problems


def compression_problem(hole: HoleData) -> str | None:
    """Why `hole` does not compress as a build compresses it, or None if it does.

    Only meaningful for a hole `hole_problems` passes: a tile out of range is a
    problem there, not here.
    """
    try:
        TerrainCompressor().compress(hole.terrain[: hole.terrain_height])
        pack_attributes(hole.attributes)
        GreensCompressor().compress(hole.greens)
    except Exception as problem:
        return str(problem)
    return None


def rom_problems(hole: HoleData) -> list[str]:
    """Everything that keeps `hole`, as the editor holds it, out of a ROM: the problems
    of the hole without its hidden rows, then, if it has none, whether it compresses."""
    trimmed, _ = hidden_rows_trimmed(hole)
    problems = hole_problems(trimmed)
    if not problems:
        problem = compression_problem(trimmed)
        if problem:
            problems.append(f"the hole does not compress: {problem}")
    return problems


def _is_int(value: Any, low: int, high: int) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    )


def _grid_problems(
    name: str, grid: list, width: int, height: int, values: range
) -> list[str]:
    if len(grid) != height or any(len(row) != width for row in grid):
        return [f"{name} must be {width} cells wide and {height} rows tall"]
    bad = sorted({cell for row in grid for cell in row if cell not in values})
    if bad:
        problem = f"{name} cells must be {values.start}-{values.stop - 1}, got " + (
            ", ".join(
                f"${cell:X}" if isinstance(cell, int) else repr(cell) for cell in bad
            )
        )
        if PLACEHOLDER in bad:
            problem += f" (${PLACEHOLDER:X} is the editor's placeholder, still to fill)"
        return [problem]
    return []
