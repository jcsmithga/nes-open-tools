"""
Where the putting view puts things on the screen, and where it aims a putt.

The green view (`ViewMode` $40, `DrawGreenDetailView`) shows the green's 24x24
pixel box eight times over: one course pixel is one 8x8 tile, and the top three
bits of a coordinate's fraction pick the screen pixel inside it. These are the
bank 13 routines that place the sprites on it, and the one that aims a shot at
the pin before the player touches the pad.

- `screen_position`: `LD_9523_GreenView`, the green view's half of `CalcSpriteAnchorForView`.
- `pin_distance`: `CalcPinDistance` once the ball is on the green (`MaybePlayerHoleStatus`
  2): the distance from the ball to the cup in green-view pixels.
- `cursor_offset`: the aim cursor's distance from the ball along `Aiming`
  ($91E3-$920C, with `CalcScaledSineTerm` and `RoundScaledTerm`).
- `aim_at_flag`: `CalcAimTowardFlag`, the aim every shot starts with.
- The power meter's marker ($A8DD `RenderSwingMeterMarkers`) is at
  `meter_marker_x`.

Positions here are the anchors the game hands its metasprite renderers ($26,
$27); `SPRITES` holds the metasprites themselves, whose offsets are added to
those anchors and whose y is one row above where the PPU draws it.
"""

from dataclasses import dataclass

from golf.core.rom_reader import RomReader
from golf.physics.arith import MASK8, MASK16, mul8
from golf.physics.distance import distance_between_points
from golf.physics.tables import PhysicsTables

PHYSICS_BANK = 13

#: The green view's left edge on the screen: course pixel `GreenX` is screen x $40.
LEFT_EDGE = 0x40
#: The green's box, in course pixels.
GREEN_SIZE = 0x18

#: The cursor's distance from the ball: short when the cup is close ($91E7).
CURSOR_NEAR_RADIUS = 0x10
CURSOR_FAR_RADIUS = 0x1C
#: `PinDistance` (its low byte) at or past which the cursor goes out to `CURSOR_FAR_RADIUS`.
CURSOR_FAR_DISTANCE = 0x20

#: `RenderSwingMeterMarkers`: the marker's x is the meter's value plus this
#: ($A932), plus the green view's metasprites' own $10 ($A96B).
METER_X_OFFSET = 0x5D
#: The marker's y: $D0 plus `$05BC`, which is 0 by the time the meter is up ($A8FC).
METER_Y = 0xD0
#: The fixed tick's x ($A954).
METER_TICK_X = 0x8D

#: `ArctangentThresholdTable` (bank 13), and how much of what follows it the
#: scan could run into.
ARCTANGENT_THRESHOLDS = 0xBAA9
ARCTANGENT_READ_LENGTH = 0x40
#: `AngleQuadrantBaseTable` and `AngleQuadrantMirrorTable` (bank 13).
QUADRANT_BASE = 0xBAA1
QUADRANT_MIRROR = 0xBAA5


@dataclass(frozen=True)
class Metasprite:
    """Sprites as (dy, tile, attributes, dx) from an anchor, the game's own data."""

    address: int
    """In bank 13."""
    sprites: tuple[tuple[int, int, int, int], ...]


def _signed8(value: int) -> int:
    return value - 0x100 if value & 0x80 else value


#: The sprites a putt moves, each read from the address the game draws it from.
SPRITE_ADDRESSES = {
    # GreenViewBallMetasprites at height 0, both frames ($9251).
    "ball": 0x9488,
    # The cup in the putting view ($8F45).
    "cup": 0x8F5D,
    # The aim cursor ($920E).
    "cursor": 0x9243,
    # The power meter's marker once the swing has begun ($A970, $A917 + $0F).
    "marker": 0xA970,
    # The marker before the backswing starts ($A96B, $A906 + $0F): on screen
    # for the first frames of the swing, so in a capture of them.
    "marker_ready": 0xA96B,
}


def read_metasprites(rom: RomReader) -> dict[str, Metasprite]:
    """`SPRITE_ADDRESSES`, each a one-count metasprite read from bank 13."""
    sprites = {}
    for name, address in SPRITE_ADDRESSES.items():
        data = rom.read_switched(address, PHYSICS_BANK, 1 + 4 * 8)
        count = data[0] & 0x3F
        entries = tuple(
            (
                _signed8(data[1 + 4 * i]),
                data[2 + 4 * i],
                data[3 + 4 * i],
                _signed8(data[4 + 4 * i]),
            )
            for i in range(count)
        )
        sprites[name] = Metasprite(address, entries)
    return sprites


def place(
    metasprite: Metasprite, anchor: tuple[int, int], clipped: bool
) -> list[tuple[int, int, int, int]]:
    """
    The OAM entries (y, tile, attributes, x) a metasprite at `anchor` makes.
    `RenderGreenViewMetasprite` ($9492, `clipped`) leaves out a sprite whose
    offset takes it off the screen; `RenderMetasprite` ($FEBD) wraps it.
    """
    entries = []
    for dy, tile, attributes, dx in metasprite.sprites:
        y, x = anchor[1] + dy, anchor[0] + dx
        if clipped and not (0 <= y <= MASK8 and 0 <= x <= MASK8):
            continue
        entries.append((y & MASK8, tile, attributes, x & MASK8))
    return entries


def screen_position(
    x: int, y: int, green_x: int, green_y: int
) -> tuple[int, int] | None:
    """
    `LD_9523_GreenView`: the screen point of a course position, x and y as pixel << 8 |
    fraction (y may be 16 bits of pixel), or None outside the green's box,
    where the game hides the sprite.
    """
    dx = ((x >> 8) - green_x) & MASK8
    if dx >= GREEN_SIZE:
        return None
    dy = (y >> 8) - green_y
    if not 0 <= dy < GREEN_SIZE:
        return None
    sx = ((dx << 3 | (x & 0xFF) >> 5) + LEFT_EDGE) & MASK8
    sy = (dy << 3 | (y & 0xFF) >> 5) & MASK8
    return sx, sy


def cup_position(flag_x_offset: int, flag_y_offset: int) -> tuple[int, int]:
    """`LD_8F36`: the cup's anchor, from the pin's place in the green (`GreenFlagX/Y`)."""
    return (flag_x_offset + LEFT_EDGE) & MASK8, flag_y_offset


def pin_distance(ball: tuple[int, int], cup: tuple[int, int]) -> int:
    """
    `CalcPinDistance` with the ball on the green: `DistanceBetweenPoints` from the
    ball's screen point to the cup's, in green-view pixels. `PinDistance` keeps
    its low byte.
    """
    return distance_between_points(ball[0], ball[1], cup[0], cup[1]) & MASK8


def cursor_radius(distance: int) -> int:
    """$91E7: the cursor sits farther out when the cup is $20 or more away."""
    return CURSOR_FAR_RADIUS if distance >= CURSOR_FAR_DISTANCE else CURSOR_NEAR_RADIUS


def _component(angle: int, radius: int, tables: PhysicsTables) -> int:
    """
    `CalcScaledSineTerm` then `RoundScaledTerm`: |sin| of `angle` times `radius`, signed by the
    half turn, as a byte rounded the game's way. A positive product rounds to
    nearest; a negative one keeps its high byte only when the low byte has its
    top bit set, and otherwise goes one lower.
    """
    angle &= MASK8
    negative = bool(angle & 0x80)
    index = (-angle & 0x7F) if negative else angle
    high, low = mul8(tables.trig[index], radius)
    product = high << 8 | low
    if negative:
        product = -product & MASK16
    high, low = product >> 8, product & MASK8
    if product & 0x8000:
        return high if low & 0x80 else (high - 1) & MASK8
    return (high + 1) & MASK8 if low & 0x80 else high


def cursor_offset(aim: int, radius: int, tables: PhysicsTables) -> tuple[int, int]:
    """$91F4-$920C: the cursor's anchor from the ball's, as wrapping bytes."""
    return _component(aim, radius, tables), _component(aim - 0x40, radius, tables)


def cursor_position(
    ball: tuple[int, int], aim: int, distance: int, tables: PhysicsTables
) -> tuple[int, int]:
    """The cursor's anchor: the ball's moved along the aim, each axis wrapping at a byte."""
    dx, dy = cursor_offset(aim, cursor_radius(distance), tables)
    return (ball[0] + dx) & MASK8, (ball[1] + dy) & MASK8


def meter_marker_x(power_stop: int) -> int:
    """The marker's anchor x for a meter value ($D6): $30 is the start, 0 full power."""
    return (power_stop + METER_X_OFFSET) & MASK8


@dataclass(frozen=True)
class AimTables:
    """The tables `CalcAimTowardFlag` reads, from bank 13."""

    thresholds: bytes
    quadrant_base: bytes
    quadrant_mirror: bytes

    @classmethod
    def from_rom(cls, rom: RomReader) -> "AimTables":
        def bank(address: int, length: int) -> bytes:
            return rom.read_switched(address, PHYSICS_BANK, length)

        return cls(
            thresholds=bank(ARCTANGENT_THRESHOLDS, ARCTANGENT_READ_LENGTH),
            quadrant_base=bank(QUADRANT_BASE, 4),
            quadrant_mirror=bank(QUADRANT_MIRROR, 4),
        )


def _fits_in_a_byte(value: int) -> bool:
    return -0x80 <= value < 0x80


def aim_at_flag(
    ball_x: int, ball_y: int, flag_x: int, flag_y: int, tables: AimTables
) -> int:
    """
    `CalcAimTowardFlag`: the aim from the ball to the flag, 256 to a turn. The ball's x
    is pixel << 8 | fraction and its y 16 bits of pixel then the fraction; the
    flag's are `FlagX`/`FlagY` (16 bits each).

    The offset is halved, keeping its sign, until both axes fit in a signed
    byte. The smaller over the larger, as a fraction of 256, is then counted
    against `ArctangentThresholdTable` (starting a quarter of the way in by its
    top two bits) for an angle of up to $20 from the nearer axis.
    """
    dx = flag_x - ball_x
    dy = flag_y - ball_y
    while not (_fits_in_a_byte(dx) and _fits_in_a_byte(dy)):
        dx >>= 1
        dy >>= 1
    quadrant = (dy < 0) << 1 | (dx < 0)
    major, minor = abs(dx), abs(dy)
    from_vertical = minor >= major
    if from_vertical:
        major, minor = minor, major
    if not major:
        return 0
    if major == minor:
        angle = 0x20
    else:
        # $BA57: minor * 256 / major, a bit at a time. Both are at most $80 and
        # the remainder stays below major, so the byte-wide shift never carries.
        ratio = 0
        remainder = minor
        for _ in range(8):
            remainder <<= 1
            bit = remainder >= major
            if bit:
                remainder -= major
            ratio = ratio << 1 | bit
        index = (ratio & 0xC0) >> 4
        while ratio >= tables.thresholds[index]:
            index += 1
        angle = index if from_vertical else (0x40 - index) & MASK8
    base = tables.quadrant_base[quadrant]
    if tables.quadrant_mirror[quadrant]:
        return (base - angle) & MASK8
    return (base + angle) & MASK8
