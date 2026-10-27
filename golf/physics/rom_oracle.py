"""
The ROM's own shot loop, run under py65, as the reference for the Python model.

Banks 13 and 15 are mapped where the game has them and `CalcLaunchVector`
($AD0A) is called once per frame, exactly as `SwingSequenceEntry` does. A few
routines are intercepted instead of run:

- `ProbeBallPosition` and the tree probe ask the same `Ground` the model uses,
  so the two see identical terrain and any difference is in the physics.
- The distance readout, `SavePlayerState`, the perfect-shot jingle, and the
  far call and redraw behind a plugged bunker ball do nothing to the physics.

Everything the loop reads that the swing panels would have set up is poked
into RAM from the `ShotInput`. `ViewMode` ($98, formerly `MaybeResumeFlag`) is
mode, is left at 0: the overhead view, where tree hits come from the probe. The
tree probe itself is stubbed to report no tree, so shots on holes with trees are
not covered yet (`docs/shot_physics.md`, Trees).

`RomTerrainProbe` does the same for `ClassifyProbePosition` alone, with a hole
loaded into RAM the way the game's loader leaves it, and `RomAimAtFlag` for the
aim at the flag every shot starts with.
"""

from collections.abc import Callable

from py65.devices.mpu6502 import MPU

from golf.core.packing import pack_attributes
from golf.core.rom_reader import RomReader
from golf.formats.hole_data import HoleData
from golf.physics.nes import NesMachine
from golf.physics.state import Ball, Ground, Lie, ShotInput, Slope, Terrain
from golf.physics.tables import PHYSICS_BANK
from golf.physics.terrain import (
    GREEN_BUFFER_OPERANDS,
    TERRAIN_BUFFER_OPERANDS,
    TERRAIN_COLUMNS,
)

CALC_LAUNCH_VECTOR = 0xAD0A
#: Return address pushed for each frame's call; reaching it ends the frame.
RETURN_SENTINEL = 0x0100
MAX_STEPS_PER_FRAME = 200_000

PROBE_BALL_POSITION = 0xEDBC
PROBE_WITH_CLAMPED_X = 0xEDD4
EXECUTE_FAR_CALL = 0xD372
DRAW_COURSE_VIEW = 0x8DA6
SILENT = (
    0xBD89,  # SavePlayerState
    0xA5B1,  # perfect-drive jingle
    0xD823,  # fade, likewise
)

# RAM the loop reads.
BALL_X = 0xAC  # $AC-$AE
BALL_Y = 0xAF  # $AF-$B2
FLAG = 0xA7  # FlagX $A7/$A8, FlagY $A9/$AA
HEIGHT = 0xB3  # $B3-$B6
SHOT_ORIGIN = 0xB8  # $B8/$B9 x, $BA-$BC y
AIMING = 0xB7
BALL_LIE = 0xC9
GREEN_FLAGS = 0xCA
ROUGH_DEPTH = 0xCB
BUNKER_DEPTH = 0xCC
CLUB = 0xCD
SWING_SPEED = 0xCE
SHOT_PHASE = 0xD2
PENALTY = 0xD3
POWER_STOP = 0xD6
ACCURACY_STOP = 0xD7
SWING_HI_LO = 0xD8
VELOCITY_X = 0xDA
VELOCITY_Y = 0xDD
VERTICAL_VELOCITY = 0xE0
BACKSPIN = 0xE4  # $E4-$E7: X, X sign, Y, Y sign
WIND = 0xEA  # $EA-$EF
RNG_STATE = 0x42
WIND_DIRECTION = 0x96
WIND_SPEED = 0x97
SPIN_SETTING = 0x012E
CURVE_DIRECTION = 0x058D
CURVE = 0x058E
LAUNCH_DEPTH = 0x05AF
WATER_SKIP_STATE = 0x05AE
BOUNCE_STATE = 0x05B0
LAUNCH_LIE = 0x05B4
WIND_DELAY = 0x05B5
LEFT_BUNKER_ARMED = 0x05A4
TREE_HIT = 0x0597
VIEW_MODE = 0x98
TREE_HIT_VALUE = 0x0599
TREE_HIT_SEEN = 0x0596
LANDING_PROCESSED = 0x05B1
SHOT_DISTANCE = 0x057D
SCENE_DEPTH = 0xC5
PREVIOUS_LIE = 0x05B3
BUNKER_FRAMES = 0x05A3
IN_GREEN_BOX_FLAG = 0x0584
FRAME_COUNTER = 0x0585
WATER_DROP = 0x05A5  # $05A5-$05AB: BallX and BallY, with fractions
CUP_X = 0x63
CUP_Y = 0x64
CUP_BOB = 0x65  # $65/$66
CUP_BOB_SPEED = 0x67  # $67/$68
CUP_LAST = 0x0580  # $0580 x, $0581 y
CUP_ENTRY_Y = 0x0582
CUP_SLOW_MOTION = 0x0593
CUP_FRAMES = 0x0594
FLAGSTICK = 0x0595
CUP_DROP = 0x05C2
HOLED = 0x05B9


class RomShot:
    """One shot in the emulated ROM, advanced a frame at a time."""

    def __init__(
        self,
        rom: RomReader,
        shot: ShotInput,
        ground: Ground,
        launch_terrain: Terrain | None = None,
        hi_lo: int = 0,
    ):
        self.memory = bytearray(0x10000)
        self.memory[0x8000:0xC000] = rom.read_switched(0x8000, PHYSICS_BANK, 0x4000)
        self.memory[0xC000:0x10000] = rom.read_fixed(0xC000, 0x4000)
        self.cpu = MPU(memory=self.memory)
        self.ground = ground
        self.launch_terrain = launch_terrain
        self.frames = 0
        self._intercepts: dict[int, Callable[[], None]] = {
            PROBE_BALL_POSITION: self._probe,
            PROBE_WITH_CLAMPED_X: self._tree_probe,
            EXECUTE_FAR_CALL: self._skip_far_call,
        }
        for address in SILENT:
            self._intercepts[address] = self._return
        self._intercepts[DRAW_COURSE_VIEW] = self._draw_course_view
        self._set_up(shot, hi_lo)

    def _set_up(self, shot: ShotInput, hi_lo: int) -> None:
        m = self.memory
        self._write(BALL_X, shot.start_x, 3)
        self._write(BALL_Y, shot.start_y, 4)
        # ShotSetupSequence: the shot's origin is where the ball starts.
        m[SHOT_ORIGIN : SHOT_ORIGIN + 5] = bytes(
            [shot.x_fraction, shot.x, shot.y_fraction, shot.y & 0xFF, shot.y >> 8]
        )
        m[AIMING] = shot.aim
        m[CLUB] = shot.club
        m[SWING_SPEED] = shot.swing_speed
        m[POWER_STOP] = shot.power_stop
        m[ACCURACY_STOP] = shot.accuracy_stop
        m[SWING_HI_LO] = hi_lo
        m[SPIN_SETTING] = shot.spin
        m[WIND_DIRECTION] = shot.wind_direction
        m[WIND_SPEED] = shot.wind_speed
        m[BUNKER_DEPTH] = shot.bunker_depth
        self._write(RNG_STATE, shot.rng_state, 2)
        m[WATER_SKIP_STATE] = 0xFF  # ShotInitialization
        launch = self.launch_terrain or self.ground.probe(self.ball())
        if launch.lie == Lie.BUNKER:
            m[LEFT_BUNKER_ARMED] = 1  # SwingSequenceEntry, $AA27

    def _write(self, address: int, value: int, length: int) -> None:
        self.memory[address : address + length] = value.to_bytes(length, "little")

    def _read(self, address: int, length: int) -> int:
        return int.from_bytes(self.memory[address : address + length], "little")

    # --- intercepts -------------------------------------------------------

    def _probe(self) -> None:
        if self.frames == 1 and self.launch_terrain is not None:
            terrain = self.launch_terrain
        else:
            terrain = self.ground.probe(self.ball())
        self._take(terrain)
        self._return()

    def _tree_probe(self) -> None:
        """`LEDD4_ProbeWithClampedX`, classifying the spot $9C/$9E-$9F points at."""
        m = self.memory
        y = m[PROBE_Y] | m[PROBE_Y + 1] << 8
        self._take(self.ground.classify(m[PROBE_X], 0, y, 0))
        self._return()

    def _take(self, terrain: Terrain) -> None:
        """Write what `ClassifyProbePosition` would have for `terrain`."""
        ball = self.ball()
        ball.observe(terrain)
        m = self.memory
        m[BALL_LIE] = ball.lie
        m[ROUGH_DEPTH] = ball.rough_depth
        m[GREEN_FLAGS] = ball.green_flags
        self._write(WIND, ball.wind_x | ball.wind_y << 24, 6)
        m[TREE_HIT] = terrain.tree_trunk
        m[TREE_EDGE] = terrain.tree_edge
        m[IN_GREEN_BOX_FLAG] = terrain.in_green_box

    def _draw_course_view(self) -> None:
        """
        `DrawCourseGameplayView`, after a plugged bunker ball: only what it does
        to registers the model compares. `LD_9AEF` sets the overhead view and
        `LD_A170` zeroes the distance readout while the ball is on screen.
        """
        m = self.memory
        m[VIEW_MODE] = 0
        if not m[BALL_Y + 3] & 0x80 and m[BALL_X + 2] < 0xB0:
            m[SHOT_DISTANCE] = m[SHOT_DISTANCE + 1] = 0
        self._return()

    def _skip_far_call(self) -> None:
        # The three inline bytes after the JSR name the target; skip them too.
        self._return(extra=3)

    def _return(self, extra: int = 0) -> None:
        cpu = self.cpu
        lo = self.memory[0x100 + ((cpu.sp + 1) & 0xFF)]
        hi = self.memory[0x100 + ((cpu.sp + 2) & 0xFF)]
        cpu.sp = (cpu.sp + 2) & 0xFF
        cpu.pc = ((hi << 8 | lo) + 1 + extra) & 0xFFFF

    # --- running ----------------------------------------------------------

    @property
    def stopped(self) -> bool:
        return self.memory[SHOT_PHASE] >= 2

    def step_frame(self) -> None:
        """One call of `CalcLaunchVector`, as one pass of the frame loop makes."""
        self.frames += 1
        cpu = self.cpu
        cpu.sp = 0xFD
        return_to = RETURN_SENTINEL - 1
        self.memory[0x1FE] = return_to & 0xFF
        self.memory[0x1FF] = return_to >> 8
        cpu.pc = CALC_LAUNCH_VECTOR
        for _ in range(MAX_STEPS_PER_FRAME):
            if cpu.pc == RETURN_SENTINEL:
                return
            intercept = self._intercepts.get(cpu.pc)
            if intercept is not None:
                intercept()  # each one returns to its caller itself
            else:
                cpu.step()
        raise RuntimeError(f"frame {self.frames} did not return (pc ${cpu.pc:04X})")

    def ball(self) -> Ball:
        """The ROM's registers in the model's `Ball` shape."""
        ball = read_ball(self.memory)
        ball.stopped = self.stopped
        return ball


def read_ball(m: bytearray) -> Ball:
    """The physics registers in RAM, in the model's `Ball` shape."""

    def read(address: int, length: int) -> int:
        return int.from_bytes(m[address : address + length], "little")

    wind = read(WIND, 6)
    return Ball(
        x=read(BALL_X, 3),
        y=read(BALL_Y, 4),
        height=read(HEIGHT, 4),
        vx=read(VELOCITY_X, 3),
        vy=read(VELOCITY_Y, 3),
        vz=read(VERTICAL_VELOCITY, 4),
        backspin_x=m[BACKSPIN],
        backspin_x_sign=m[BACKSPIN + 1],
        backspin_y=m[BACKSPIN + 2],
        backspin_y_sign=m[BACKSPIN + 3],
        curve=m[CURVE],
        curve_direction=m[CURVE_DIRECTION],
        wind_x=wind & 0xFFFFFF,
        wind_y=wind >> 24,
        penalty=m[PENALTY],
        lie=Lie(m[BALL_LIE]),
        rough_depth=m[ROUGH_DEPTH],
        bunker_depth=m[BUNKER_DEPTH],
        green_flags=m[GREEN_FLAGS],
        launch_lie=Lie(m[LAUNCH_LIE]),
        launch_depth=m[LAUNCH_DEPTH],
        bounce_state=m[BOUNCE_STATE],
        wind_delay=m[WIND_DELAY],
        water_skip_state=m[WATER_SKIP_STATE],
        rng_state=read(RNG_STATE, 2),
        view=m[VIEW_MODE],
        tree_hit=m[TREE_HIT_VALUE],
        tree_hit_seen=m[TREE_HIT_SEEN],
        landing_processed=m[LANDING_PROCESSED],
        shot_distance=read(SHOT_DISTANCE, 2),
        scene_depth=m[SCENE_DEPTH],
        in_green_box=bool(m[IN_GREEN_BOX_FLAG]),
        previous_lie=Lie(m[PREVIOUS_LIE]),
        bunker_frames=m[BUNKER_FRAMES],
        bunker_exit_armed=m[LEFT_BUNKER_ARMED],
        drop_x=read(WATER_DROP, 3),
        drop_y=read(WATER_DROP + 3, 4),
        frame_counter=m[FRAME_COUNTER],
        cup_x=m[CUP_X],
        cup_y=m[CUP_Y],
        cup_bob=read(CUP_BOB, 2),
        cup_bob_speed=read(CUP_BOB_SPEED, 2),
        cup_last_x=m[CUP_LAST],
        cup_last_y=m[CUP_LAST + 1],
        cup_entry_y=m[CUP_ENTRY_Y],
        cup_frames=m[CUP_FRAMES],
        cup_slow_motion=m[CUP_SLOW_MOTION],
        cup_drop=m[CUP_DROP],
        flagstick=m[FLAGSTICK],
        holed=m[HOLED],
        stopped=m[SHOT_PHASE] >= 2,
    )


# --- the terrain probe ------------------------------------------------------

CLASSIFY_PROBE_POSITION = 0xEDEA
#: Where the hole goes: operands of the instructions that read or copy it, so
#: a ROM with the `wram_expansion` patch, which moves both buffers, is loaded
#: where it reads. The terrain buffer holds `DecompressTerrain`'s output, 22
#: tiles a row; the green buffer `DecompressGreen`'s, 24x24 tiles, row-major.
TERRAIN_ATTRS_OPERAND = 0xEF05
"""$EF04: LDA TerrainAttrs,Y ($0533, or $6F9C expanded)."""
TERRAIN_ATTRS_COUNT_OPERAND = 0xDB97
"""$DB96: LDY #count-1 in `LoadTerrainAndAttrs`'s copy: 72 bytes, or 90 expanded."""
GREEN_X = 0xA3
GREEN_Y = 0xA4
SCROLL_LIMIT = 0x010D
PROBE_X_FRACTION = 0x9B
PROBE_X = 0x9C
PROBE_Y_FRACTION = 0x9D
PROBE_Y = 0x9E  # $9E-$9F
TREE_EDGE = 0x0598
IN_GREEN_BOX = 0x0584


class RomTerrainProbe:
    """The ROM's `ClassifyProbePosition` over one hole, loaded as the game loads it."""

    def __init__(self, rom: RomReader, hole: HoleData):
        self.memory = bytearray(0x10000)
        self.memory[0xC000:0x10000] = rom.read_fixed(0xC000, 0x4000)
        self.cpu = MPU(memory=self.memory)
        m = self.memory

        def word(low: int, high: int) -> int:
            return m[low] | m[high] << 8

        terrain = word(*TERRAIN_BUFFER_OPERANDS)
        green = word(*GREEN_BUFFER_OPERANDS)
        attributes = word(TERRAIN_ATTRS_OPERAND, TERRAIN_ATTRS_OPERAND + 1)
        for row, tiles in enumerate(hole.terrain[: hole.terrain_height]):
            start = terrain + row * TERRAIN_COLUMNS
            m[start : start + TERRAIN_COLUMNS] = bytes(tiles)
        attrs = pack_attributes(hole.attributes)[: m[TERRAIN_ATTRS_COUNT_OPERAND] + 1]
        m[attributes : attributes + len(attrs)] = attrs
        for row, tiles in enumerate(hole.greens):
            start = green + row * 24
            m[start : start + 24] = bytes(tiles)
        m[GREEN_X] = hole.green_x
        m[GREEN_Y] = hole.green_y
        m[SCROLL_LIMIT] = hole.metadata["scroll_limit"]

    def classify(self, x: int, x_fraction: int, y: int, y_fraction: int) -> Terrain:
        m = self.memory
        m[PROBE_X], m[PROBE_X_FRACTION] = x, x_fraction
        m[PROBE_Y], m[PROBE_Y + 1], m[PROBE_Y_FRACTION] = y & 0xFF, y >> 8, y_fraction
        # Clear what only some paths write, so "not written" reads as the
        # model's defaults rather than a previous probe's leftovers.
        m[GREEN_FLAGS] = m[ROUGH_DEPTH] = 0
        m[WIND : WIND + 6] = bytes(6)
        cpu = self.cpu
        cpu.sp = 0xFD
        return_to = RETURN_SENTINEL - 1
        m[0x1FE], m[0x1FF] = return_to & 0xFF, return_to >> 8
        cpu.pc = CLASSIFY_PROBE_POSITION
        for _ in range(MAX_STEPS_PER_FRAME):
            if cpu.pc == RETURN_SENTINEL:
                break
            cpu.step()
        else:
            raise RuntimeError(f"probe at ({x}, {y}) did not return")
        wind = self._read(WIND, 6)
        return Terrain(
            Lie(m[BALL_LIE]),
            rough_depth=m[ROUGH_DEPTH],
            green_flags=m[GREEN_FLAGS],
            slope=Slope(wind & 0xFFFFFF, wind >> 24),
            tree_trunk=bool(m[TREE_HIT]),
            tree_edge=bool(m[TREE_EDGE]),
            in_green_box=bool(m[IN_GREEN_BOX]),
        )

    def _read(self, address: int, length: int) -> int:
        return int.from_bytes(self.memory[address : address + length], "little")


class RomAimAtFlag:
    """`CalcAimTowardFlag` (bank 13) alone: the aim every shot starts with, at the flag."""

    ROUTINE = 0xB9A7

    def __init__(self, rom: RomReader):
        self.machine = NesMachine(rom)

    def aim(self, ball_x: int, ball_y: int, flag_x: int, flag_y: int) -> int:
        """
        The ball's x as pixel << 8 | fraction and its y as 16 bits of pixel then
        the fraction; the flag's as `FlagX`/`FlagY`.
        """
        m = self.machine.memory
        m[BALL_X + 1], m[BALL_X + 2] = ball_x & 0xFF, ball_x >> 8
        m[BALL_Y + 1 : BALL_Y + 4] = bytes(
            [ball_y & 0xFF, ball_y >> 8 & 0xFF, ball_y >> 16]
        )
        m[FLAG : FLAG + 4] = bytes(
            [flag_x & 0xFF, flag_x >> 8, flag_y & 0xFF, flag_y >> 8]
        )
        self.machine.call(self.ROUTINE, bank=PHYSICS_BANK)
        return self.machine.cpu.a
