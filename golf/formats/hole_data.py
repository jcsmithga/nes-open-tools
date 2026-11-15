"""
NES Open Tournament Golf - Hole Data Model

Manages hole data including terrain, attributes, greens, and metadata.
Handles loading from and saving to JSON files.
"""

import os
from typing import Any

from ..core.palettes import GREENS_HEIGHT, GREENS_WIDTH, TERRAIN_WIDTH
from . import compact_json as json
from . import hex_utils

#: the fewest terrain rows a hole has
MIN_TERRAIN_ROWS = 30
#: the most terrain rows a hole has: the tallest Mario Open hole, and what the
#: wram_expansion patch every written course gets sizes its buffers for
MAX_TERRAIN_ROWS = 60
#: the pars a hole can have
HOLE_PARS = (3, 4, 5)
#: palette columns in an attribute row: one per 2x2 supertile of the 22-tile terrain
ATTRIBUTES_WIDTH = 11
#: the pin positions every hole has, one per round of a tournament
PINS = 4
#: deep rough, the commonest ground of the vanilla holes after fairway, and palette 1,
#: their commonest palette, which a blank hole starts as everywhere
BLANK_TERRAIN_TILE = 0xDF
BLANK_PALETTE = 1
#: the greens' rough, checkered as most vanilla greens have it
#: (`ROUGH_FAMILIES` in golf/algorithms/green_zones.py)
BLANK_GREENS_TILES = (0x29, 0x2C)


class HoleData:
    """Manages hole data including terrain, attributes, and greens."""

    def __init__(self):
        self.terrain: list[list[int]] = []
        self.terrain_height: int = 0  # Visible terrain height (may be < len(terrain))
        self.attributes: list[list[int]] = []
        self.greens: list[list[int]] = []
        self.green_x: int = 0
        self.green_y: int = 0
        self.metadata: dict[str, Any] = {}
        self.filepath: str | None = None
        self.modified: bool = False

    def load(self, path: str | os.PathLike[str]):
        """Load hole data from JSON file.

        Raises OSError, ValueError (bad JSON or hex), KeyError or TypeError for a file
        that is not a hole, leaving this hole as it was.
        """
        with open(path) as f:
            data = json.load(f)
        self.load_dict(data)
        self.filepath = os.fspath(path)

    @classmethod
    def blank(cls) -> "HoleData":
        """A new hole with nothing on it, for starting one from scratch.

        The fewest rows a hole can have, all deep rough, and a green all rough. The
        green sits at the top in the middle, the tee near the bottom in the middle, and
        the four pins spread round where the vanilla holes' pins sit on average, so each
        is on screen to move. Of `golf/formats/hole_check.py`'s checks it fails only
        the one for a green with no putting surface.
        """
        height = MIN_TERRAIN_ROWS
        hole = cls()
        hole.terrain = [[BLANK_TERRAIN_TILE] * TERRAIN_WIDTH for _ in range(height)]
        hole.terrain_height = height
        hole.attributes = [
            [BLANK_PALETTE] * ATTRIBUTES_WIDTH for _ in range((height + 1) // 2)
        ]
        hole.greens = [
            [BLANK_GREENS_TILES[(row + col) % 2] for col in range(GREENS_WIDTH)]
            for row in range(GREENS_HEIGHT)
        ]
        # the green's box is GREENS_WIDTH course pixels square
        hole.green_x = (TERRAIN_WIDTH * 8 - GREENS_WIDTH) // 2
        hole.green_y = 40
        hole.metadata = {
            "hole": 1,
            "par": 4,
            "distance": 400,
            "handicap": 1,
            "scroll_limit": (height - 28) // 2,
            "tee": {"x": TERRAIN_WIDTH * 8 // 2, "y": height * 8 - 30},
            "flag_positions": [
                {"x_offset": x, "y_offset": y}
                for x, y in ((80, 88), (104, 88), (80, 112), (104, 112))
            ],
            "_debug": {},
        }
        return hole

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HoleData":
        """A hole from the dict `to_dict` returns, or any dict with its hole fields."""
        hole = cls()
        hole.load_dict(data)
        return hole

    def load_dict(self, data: dict[str, Any]):
        """Load hole data from the dict a hole file holds.

        Everything is parsed before anything is assigned, so a dict that fails to
        parse leaves this hole unchanged.
        """
        terrain = [hex_utils.parse_hex_row(row) for row in data["terrain"]["rows"]]
        terrain_height = data["terrain"]["height"]
        attributes = data["attributes"]["rows"]
        greens = [hex_utils.parse_hex_row(row) for row in data["greens"]["rows"]]
        green_x = data["green"]["x"]
        green_y = data["green"]["y"]
        metadata = {
            "hole": data.get("hole", 1),
            "par": data.get("par", 4),
            "distance": data.get("distance", 400),
            "handicap": data.get("handicap", 1),
            "scroll_limit": data.get("scroll_limit", 32),
            "tee": data.get("tee", {"x": 0, "y": 0}),
            "flag_positions": data.get("flag_positions", []),
            "_debug": data.get("_debug", {}),
        }
        # Who made the hole, for a community hole's credit; it never reaches the ROM
        if "author" in data:
            metadata["author"] = data["author"]

        self.terrain = terrain
        self.terrain_height = terrain_height
        self.attributes = attributes
        self.greens = greens
        self.green_x = green_x
        self.green_y = green_y
        self.metadata = metadata
        self.modified = False

    def save(self, path: str | None = None):
        """Save hole data to JSON file."""
        if path is None:
            path = self.filepath
        if path is None:
            raise ValueError("No save path specified")

        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)

        self.filepath = path
        self.modified = False

    def to_dict(self) -> dict[str, Any]:
        """The hole as the JSON-ready dict `save` writes."""
        # Convert terrain to hex strings using shared utility
        terrain_rows = []
        for row in self.terrain:
            row_str = hex_utils.format_hex_row(row)
            terrain_rows.append(row_str)

        # Convert greens to hex strings using shared utility
        greens_rows = []
        for row in self.greens:
            row_str = hex_utils.format_hex_row(row)
            greens_rows.append(row_str)

        author = (
            {"author": self.metadata["author"]} if "author" in self.metadata else {}
        )
        return {
            "hole": self.metadata.get("hole", 1),
            **author,
            "par": self.metadata.get("par", 4),
            "distance": self.metadata.get("distance", 400),
            "handicap": self.metadata.get("handicap", 1),
            "scroll_limit": self.metadata.get("scroll_limit", 32),
            "green": {"x": self.green_x, "y": self.green_y},
            "tee": self.metadata.get("tee", {"x": 0, "y": 0}),
            "flag_positions": self.metadata.get("flag_positions", []),
            "terrain": {
                "width": TERRAIN_WIDTH,
                "height": self.terrain_height,
                "rows": terrain_rows,
            },
            "attributes": {
                "width": len(self.attributes[0]) if self.attributes else 11,
                "height": len(self.attributes),
                "rows": self.attributes,
            },
            "greens": {
                "width": GREENS_WIDTH,
                "height": len(self.greens),
                "rows": greens_rows,
            },
            "_debug": self.metadata.get("_debug", {}),
        }

    def get_terrain_height(self) -> int:
        return self.terrain_height

    def get_attribute(self, tile_row: int, tile_col: int) -> int:
        """Get palette index for a terrain tile position."""
        attr_row = tile_row // 2
        attr_col = tile_col // 2
        if 0 <= attr_row < len(self.attributes) and 0 <= attr_col < len(
            self.attributes[attr_row]
        ):
            return self.attributes[attr_row][attr_col]
        return 1

    def set_attribute(self, super_row: int, super_col: int, palette: int):
        """Set palette index for a supertile position."""
        if 0 <= super_row < len(self.attributes) and 0 <= super_col < len(
            self.attributes[super_row]
        ):
            self.attributes[super_row][super_col] = palette
            self.modified = True

    def set_terrain_tile(self, row: int, col: int, tile_idx: int):
        """Set a terrain tile."""
        if 0 <= row < len(self.terrain) and 0 <= col < len(self.terrain[row]):
            self.terrain[row][col] = tile_idx
            self.modified = True

    def set_greens_tile(self, row: int, col: int, tile_idx: int):
        """Set a greens tile."""
        if 0 <= row < len(self.greens) and 0 <= col < len(self.greens[row]):
            self.greens[row][col] = tile_idx
            self.modified = True

    def add_terrain_row(self, at_top: bool = False):
        """Add a row of default terrain."""
        new_row = [0xDF] * TERRAIN_WIDTH  # Default to deep rough
        if at_top:
            self.terrain.insert(0, new_row)
        else:
            self.terrain.append(new_row)

        # Add attribute row if needed
        if len(self.terrain) // 2 > len(self.attributes):
            new_attr_row = [1] * 11  # Default to fairway palette
            if at_top:
                self.attributes.insert(0, new_attr_row)
            else:
                self.attributes.append(new_attr_row)

        self.modified = True

    def remove_terrain_row(self, from_top: bool = False):
        """Remove a row of terrain."""
        if len(self.terrain) <= 1:
            return

        if from_top:
            self.terrain.pop(0)
        else:
            self.terrain.pop()

        # Remove attribute row if needed
        expected_attr_rows = (len(self.terrain) + 1) // 2
        while len(self.attributes) > expected_attr_rows:
            if from_top:
                self.attributes.pop(0)
            else:
                self.attributes.pop()

        self.modified = True
