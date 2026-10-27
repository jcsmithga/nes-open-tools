"""
The shot's inputs, the ball's state, and the ground it lands on.

`Ball` holds the game's registers at their natural width and in the game's own
units, so a frame of the model can be compared to a frame of the ROM field for
field. Positions and velocities are fixed point with 16 fraction bits: `x` is
BallX ($AE) with $AC/$AD below it, so `x >> 16` is the pixel. One pixel is two
yards, the scale the in-flight distance readout uses.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Protocol

from golf.physics.arith import MASK16, byte, signed


class Lie(IntEnum):
    """
    `BallLie` ($C9), as `ClassifyProbePosition` sets it. Fairway is 0 and the tee
    box tiles ($35-$3C) are 1; the physics treats the two alike.
    """

    FAIRWAY = 0
    TEE = 1
    ROUGH = 2
    BUNKER = 3
    WATER = 4
    OUT_OF_BOUNDS = 5
    GREEN = 6


class Spin(IntEnum):
    """`ShotSpinSetting` ($012E). TOP 1 and TOP 2 behave exactly like NORMAL."""

    TOP_2 = 0
    TOP_1 = 1
    NORMAL = 2
    BACK_1 = 3
    BACK_2 = 4


PUTTER = 0x0F

#: `SwingPowerBarPos` range: the meter starts at $30 and a stop of 0 is full power.
FULL_POWER = 0x00
#: The accuracy-meter stop that is dead center.
PERFECT_ACCURACY = 0x30


@dataclass(frozen=True)
class ShotInput:
    club: int
    """`ClubSelection`: 0-14 from the bag's club table, `PUTTER` for the putter."""
    swing_speed: int = 1
    """`SwingSpeed`: 0 slow, 1 medium, 2 fast."""
    power_stop: int = FULL_POWER
    """Where the power meter stopped ($D6): 0 is full power, $30 is none."""
    accuracy_stop: int = PERFECT_ACCURACY
    """Where the accuracy meter stopped ($D7): $30 is center, either side curves."""
    hi_lo: int = 0
    """-1 for a low shot (Up held), +1 for high (Down), 0 for neither."""
    spin: Spin = Spin.NORMAL
    aim: int = 0
    """`Aiming` ($B7), 256 steps to a turn: 0 is up the screen, $40 is right."""
    wind_direction: int = 0
    """`WindDirection` ($96), the direction the wind blows toward, as `aim`."""
    wind_speed: int = 0
    """`WindSpeed` ($97)."""
    rng_state: int = 0
    """`RngState` ($43 << 8 | $42) when the shot starts."""
    bunker_depth: int = 0
    """`BunkerDepth` ($CC) left by the landing that put the ball in the sand, 0-2."""
    x: int = 88
    """Starting BallX, in pixels. The playfield is 176 wide; x >= 176 is out of bounds."""
    y: int = 0x300
    """Starting BallY, in pixels."""
    x_fraction: int = 0
    """Starting BallX's fraction ($AD), in 256ths of a pixel."""
    y_fraction: int = 0
    """Starting BallY's fraction ($B0)."""
    frames_to_impact: int = 0
    """
    Frames after launch before the golfer's swing animation (bank 8) reaches
    impact; the view does not change until then. It depends on how the swing
    was timed: `golf.physics.meter.swing` gives it, with the meter stops.
    """
    scene_aim: int | None = None
    """`MaybeFineAimAnchor` ($BD): the aim the behind-the-golfer scene was built along. Defaults to `aim`."""

    @property
    def start_x(self) -> int:
        """The starting BallX in `Ball`'s units: pixel, fraction and a zero low byte."""
        return self.x << 16 | self.x_fraction << 8

    @property
    def start_y(self) -> int:
        return self.y << 16 | self.y_fraction << 8


@dataclass(frozen=True)
class Slope:
    """
    A green tile's slope, as `LF300` writes it: $EA-$EC for X and $ED-$EF for Y,
    each a fraction byte, a magnitude byte and a sign byte ($80 when negative).
    """

    x: int = 0
    y: int = 0

    @classmethod
    def of(cls, x_magnitude: int, x_negative: bool, y_magnitude: int, y_negative: bool):
        """A slope from magnitudes alone, for tests and made-up greens."""
        return cls(
            x_negative << 23 | x_magnitude << 8, y_negative << 23 | y_magnitude << 8
        )


FLAT = Slope()


@dataclass(frozen=True)
class Terrain:
    """What the ball's position probe reports for one spot."""

    lie: Lie
    rough_depth: int = 0
    """`RoughDepth` ($CB), 0 or 1."""
    green_flags: int = 0
    """$CA on the green: bit 6 picks the slope scale, bit 7 adds roll friction."""
    slope: Slope = FLAT
    tree_trunk: bool = False
    """$0597: the probe hit the solid (color 2) part of a tree tile."""
    tree_edge: bool = False
    """$0598: the probe hit a clear pixel of a tree tile next to a leafy one."""
    in_green_box: bool = False
    """$0584: the spot is inside the green's 24x24 box, fringe included."""


class Ground(Protocol):
    """The course under the ball: `ClassifyProbePosition` ($EDEA) for the model."""

    def classify(self, x: int, x_fraction: int, y: int, y_fraction: int) -> Terrain:
        """The terrain at a pixel (x 8 bits, y 16 bits) and its fractions."""
        ...

    def probe(self, ball: "Ball") -> Terrain:
        """`ProbeBallPosition` ($EDBC): the terrain under the ball itself."""
        ...


@dataclass(frozen=True)
class UniformGround:
    """The same terrain everywhere: a driving range, or a sea of rough."""

    terrain: Terrain

    def classify(self, x: int, x_fraction: int, y: int, y_fraction: int) -> Terrain:
        return self.terrain

    def probe(self, ball: "Ball") -> Terrain:
        return self.terrain


@dataclass
class Ball:
    """The game's physics registers for the ball in play."""

    x: int
    """BallX with fraction: $AE.$AD$AC, 24 bits."""
    y: int
    """BallY with fraction: $B2$B1.$B0$AF, 32 bits."""
    height: int = 0
    """$B6$B5$B4$B3, 32 bits. `height >> 23` is how many pixels the sprite lifts."""
    vx: int = 0
    """`VelocityDeltaX` $DA-$DC, 24-bit two's complement, pixels/frame << 16."""
    vy: int = 0
    """`VelocityDeltaY` $DD-$DF, the same."""
    vz: int = 0
    """`VerticalVelocity` $E0-$E3, 32-bit two's complement, in `height` units."""
    backspin_x: int = 0
    """`VelocityScaleX` $E4: backspin still to spend, X component."""
    backspin_x_sign: int = 0
    """`VelocityScaleXSign` $E5: 1 adds the backspin to X velocity, 0 subtracts it."""
    backspin_y: int = 0
    """`VelocityScaleY` $E6."""
    backspin_y_sign: int = 0
    """`VelocityScaleYSign` $E7."""
    curve: int = 0
    """`AimDeviationMag` $058E: hook/slice strength from the accuracy meter."""
    curve_direction: int = 0
    """`AimDeviationDir` $058D: which way it curves."""
    wind_x: int = 0
    """$EA-$EC: this frame's wind push in the air, or the slope on the green. 24 bits."""
    wind_y: int = 0
    """$ED-$EF."""
    penalty: int = 0
    """`PenaltyAccumulator` $D3; after launch, the first-bounce friction for backspin."""
    lie: Lie = Lie.FAIRWAY
    rough_depth: int = 0
    bunker_depth: int = 0
    """`BunkerDepth` ($CC): how buried a ball that finishes in sand is."""
    green_flags: int = 0
    launch_lie: Lie = Lie.FAIRWAY
    """`PreviousLieType` $05B4, despite its name only ever written at launch."""
    launch_depth: int = 0
    """`TerrainPenaltyDepth` $05AF: rough depth + 1, or bunker depth, at launch."""
    bounce_state: int = 0
    """`BounceState` $05B0: 0 in the air before first contact, 1 after, 2 stopped."""
    wind_delay: int = 10
    """`WindDelayCounter` $05B5: the wind moves the ball from the tenth air frame."""
    water_skip_state: int = 0xFF
    """`WaterSkipState` $05AE: $FF may still skip, 0 sinks on contact."""
    rng_state: int = 0
    view: int = 0
    """`ViewMode` ($98): $80 behind the golfer, $00 overhead, $40 the green."""
    tree_hit: int = 0
    """$0599: nonzero after a tree hit; $CC or more stops the ball dead."""
    tree_hit_seen: int = 0
    """$0596: set on the first frame `tree_hit` is acted on."""
    landing_processed: int = 0
    """`LandingProcessed` ($05B1): 1 on a ground-contact frame, 0 in the air, 9 for a putt."""
    shot_distance: int = 0
    """$057D/$057E: whole pixels from the shot's origin, as the readout shows it (halved)."""
    scene_depth: int = 0
    """$C5: the ball's depth in the behind-the-golfer scene, 0 out of view."""
    in_green_box: bool = False
    """$0584: the last probe was inside the green's box."""
    previous_lie: Lie = Lie.FAIRWAY
    """`InitialLieType` ($05B3), despite its name the lie on the previous frame."""
    bunker_frames: int = 0
    """$05A3: consecutive frames in sand, starting at 3 for a shot from sand."""
    bunker_exit_armed: int = 0
    """$05A4: set for a shot from sand until the lip rule has run."""
    drop_x: int = 0
    """
    $05A5-$05A7: `x` on the last frame the ball was over anything but water or
    out of bounds (`LD_B0FF`). A ball that finishes in water is played from here.
    """
    drop_y: int = 0
    """$05A8-$05AB: `y` then."""
    frame_counter: int = 0
    """$0585: counts calls of `CalcLaunchVector`; the cup view's slow frames key off it."""
    # Zero-page bytes that other code reuses as scratch outside the cup view,
    # so they are not compared; what they decide lands in the registers below.
    cup_x: int = field(default=0, compare=False)
    """$63: the ball's screen x in the cup view, $80 at the cup's center."""
    cup_y: int = field(default=0, compare=False)
    """$64: the ball's screen y in the cup view, or $F0 when out of the cup's reach."""
    cup_bob: int = field(default=0, compare=False)
    """$65/$66: how far a lipped-out ball has hopped, in the cup view's animation."""
    cup_bob_speed: int = field(default=0, compare=False)
    """$67/$68: its speed; gravity pulls it back to the lip."""
    cup_last_x: int = 0
    """$0580: `cup_x` on the last frame the ball was over the cup."""
    cup_last_y: int = 0
    """$0581: `cup_y` then."""
    cup_entry_y: int = 0
    """$0582: `cup_y` on the first frame over the cup since the cup view opened ($FF before)."""
    cup_frames: int = 0
    """$0594: consecutive frames the ball has been over the cup."""
    cup_slow_motion: int = 0
    """`MaybeCupSlowMotionFlag` $0593: $FF while a lip-out plays, when the physics runs every 4th frame."""
    cup_drop: int = 0
    """$05C2: how fast a ball that caught the rim went in, for the drop animation."""
    flagstick: int = 0
    """`MaybeFlagstickHitFlag` $0595: $FF when the ball has touched the pin, 1 once it has bounced off."""
    holed: int = 0
    """`MaybeHoleCompleteFlag` $05B9: counts the ball into the cup."""
    stopped: bool = False
    frames: int = field(default=0, compare=False)

    def observe(self, terrain: Terrain) -> None:
        """
        Take on what `ProbeBallPosition` reports for the ball's spot. Like the
        ROM, only the lie is always written: `RoughDepth` only for rough, $CA
        only inside the green's box, and the slope only on the green.
        """
        self.lie = terrain.lie
        self.in_green_box = terrain.in_green_box
        if terrain.lie == Lie.ROUGH:
            self.rough_depth = terrain.rough_depth
        if terrain.in_green_box or terrain.lie == Lie.GREEN:
            self.green_flags = terrain.green_flags
        if terrain.lie == Lie.GREEN:
            # On the green `LF300` fills $EA-$EF with the slope, over the wind.
            self.wind_x = terrain.slope.x
            self.wind_y = terrain.slope.y

    @property
    def x_px(self) -> float:
        return self.x / 0x10000

    @property
    def y_px(self) -> float:
        return signed(self.y, 32) / 0x10000

    @property
    def height_px(self) -> float:
        """Sprite lift in pixels, as `LD_AFF3` computes it for the tree probe."""
        return signed(self.height, 32) / (1 << 23)

    @property
    def pixel_x(self) -> int:
        return byte(self.x, 2)

    @property
    def pixel_y(self) -> int:
        return (self.y >> 16) & MASK16
