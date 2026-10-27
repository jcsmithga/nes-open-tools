"""
A shot played through the game itself, as the reference for the model on real holes.

`RomGameShot` loads a hole with the game's own `InitHole`, puts the ball where
it is told, and runs `ShotSetupSequence`, pressing buttons the way a player
would: A through the setup panels, then A to start the swing and again to stop
each meter. From the swing on, everything is the game's frame loop running as
it does on a console: the physics, the swing animation, the behind-the-golfer
scene, the view switches and the cup (see `golf.physics.nes`).

It records the ball's registers at every frame boundary from launch to rest,
and the inputs the swing actually produced, so the model can replay the shot
from the same inputs and be compared frame by frame.
"""

from dataclasses import dataclass, field

from golf.core.rom_reader import RomReader
from golf.formats.hole_data import HoleData
from golf.physics.nes import (
    BUTTON_A,
    BUTTON_DOWN,
    BUTTON_UP,
    NesMachine,
)
from golf.physics.perspective import PerspectiveScene
from golf.physics.rom_oracle import (
    ACCURACY_STOP,
    AIMING,
    BALL_X,
    BALL_Y,
    BUNKER_DEPTH,
    CLUB,
    POWER_STOP,
    RNG_STATE,
    SHOT_PHASE,
    SPIN_SETTING,
    SWING_HI_LO,
    SWING_SPEED,
    WIND_DIRECTION,
    WIND_SPEED,
    read_ball,
)
from golf.physics.shot import Flag
from golf.physics.state import Ball, ShotInput, Spin
from golf.physics.terrain import GREEN_BUFFER_OPERANDS, GREEN_SIZE

PHYSICS_BANK = 13

INIT_HOLE = 0xDA90
SHOT_SETUP_SEQUENCE = 0x877A
SWING_SEQUENCE_ENTRY = 0xAA09
BUILD_PERSPECTIVE_SCENE_CALL = 0x8829  # bank 13: the far call to bank 9 $8829
AFTER_SCENE_BUILT = 0x882F  # bank 13: just after that far call returns
# bank 13: leaving the swing loop, before a holed ball's drop animation
SHOT_COMPLETE = 0xACA1
PLAY_SHOT = 0x82AD  # bank 13: the play loop's call of ShotSetupSequence, and after
AFTER_PENALTIES = 0x8672  # bank 13: the lie dealt with, before LD_86ED stores it
HOLED_OUT = 0x832E  # bank 13: the play loop's branch for a holed ball
SWING_LOOP_TOP = 0xAA2A  # bank 13: LD_AA2A, once per pass of the swing loop

PPU_CTRL_CACHE = 0x10
GOLF_GAME_MODE = 0x0100
CURRENT_COURSE = 0x0102
HOLE_NUMBER = 0x94
CURRENT_PLAYER = 0x99
PLAYER_SWING_SPEED = 0x0123
PLAYER_PUTT_SPEED = 0x0125  # the setup panels use this one on the green
#: `LD_86ED` keeps each player's ball here after every shot: $AD/$AE, $B0-$B2 and
#: `BunkerDepth`, a byte each, two players apart. Out of bounds restores it.
PLAYER_LIE = 0x0113
HOLE_STROKES = 0x011F  # CurrentHoleStrokes, player one
PLAYER_SPIN = 0x0127
PLAYER_BAG_INDEX = 0x0129
PLAYER_ONE_BAG = 0x6027
SRAM_DEFAULTS = 0x6F99  # swing speed, putt speed, spin: $FF = unset
PLAY_LOOP_ACTIVE = 0x05BA
VIEW_MODE = 0x98
SWING_ANIMATION_FRAME = 0xCF  # SwingPowerBarPos, advanced by bank 8
SWING_IMPACT_FRAME = 0x058F
FINE_AIM_ANCHOR = 0xBD
FLAG = 0xA7  # $A7/$A8 x, $A9/$AA y
PUTTING = 0xD4  # MaybeIsPuttingFlag: the ball was on the green at setup
#: The hole's green and pin, as `LoadHoleParameters` ($DAB4) writes them.
GREEN_X = 0xA3
GREEN_Y = 0xA4
GREEN_FLAG_X = 0xA5
GREEN_FLAG_Y = 0xA6
#: `MaybePlayerHoleStatus`: 2 once a shot has come to rest on the green ($82E2).
HOLE_STATUS = 0x0111
ON_THE_GREEN = 2
#: The meter's fixed tick (`$A975`), in the sprite page from the swing's first frame.
METER_TICK_TILE = 0x3C
#: OAM y at and below which a sprite is off the bottom of the screen.
HIDDEN_Y = 0xEF

#: `CurrCourse` numbers, in the ROM's order.
COURSES = {"japan": 0, "us": 1, "uk": 2}


class WhiffError(Exception):
    """The swing never launched the ball."""


@dataclass(frozen=True)
class Swing:
    """
    When the player presses A, in frames of the swing loop. A putt has no
    accuracy meter, and its backswing starts by itself ($AAED), so its only
    press stops the power meter, `start + power` frames in.
    """

    start: int = 4
    """Ready screen frames before A starts the backswing."""
    power: int = 30
    """Frames of backswing before A stops the power meter."""
    accuracy: int = 20
    """Frames of downswing before A stops the accuracy meter."""


DEFAULT_SWING = Swing()


@dataclass
class RomShotRecord:
    shot: ShotInput
    """The shot as the game actually launched it: meter stops, RNG and all."""
    frames: list[Ball] = field(default_factory=list)
    """
    The ball after the launch frame, then after every frame to rest. A frame
    here is one pass of the swing loop, which is one step of the physics: a
    pass that waits for vblank twice is still one frame.
    """
    view_modes: list[int] = field(default_factory=list)
    """`ViewMode` at the same points."""
    scene: PerspectiveScene | None = None
    """WRAM as the behind-the-golfer scene builder left it (none for a putt)."""
    flag: Flag | None = None
    after: Ball | None = None
    """
    With `follow_through`, the ball once the play loop has dealt with where it
    finished: dropped after water, back where it was after out of bounds.
    """
    strokes: int | None = None
    """With `follow_through`, the strokes the shot added to the hole, penalties included."""


class RomGameShot:
    def __init__(
        self,
        rom: RomReader,
        course: str,
        hole: int,
        rng_state: int = 0,
        ppu: bool = False,
    ):
        """
        Load the hole with `InitHole`, which draws the pin and the wind anchors
        from `rng_state`. With `ppu` the machine keeps the PPU's memory.
        """
        self.machine = NesMachine(rom, ppu=ppu)
        m = self.machine.memory
        m[PPU_CTRL_CACHE] = 0x80  # NMI on, so frames come from vblank
        m[GOLF_GAME_MODE] = 0
        m[CURRENT_COURSE] = COURSES[course]
        m[HOLE_NUMBER] = hole - 1
        m[RNG_STATE], m[RNG_STATE + 1] = rng_state & 0xFF, rng_state >> 8
        self.machine.call(INIT_HOLE)
        self.flag = Flag(m[FLAG] | m[FLAG + 1] << 8, m[FLAG + 2] | m[FLAG + 3] << 8)
        """Where `InitHole` put the pin."""

    def play(
        self,
        shot: ShotInput,
        swing: Swing = DEFAULT_SWING,
        follow_through: bool = False,
    ) -> RomShotRecord:
        """
        Play `shot` with `swing`. With `follow_through` the game carries on
        past the shot into the play loop, which scores it and deals with water
        and out of bounds (`after`, `strokes`), from $82AD rather than
        `ShotSetupSequence`.
        """
        machine = self.machine
        m = machine.memory
        x_fraction, y_fraction = shot.x_fraction, shot.y_fraction
        m[BALL_X : BALL_X + 3] = bytes([0, x_fraction, shot.x])
        m[BALL_Y : BALL_Y + 4] = bytes([0, y_fraction, shot.y & 0xFF, shot.y >> 8])
        m[RNG_STATE], m[RNG_STATE + 1] = shot.rng_state & 0xFF, shot.rng_state >> 8
        m[WIND_DIRECTION], m[WIND_SPEED] = shot.wind_direction, shot.wind_speed
        m[CURRENT_PLAYER] = 0
        m[SRAM_DEFAULTS : SRAM_DEFAULTS + 3] = b"\xff\xff\xff"
        m[PLAYER_SWING_SPEED] = m[PLAYER_PUTT_SPEED] = shot.swing_speed
        m[PLAYER_SPIN] = shot.spin
        m[PLAYER_ONE_BAG] = shot.club
        m[PLAYER_BAG_INDEX] = 0
        m[PLAY_LOOP_ACTIVE] = 0xFF
        m[BUNKER_DEPTH] = shot.bunker_depth
        # What `LD_86ED` stored after the shot before this one.
        m[PLAYER_LIE : PLAYER_LIE + 12 : 2] = bytes(
            [x_fraction, shot.x, y_fraction, shot.y & 0xFF, shot.y >> 8]
            + [shot.bunker_depth]
        )
        strokes_before = m[HOLE_STROKES]

        record = RomShotRecord(shot)
        record.flag = self.flag
        state = {
            "swing_frame": None,
            "done": False,
            "launch": None,
            "impact": 0,
            "impact_reached": False,
            "scene_aim": None,
            "shot_over": False,
        }
        fixed_bank = bytes(m[0xC000:0x10000])
        hold = {1: BUTTON_DOWN, -1: BUTTON_UP}.get(shot.hi_lo, 0)
        presses = {
            swing.start,
            swing.start + swing.power,
            swing.start + swing.power + swing.accuracy,
        }

        def aim() -> None:
            m[AIMING] = shot.aim

        def enter_swing() -> None:
            aim()
            state["swing_frame"] = 0
            if m[PUTTING]:
                presses.clear()
                presses.add(swing.start + swing.power)

        def shot_complete() -> None:
            record.frames.append(read_ball(m))
            record.view_modes.append(m[VIEW_MODE])
            state["shot_over"] = True
            state["done"] = not follow_through

        def lie_dealt_with() -> None:
            record.after = read_ball(m)
            record.strokes = m[HOLE_STROKES] - strokes_before
            state["done"] = True

        def scene_built() -> None:
            record.scene = PerspectiveScene(
                bytes(m[0x6000:0x8000]), fixed_bank, machine.banks[PHYSICS_BANK]
            )
            state["scene_aim"] = m[FINE_AIM_ANCHOR]

        def loop_top() -> None:
            phase = m[SHOT_PHASE]
            if record.frames and phase == 1 and not state["impact_reached"]:
                # The view cannot change until the swing animation reaches impact.
                if m[SWING_ANIMATION_FRAME] < m[SWING_IMPACT_FRAME]:
                    state["impact"] += 1
                else:
                    state["impact_reached"] = True
            if phase in (1, 2) and not record.frames or phase == 1:
                record.frames.append(read_ball(m))
                record.view_modes.append(m[VIEW_MODE])
            elif phase == 0:
                # This pass launches the ball: note what the launch will use.
                state["launch"] = {
                    "rng_state": m[RNG_STATE] | m[RNG_STATE + 1] << 8,
                    "aim": m[AIMING],
                    "club": m[CLUB],
                    "swing_speed": m[SWING_SPEED],
                    "power_stop": m[POWER_STOP],
                    "accuracy_stop": m[ACCURACY_STOP],
                    "hi_lo": m[SWING_HI_LO],
                    "wind_direction": m[WIND_DIRECTION],
                    "wind_speed": m[WIND_SPEED],
                    "spin": Spin(m[SPIN_SETTING]),
                }

        def on_frame(frame: int) -> None:
            if state["swing_frame"] is None or state["shot_over"]:
                # Setup panels and the lie announcement: tap A every few frames.
                machine.buttons = BUTTON_A if frame % 6 == 0 else 0
                return
            state["swing_frame"] += 1
            # Hi/lo is read again every pass until the swing animation reaches
            # impact, after launch included, so hold it for the whole shot.
            buttons = hold
            if state["swing_frame"] in presses:
                buttons |= BUTTON_A
            machine.buttons = buttons

        machine.on_frame = on_frame
        machine.add_breakpoint(BUILD_PERSPECTIVE_SCENE_CALL, aim, PHYSICS_BANK)
        machine.add_breakpoint(SWING_SEQUENCE_ENTRY, enter_swing, PHYSICS_BANK)
        machine.add_breakpoint(SHOT_COMPLETE, shot_complete, PHYSICS_BANK)
        machine.add_breakpoint(SWING_LOOP_TOP, loop_top, PHYSICS_BANK)
        machine.add_breakpoint(AFTER_SCENE_BUILT, scene_built, PHYSICS_BANK)
        machine.add_breakpoint(AFTER_PENALTIES, lie_dealt_with, PHYSICS_BANK)
        machine.add_breakpoint(HOLED_OUT, lie_dealt_with, PHYSICS_BANK)
        machine.call(
            PLAY_SHOT if follow_through else SHOT_SETUP_SEQUENCE,
            bank=PHYSICS_BANK,
            stop=lambda: state["done"],
        )

        launch = state["launch"]
        if launch is None:
            raise WhiffError(
                "the accuracy meter ran off the end; the ball never launched"
            )
        hi_lo = launch.pop("hi_lo")
        record.shot = ShotInput(
            **launch,
            hi_lo=0 if not hi_lo else -1 if hi_lo & 0x80 else 1,
            bunker_depth=shot.bunker_depth,
            x=shot.x,
            y=shot.y,
            x_fraction=x_fraction,
            y_fraction=y_fraction,
            frames_to_impact=state["impact"],
            scene_aim=state["scene_aim"],
        )
        return record


def green_view_sprites(
    rom: RomReader, hole: HoleData, pin: int, ball: tuple[int, int], aim: int
) -> list[tuple[int, int, int, int]]:
    """
    The visible sprites, as OAM entries (y, tile, attributes, x), of the putting
    view on the first frame of the swing, with the ball at `ball` (x as pixel <<
    8 | fraction, y as 16 bits of pixel then the fraction) aimed at `aim`.

    `hole` need not be one the ROM holds: after `InitHole` has set up US hole 1,
    its green, green position and pin are written over what it left, which is
    everything the green view's sprites read. The sprites are the page at RAM
    $0200 the NMI's DMA would copy; this harness never turns rendering on.
    """
    machine = NesMachine(rom)
    m = machine.memory
    m[PPU_CTRL_CACHE] = 0x80
    m[GOLF_GAME_MODE] = 0
    m[CURRENT_COURSE] = COURSES["us"]
    m[HOLE_NUMBER] = 0
    machine.call(INIT_HOLE)

    flag = Flag.for_pin(hole, pin)
    position = hole.metadata["flag_positions"][pin]
    m[GREEN_X], m[GREEN_Y] = hole.green_x, hole.green_y
    m[GREEN_FLAG_X], m[GREEN_FLAG_Y] = position["x_offset"], position["y_offset"]
    m[FLAG : FLAG + 4] = bytes([flag.x & 0xFF, flag.x >> 8, flag.y & 0xFF, flag.y >> 8])
    low, high = (rom.read_fixed(address, 1)[0] for address in GREEN_BUFFER_OPERANDS)
    buffer = high << 8 | low
    m[buffer : buffer + GREEN_SIZE * GREEN_SIZE] = bytes(
        tile for row in hole.greens for tile in row
    )

    (x, x_fraction), (y, y_fraction) = divmod(ball[0], 0x100), divmod(ball[1], 0x100)
    m[CURRENT_PLAYER] = 0
    m[SRAM_DEFAULTS : SRAM_DEFAULTS + 3] = b"\xff\xff\xff"
    m[PLAY_LOOP_ACTIVE] = 0xFF
    m[HOLE_STATUS] = ON_THE_GREEN
    m[PLAYER_LIE : PLAYER_LIE + 12 : 2] = bytes(
        [x_fraction, x, y_fraction, y & 0xFF, y >> 8, 0]
    )
    m[BALL_X : BALL_X + 3] = bytes([0, x_fraction, x])
    m[BALL_Y : BALL_Y + 4] = bytes([0, y_fraction, y & 0xFF, y >> 8])
    m[AIMING] = aim

    def on_frame(frame: int) -> None:
        # The setup panels: tap A every few frames, as `RomGameShot` does.
        machine.buttons = BUTTON_A if frame % 6 == 0 else 0

    def swing_ready() -> bool:
        page = m[0x200:0x300]
        return any(
            page[i + 1] == METER_TICK_TILE and page[i] < HIDDEN_Y
            for i in range(0, 256, 4)
        )

    machine.on_frame = on_frame
    machine.call(SHOT_SETUP_SEQUENCE, bank=PHYSICS_BANK, stop=swing_ready)
    page = m[0x200:0x300]
    return [
        (page[i], page[i + 1], page[i + 2], page[i + 3])
        for i in range(0, 256, 4)
        if page[i] < HIDDEN_Y
    ]
