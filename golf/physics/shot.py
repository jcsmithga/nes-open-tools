"""
A whole shot, frame by frame: one pass of the swing loop (`SwingSequenceEntry`,
$AA2A) per frame, from the swing until `ShotPhaseState` reaches 2.
"""

import math
from dataclasses import dataclass, field

from golf.formats.hole_data import HoleData
from golf.physics.arith import MASK8, MASK24, byte, halve, is_negative
from golf.physics.cup import OUT_OF_REACH, Cup
from golf.physics.distance import distance_between_points
from golf.physics.flight import airborne, drag, fall, move
from golf.physics.landing import contact, stop
from golf.physics.launch import hi_lo_offset, launch
from golf.physics.perspective import PerspectiveScene, collide, project
from golf.physics.state import PUTTER, Ball, Ground, Lie, ShotInput, Terrain
from golf.physics.tables import PhysicsTables

#: BallX at or beyond this is out of bounds, checked every frame ($AFE0).
PLAYFIELD_WIDTH = 0xB0

#: One pixel is two yards (`LD_A246` doubles the pixel distance for the readout).
YARDS_PER_PIXEL = 2

#: No vanilla shot runs past about 700 frames; this catches a model that never stops.
MAX_FRAMES = 5000


@dataclass(frozen=True)
class Point:
    x: float
    y: float
    height: float = 0.0


@dataclass
class ShotResult:
    shot: ShotInput
    ball: Ball
    """The ball's registers once it has stopped."""
    landing: Point
    """Where it first touched the ground (the start, for a putt)."""
    landing_frame: int
    apex: float
    """Greatest sprite lift, in pixels."""
    path: list[Point] = field(default_factory=list)
    """One point per frame, when asked for."""

    @property
    def start(self) -> Point:
        return Point(self.shot.start_x / 0x10000, self.shot.start_y / 0x10000)

    @property
    def holed(self) -> bool:
        return bool(self.ball.holed)

    @property
    def rest(self) -> Point:
        return Point(self.ball.x_px, self.ball.y_px)

    @property
    def carry_yards(self) -> float:
        return _yards(self.start, self.landing)

    @property
    def total_yards(self) -> float:
        return _yards(self.start, self.rest)

    @property
    def roll_yards(self) -> float:
        return _yards(self.landing, self.rest)

    @property
    def offline_yards(self) -> float:
        """How far right (+) or left (-) of the aim line the ball finished."""
        dx = self.rest.x - self.start.x
        dy = self.rest.y - self.start.y
        angle = self.shot.aim * 2 * math.pi / 256
        return YARDS_PER_PIXEL * (dx * math.cos(angle) + dy * math.sin(angle))


def _yards(a: Point, b: Point) -> float:
    return YARDS_PER_PIXEL * math.hypot(b.x - a.x, b.y - a.y)


#: Tree hits at or above this stop the ball dead; below, it bounces back ($B03B).
DEAD_STOP_HIT = 0xCC
#: A sprite lift of this many pixels or more clears the overhead tree probe ($AFFD).
TREE_PROBE_HEIGHT = 0x0E
#: The readout distance at which the behind-the-golfer view ends ($AA50).
SCENE_DISTANCE = 0x3D

VIEW_OVERHEAD = 0x00
VIEW_GREEN = 0x40
VIEW_BEHIND = 0x80
#: The close-up of the cup (bank 9 $8000). The ball moves at half speed there
#: (a quarter on a putt), and the rest of the physics runs every 2nd frame
#: (every 4th on a putt, for a ball on the ground).
VIEW_CUP = 0xC0


@dataclass(frozen=True)
class Flag:
    """The pin, as `InitHole` picks it: `FlagX` $A7/$A8 and `FlagY` $A9/$AA."""

    x: int
    """16 bits: pixel and fraction."""
    y: int
    """16 bits: pixel (the green is always in the top 256 rows) and fraction."""

    @classmethod
    def for_pin(cls, hole: HoleData, pin: int) -> "Flag":
        """
        $DB1C-$DB5B: flag position `pin` (0-3) of `hole`. Each offset is in
        eighths of a pixel from the green's corner, stored as the pixel and a
        fraction of `(offset & 7) << 5`, plus $28, which can carry.
        """
        position = hole.metadata["flag_positions"][pin]

        def place(green: int, offset: int) -> int:
            return ((green + (offset >> 3)) << 8) + ((offset & 7) << 5) + 0x28

        return cls(
            place(hole.green_x, position["x_offset"]) & 0xFFFF,
            place(hole.green_y, position["y_offset"]) & 0xFFFF,
        )


class ShotInFlight:
    """
    A shot being played, one `step` per pass of the swing loop: the physics
    (`CalcLaunchVector`), then the behind-the-golfer scene (`LD_BB6D`), then the
    main loop's view switch ($AA37-$AA9B), which runs the cup near the pin.
    """

    def __init__(
        self,
        shot: ShotInput,
        ground: Ground,
        tables: PhysicsTables,
        launch_terrain: Terrain | None = None,
        view: int | None = None,
        scene: PerspectiveScene | None = None,
        flag: Flag | None = None,
    ):
        if launch_terrain is None:
            launch_terrain = ground.probe(Ball(x=shot.start_x, y=shot.start_y))
        self.shot = shot
        self.ground = ground
        self.tables = tables
        self.scene = scene
        self.cup = (
            None
            if flag is None
            else Cup(flag.x, flag.y, shot.aim, launch_terrain.lie == Lie.GREEN, tables)
        )
        self.origin_x = shot.start_x >> 8
        self.origin_y = shot.start_y >> 8
        self.scene_aim = shot.aim if shot.scene_aim is None else shot.scene_aim
        self.ball = launch(shot, launch_terrain, tables)
        # `ShotInitialization` zeroes it, and the launch counts one.
        self.ball.frame_counter = 1
        self._save_drop()
        if view is None:
            view = VIEW_GREEN if launch_terrain.lie == Lie.GREEN else VIEW_BEHIND
        self.ball.view = view
        self.hi_lo = hi_lo_offset(shot, tables)
        self.landing: Point | None = None
        self.landing_frame = 0
        #: The last screen y the scene projection wrote ($C7/$C8).
        self.screen_y = 0
        self._bunker_snapshot = (self.ball.x, self.ball.y)
        self._switch_view()

    def step(self) -> None:
        """One pass of the swing loop."""
        ball = self.ball
        ball.frames += 1
        if ball.frames > MAX_FRAMES:
            raise RuntimeError(f"shot still moving after {MAX_FRAMES} frames")
        self._physics()
        if ball.view == VIEW_BEHIND:
            self._scene()
        # Runs on the frame the ball stops too: the loop only skips it for a
        # negative ShotPhaseState.
        self._switch_view()

    # --- CalcLaunchVector ------------------------------------------------

    def _physics(self) -> None:
        ball = self.ball
        ball.frame_counter = (ball.frame_counter + 1) & MASK8
        # $AF3A: a lip-out plays in slow motion.
        if ball.cup_slow_motion & 0x80 and ball.frame_counter & 3:
            return
        cup_view = ball.view == VIEW_CUP
        putting = self.cup is not None and self.cup.putting
        move(ball, halvings=(1 + putting) * cup_view)
        self._flagstick()
        if cup_view:
            # $AF75: only the move runs on the in-between frames.
            every = 4 if putting and not byte(ball.height, 2) else 2
            if ball.frame_counter & (every - 1):
                return
        self._update_readout()
        landed = fall(ball)
        drag(ball)

        if ball.pixel_x >= PLAYFIELD_WIDTH:
            ball.lie = Lie.OUT_OF_BOUNDS
            stop(ball)
            return

        self._probe()
        self._save_drop()
        self._tree_hit()
        if self._bunker_lip():
            return

        ball.previous_lie = ball.lie
        if ball.lie == Lie.BUNKER:
            self._bunker_snapshot = (ball.x, ball.y)
            ball.bunker_frames = (ball.bunker_frames + 1) & 0xFF
        else:
            ball.bunker_frames = 0

        if landed:
            if self.landing is None:
                self.landing = Point(ball.x_px, ball.y_px)
                self.landing_frame = ball.frames
            contact(ball, self.shot, self.tables, self.hi_lo)
        else:
            ball.landing_processed = 0
            airborne(ball, self.tables, self.shot.wind_direction, self.shot.wind_speed)

    def _flagstick(self) -> None:
        """$AF49: a ball that touched the pin comes back off it at half speed."""
        ball = self.ball
        if not ball.flagstick & 0x80:
            return
        ball.flagstick = 1
        ball.vx = halve(-ball.vx & MASK24)
        ball.vy = halve(-ball.vy & MASK24)

    def _update_readout(self) -> None:
        """`LD_A246`: the distance readout, when the overhead or scene view shows it."""
        ball = self.ball
        if (
            ball.view & VIEW_GREEN
            or ball.pixel_y & 0x8000
            or ball.pixel_x >= PLAYFIELD_WIDTH
        ):
            return
        ball.shot_distance = distance_between_points(
            ball.pixel_x, ball.pixel_y, self.origin_x >> 8, self.origin_y >> 8
        )

    def _probe(self) -> None:
        """
        $AFED-$B026: in the overhead view, a low ball first probes the spot its
        sprite covers, that many pixels up the screen, for a tree trunk. A trunk
        there and a leafy edge under the ball count a hit.
        """
        ball = self.ball
        lift = (byte(ball.height, 3) << 1 | byte(ball.height, 2) >> 7) & 0xFF
        if ball.view & (VIEW_BEHIND | VIEW_GREEN) or lift >= TREE_PROBE_HEIGHT:
            ball.observe(self.ground.probe(ball))
            return
        # The fractions are left over from the last probe; nothing reads them.
        above = self.ground.classify(ball.pixel_x, 0, (ball.pixel_y - lift) & 0xFFFF, 0)
        ball.observe(above)
        under = self.ground.probe(ball)
        ball.observe(under)
        if above.tree_trunk and under.tree_edge:
            ball.tree_hit = (ball.tree_hit - 1) & 0xFF

    def _save_drop(self) -> None:
        """`LD_B0FF`: remember a dry spot, where a ball in water is dropped."""
        ball = self.ball
        if ball.lie not in (Lie.WATER, Lie.OUT_OF_BOUNDS):
            ball.drop_x, ball.drop_y = ball.x, ball.y

    def _tree_hit(self) -> None:
        """$B02C-$B072: a hit stops the ball dead or sends it back at a quarter speed."""
        ball = self.ball
        if not ball.tree_hit:
            return
        if not ball.tree_hit_seen:
            ball.tree_hit_seen = 1
            if ball.tree_hit >= DEAD_STOP_HIT:
                ball.vx = ball.vy = 0
                ball.backspin_x = ball.backspin_y = 0
                return
            ball.vx = _bounce_back(ball.vx)
        # Every later frame keeps reversing Y, for as long as the hit is set.
        ball.vy = _bounce_back(ball.vy)

    def _bunker_lip(self) -> bool:
        """
        $B073-$B0D0: a shot from sand that leaves it while still low can be put
        back where it last was in the sand, and stopped there. Always for clubs
        0-5; for 6 and up only when swung at under half power ($D6 >= $18) and
        the ball sits low in the behind-the-golfer view ($C7 >= $A8).
        """
        ball = self.ball
        if not (
            ball.previous_lie == Lie.BUNKER
            and ball.lie != Lie.BUNKER
            and ball.bunker_frames >= 3
            and not byte(ball.height, 3)
            and ball.bunker_exit_armed
        ):
            return False
        ball.bunker_exit_armed = 0
        club = self.shot.club
        weak = self.shot.power_stop >= 0x18
        low = not self.screen_y >> 8 and self.screen_y & 0xFF >= 0xA8
        if not (club == PUTTER or club + 1 < 7 or (weak and low)):
            return False
        ball.x, ball.y = self._bunker_snapshot
        ball.observe(self.ground.probe(ball))
        self._update_readout()
        ball.height = 0
        ball.lie = Lie.BUNKER
        stop(ball)
        return True

    # --- LD_BB6D -----------------------------------------------------------

    def _scene(self) -> None:
        ball = self.ball
        projection = project(
            ball, self.origin_x, self.origin_y, self.scene_aim, self.tables
        )
        ball.scene_depth = projection.depth
        if not projection.depth:
            return
        self.screen_y = projection.screen_y
        if self.scene is not None:
            ball.tree_hit = collide(projection, self.scene, ball.tree_hit)

    # --- the main loop's view switch --------------------------------------

    def _switch_view(self) -> None:
        """$AA37-$AA9B."""
        ball = self.ball
        if not ball.landing_processed:
            if ball.view == VIEW_CUP:
                self._at_cup()
                return
            if ball.view == VIEW_BEHIND:
                if ball.frames <= self.shot.frames_to_impact:
                    return
                in_scene = (
                    ball.scene_depth and ball.shot_distance & 0xFF < SCENE_DISTANCE
                )
                if in_scene and not is_negative(ball.vz, 32):
                    return
            elif not is_negative(ball.vz, 32):
                return
        on_green = ball.in_green_box and ball.lie not in (Lie.BUNKER, Lie.WATER)
        if on_green and (ball.lie == Lie.GREEN or ball.view & VIEW_GREEN):
            if ball.view & VIEW_GREEN:
                self._at_cup()
            else:
                ball.view = VIEW_GREEN
        elif ball.view & (VIEW_BEHIND | VIEW_GREEN):
            ball.view = VIEW_OVERHEAD
            # The overhead redraw (`LD_9AEF` -> `LD_A170`) swaps the readout
            # over to the distance to the pin and zeroes the flight distance.
            if not ball.pixel_y & 0x8000 and ball.pixel_x < PLAYFIELD_WIDTH:
                ball.shot_distance = 0

    def _at_cup(self) -> None:
        """
        $AA87 and `LD_A884`: the cup routine, then in or out of the cup view.
        Leaving it, the ball loses half its speed (unless it is hopping out
        of a lip-out, which already cost it that) and half its height, and the
        green view is redrawn.
        """
        ball = self.ball
        if self.cup is None:
            return
        self.cup.update(ball, ball.view == VIEW_CUP)
        if ball.cup_y != OUT_OF_REACH:
            if ball.view != VIEW_CUP:
                # Bank 9 $8000 opens the cup view.
                ball.view = VIEW_CUP
                ball.cup_entry_y = 0xFF
        elif ball.view == VIEW_CUP:
            if not ball.cup_slow_motion & 0x80:
                self.cup.halve_speed(ball)
            ball.cup_slow_motion = 0
            if not is_negative(ball.height, 32):
                ball.height >>= 1
            # Bank 9 $803A, then `DrawGreenDetailView`.
            ball.view = VIEW_GREEN


def _bounce_back(v: int) -> int:
    """$B055: quarter speed and reversed, whichever way it was going."""
    if not is_negative(v, 24):
        v >>= 2
    v = -v & 0xFFFFFF
    if not is_negative(v, 24):
        v >>= 2
    return v


def simulate(
    shot: ShotInput,
    ground: Ground,
    tables: PhysicsTables,
    launch_terrain: Terrain | None = None,
    record_path: bool = False,
    flag: Flag | None = None,
) -> ShotResult:
    """
    Play one shot to rest, or into the cup.

    `launch_terrain` overrides what the ground reports at the starting spot,
    so a uniform fairway can still be driven from a tee. Without a `flag`
    there is no cup: the ball rolls through the green view untouched.
    """
    flight = ShotInFlight(shot, ground, tables, launch_terrain, flag=flag)
    ball = flight.ball
    apex = 0.0
    path: list[Point] = []
    while not ball.stopped:
        flight.step()
        apex = max(apex, ball.height_px)
        if record_path:
            path.append(Point(ball.x_px, ball.y_px, ball.height_px))

    landing = flight.landing or Point(ball.x_px, ball.y_px)
    landing_frame = flight.landing_frame or ball.frames
    return ShotResult(shot, ball, landing, landing_frame, apex, path)
