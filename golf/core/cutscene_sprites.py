"""
Mario and Luigi outside the shot screen: the cutscene poses the object engine draws.

The swinging golfer is bank 8's (`golf/core/golfer_sprites.py`). Everywhere else
a golfer appears - walking out of the club house to start a round, reacting to
the hole just played, the other club house scenes - he is a scene object (`docs/scene_objects.md`):
a sprite id in bank 10 whose frame table names one metasprite per pose, drawn
with a CHR table the scene loads at `$0000`. Nothing is shared with the swing:
separate tiles, separate metasprites.

`SETS` lists the sprite ids that hold a golfer, with the frames that are him
and the scenes that show them. The frame lists come from the animation streams
of each scene's object records (`object_script.walk_stream`), so a frame not
listed is one no stream found so far shows. See `docs/cutscene_sprites.md`.
"""

from dataclasses import dataclass

from golf.core.graphics_codec import VideoMemory, load_graphics_table

SPRITE_BANK = 10
SPRITE_TABLE = 0x8000  # a word per sprite id: its frame table
PALETTE_SIZE = 32
SPRITE_PALETTES = 16  # the sprite half of a 32-byte palette set

FLIP_X = 0x40
FLIP_Y = 0x80


def _signed(value: int) -> int:
    return value - 0x100 if value & 0x80 else value


@dataclass(frozen=True)
class Sprite:
    dy: int
    tile: int
    attr: int
    dx: int


@dataclass(frozen=True)
class Metasprite:
    cpu_addr: int
    sprites: tuple[Sprite, ...]
    byte_length: int

    def __len__(self) -> int:
        return len(self.sprites)

    def bounds(self) -> tuple[int, int, int, int]:
        """(x0, y0, x1, y1) in pixels, the far edges exclusive."""
        if not self.sprites:
            return (0, 0, 0, 0)
        xs = [s.dx for s in self.sprites]
        ys = [s.dy for s in self.sprites]
        return (min(xs), min(ys), max(xs) + 8, max(ys) + 8)

    def peak_per_scanline(self) -> int:
        lines: dict[int, int] = {}
        for sprite in self.sprites:
            for y in range(sprite.dy, sprite.dy + 8):
                lines[y] = lines.get(y, 0) + 1
        return max(lines.values(), default=0)


def parse_metasprite(data: bytes, cpu_addr: int = 0) -> Metasprite:
    """Decode the chunked format `RenderMetasprite` (`$FEBD`) reads.

    A chunk header's low six bits count its sprites and bit 6 says another
    chunk follows. With bit 7 clear each sprite is dY, tile, attribute, dX;
    with it set one attribute byte follows the header and each sprite is dY,
    tile, dX. A header of 0 (ignoring bit 6) ends an empty metasprite.
    """
    sprites, pos = [], 0
    while True:
        header = data[pos]
        pos += 1
        if header & 0xBF == 0:
            break
        count = header & 0x3F
        if header & 0x80:
            attr = data[pos]
            pos += 1
            for _ in range(count):
                dy, tile, dx = data[pos : pos + 3]
                sprites.append(Sprite(_signed(dy), tile, attr, _signed(dx)))
                pos += 3
        else:
            for _ in range(count):
                dy, tile, attr, dx = data[pos : pos + 4]
                sprites.append(Sprite(_signed(dy), tile, attr, _signed(dx)))
                pos += 4
        if not header & 0x40:
            break
    return Metasprite(cpu_addr, tuple(sprites), pos)


@dataclass(frozen=True)
class Scene:
    """One place a set is shown: the CHR its setup code loads and its palette."""

    name: str
    setup: int  # bank 12: where the scene's golfer CHR loads start
    chr_tables: tuple[tuple[int, int], ...]  # (bank, address), in load order
    palette: int  # bank 12, a 32-byte set for Load32BytesToBuffer
    frames: tuple[int, ...]
    note: str = ""


@dataclass(frozen=True)
class SpriteSet:
    name: str
    golfer: str
    sprite_id: int
    scenes: tuple[Scene, ...]

    @property
    def frames(self) -> tuple[int, ...]:
        return tuple(sorted({f for scene in self.scenes for f in scene.frames}))


SCENE_BANK = 12

MARIO_CUTSCENE_CHR = (6, 0x8000)  # $0000-$0F7F: the hole result and club house
LUIGI_CUTSCENE_CHR = (6, 0x8AB2)  # $0000-$0B9F
WALK_OUT_CHR = (7, 0x8000)  # $0000-$0B7F: both brothers' walk-out
CLUB_HOUSE_EXTRA_CHR = (7, 0x8A45)  # $0DB0-$0FFF
WAGER_CHR = (0, 0xB2E2)  # $0A00-$0DEF


def _hole_result(chr_table) -> tuple[Scene, ...]:
    return (
        Scene(
            "hole_result",
            0xB123,
            (chr_table,),
            0xB680,
            tuple(range(0x06, 0x19)),
            "stroke play (GolfGameMode 0-2); one of five records by strokes "
            "against par ($B2F3, $B35B), "
            "ObjectRecordPtrTableCB562",
        ),
        Scene(
            "great_shot",
            0xB831,
            (chr_table,),
            0xBDB2,
            tuple(range(0x00, 0x06)),
            "a hole in one ($B7D9), and from bank 2 a long drive ($B7F0) or a shot "
            "close to the pin ($B807); frames 0-2 hold a wood for clubs 0-3, "
            "3-5 an iron for the rest",
        ),
    )


SETS = (
    SpriteSet(
        "club_house_walk_out",
        "Mario",
        0x01,
        (
            Scene(
                "club_house_walk_out",
                0xAC17,
                (WALK_OUT_CHR,),
                0xADA4,
                tuple(range(0x00, 0x07)),
                "LC_AC01_PlaceClubHouseGolfer, one player",
            ),
        ),
    ),
    SpriteSet(
        "club_house_walk_out_2p",
        "Mario and Luigi",
        0x01,
        (
            Scene(
                "club_house_walk_out",
                0xAC17,
                (WALK_OUT_CHR,),
                0xADA4,
                tuple(range(0x07, 0x0E)),
                "LC_AC01_PlaceClubHouseGolfer, two players: both in each metasprite",
            ),
        ),
    ),
    SpriteSet(
        "hole_result",
        "Mario",
        0x02,
        (
            *_hole_result(MARIO_CUTSCENE_CHR),
            Scene(
                "wager",
                0xB903,
                (MARIO_CUTSCENE_CHR, WAGER_CHR),
                0xBDEA,
                (0x19,),
                "putter in hand; frame $1A is its neighbor and no stream shows it",
            ),
        ),
    ),
    SpriteSet("hole_result_2p", "Luigi", 0x03, _hole_result(LUIGI_CUTSCENE_CHR)),
    SpriteSet(
        "club_house",
        "Mario",
        0x05,
        (
            Scene(
                "prize_money",
                0x8F85,
                (MARIO_CUTSCENE_CHR,),
                0x9125,
                (0, 1, 2, 3, *range(0x06, 0x12)),
                "docs/prize_money.md; frames $12-$31 of the sprite are the money",
            ),
            Scene(
                "club_house_a3b3",
                0xA3B3,
                (MARIO_CUTSCENE_CHR, CLUB_HOUSE_EXTRA_CHR),
                0xA469,
                tuple(range(0x00, 0x08)),
                "runs text script $B8DD or $B93C; which screen is not known",
            ),
            Scene(
                "club_house_a4cc",
                0xA4CC,
                (MARIO_CUTSCENE_CHR,),
                0xA5F7,
                (0x0E, 0x0F, 0x10, *range(0x35, 0x3B)),
                "the jumping celebration; entered at $A4A4 from bank 9 $B1A0, after a "
                "prize is looked up by placing",
            ),
        ),
    ),
)


def set_named(name: str) -> SpriteSet:
    for sprite_set in SETS:
        if sprite_set.name == name:
            return sprite_set
    raise KeyError(name)


class CutsceneSprites:
    """Reads the object engine's sprite data out of bank 10."""

    def __init__(self, rom):
        self.rom = rom
        self._bank = rom.read_switched(0x8000, SPRITE_BANK, 0x4000)

    def _word(self, cpu_addr: int) -> int:
        offset = cpu_addr - 0x8000
        return self._bank[offset] | self._bank[offset + 1] << 8

    def frame_table(self, sprite_id: int) -> int:
        return self._word(SPRITE_TABLE + 2 * sprite_id)

    def metasprite(self, sprite_id: int, frame: int) -> Metasprite:
        addr = self._word(self.frame_table(sprite_id) + 2 * frame)
        return parse_metasprite(self._bank[addr - 0x8000 :], addr)

    def frames(self, sprite_set: SpriteSet, scene: Scene | None = None):
        """(frame, metasprite) for a set, or for one of its scenes."""
        wanted = sprite_set.frames if scene is None else scene.frames
        return [(f, self.metasprite(sprite_set.sprite_id, f)) for f in wanted]

    def tiles(self, sprite_set: SpriteSet) -> set[int]:
        return {s.tile for _, meta in self.frames(sprite_set) for s in meta.sprites}

    def load_chr(self, scene: Scene) -> VideoMemory:
        vram = None
        for bank, addr in scene.chr_tables:
            _, vram = load_graphics_table(self.rom, bank, addr, vram)
        assert vram is not None
        return vram

    def sprite_palettes(self, scene: Scene) -> list[list[int]]:
        """The scene's four sprite palettes, four NES colors each."""
        data = self.rom.read_switched(scene.palette, SCENE_BANK, PALETTE_SIZE)
        half = data[PALETTE_SIZE - SPRITE_PALETTES :]
        return [list(half[i : i + 4]) for i in range(0, SPRITE_PALETTES, 4)]
