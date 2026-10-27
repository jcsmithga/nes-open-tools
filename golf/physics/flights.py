"""
Flights shared between starts: a shot played once over uniform ground, and
reused from any start where the real ground makes no difference.

This is not a port. It is a faster way to run `golf.physics.shot`, and gives
exactly what `simulate` gives, register for register
(`tests/integration/test_flights_rom.py` checks this).

Nothing in a frame depends on where the shot started except through the ground:
every step adds the same velocities and wind wherever the ball is, and the
readout and the scene projection only look at the ball relative to the shot's
origin. The ground comes in through the probe each frame. A shot is recorded in
two parts:

1. **The air**, up to first contact, over plain fairway. The lie under a ball
   in the air changes nothing, except:

   - the overhead view's tree probe finding a trunk above a leafy edge;
   - the view switch acting over the green itself, where it goes to the green
     view (and on to the cup) instead of overhead. It acts on a falling ball,
     and on the frame the behind-the-golfer view ends;
   - the behind-the-golfer scene's trees. A `Flight` has no scene, like
     `simulate` without one.

   From the tee or the fairway nothing reads the RNG in the air, so one
   recording serves every RNG state. From the rough the launch draws on it.

2. **The roll**, from first contact to rest, over ground that is everywhere
   what the ball lands on. One is recorded for each RNG state and landing
   terrain it is asked for, since first contact reads the RNG. It holds for
   as long as the ball keeps finding exactly that terrain.

Throughout, the ball must stay on the playfield: `BallX >= $B0` stops it, and
above the top of the hole (`BallY` negative) the readout stops counting.

Where the ground makes no difference the probe still writes registers: the lie,
the rough depth, $CA, whether the ball is in the green's box, the water drop
and the sand bookkeeping. So each recording keeps the pixels the probe looked
at each frame, and a snapshot of the ball every few frames. From a real start,
`Flight.finish` walks those pixels over the real ground, working out what the
probe would have written, and stops at the first frame where the ground would
change the shot. It carries on from the snapshot before that with the real
ground, `ShotInFlight` doing the rest. A start the ground never reaches into
needs no frames played at all.

Only shots from the tee, the fairway and the rough are shared. From sand the
bunker lip rule reads the ground in the air, and a putt is played in the green
view, near the cup, from the first frame. Rolls are not shared on the green,
nor in its box, where the slope, $CA and the cup all come in.
"""

from collections import OrderedDict
from copy import copy
from dataclasses import dataclass, replace

from golf.physics.arith import MASK24, MASK32, byte, is_negative
from golf.physics.cup import Cup
from golf.physics.shot import (
    PLAYFIELD_WIDTH,
    Flag,
    Point,
    ShotInFlight,
    ShotResult,
    simulate,
)
from golf.physics.state import PUTTER, Ball, Ground, Lie, ShotInput, Terrain
from golf.physics.tables import PhysicsTables

#: Shots are recorded this far down the hole, so that none of them goes above
#: its top (`BallY` negative), whatever start they are later played from.
RECORD_Y = 0x4000

#: A snapshot of the ball is kept every this many frames. From a start where
#: the ground reaches into the shot, up to this many frames before it are
#: played again for real.
SNAPSHOT_EVERY = 8

#: The lies a `Flight` can be played from.
SHAREABLE_LIES = (Lie.FAIRWAY, Lie.TEE, Lie.ROUGH)

_FAIRWAY = Terrain(Lie.FAIRWAY)

_Probe = tuple[int, int, int | None]
"""Where a frame's probe looked: the ball's x and y, and the overhead tree probe's pixel y."""


def shareable(shot: ShotInput, launch_terrain: Terrain) -> bool:
    """Whether `shot` from ground reporting `launch_terrain` can use a `Flight`."""
    return shot.club != PUTTER and launch_terrain.lie in SHAREABLE_LIES


def draws_at_launch(launch_terrain: Terrain) -> bool:
    """Whether the launch advances the RNG: the rough's power variance does."""
    return launch_terrain.lie == Lie.ROUGH


def rolls_alike(terrain: Terrain) -> bool:
    """Whether a roll over `terrain` can be recorded once and shared."""
    return terrain.lie != Lie.GREEN and not terrain.in_green_box


class _Recorder:
    """The same terrain everywhere, remembering where each frame's probe looked."""

    def __init__(self, terrain: Terrain) -> None:
        self.terrain = terrain
        self.probes: list[_Probe] = []
        self.switching: list[bool] = []
        """Per frame: whether the view switch could act on the lie."""
        self._above: int | None = None

    def classify(self, x: int, x_fraction: int, y: int, y_fraction: int) -> Terrain:
        # Only the overhead tree probe asks for a pixel, and the probe under
        # the ball comes next.
        self._above = y
        return self.terrain

    def probe(self, ball: Ball) -> Terrain:
        self.probes.append((ball.x, ball.y, self._above))
        self._above = None
        return self.terrain


@dataclass(frozen=True)
class _Run:
    """Consecutive frames whose probes looked at the same pixels."""

    start: int
    """The first frame, counting from 0 at launch."""
    end: int
    """One past the last."""
    pixel_x: int
    pixel_y: int
    above_y: int | None
    """The overhead tree probe's pixel y, if it looked."""
    switching: bool
    """The view switch could act on the lie in one of the frames."""


def _pixels(probe: _Probe) -> tuple[int, int, int | None]:
    x, y, above_y = probe
    return byte(x, 2), y >> 16, above_y


@dataclass(frozen=True)
class _Snapshot:
    ball: Ball
    screen_y: int
    apex: float
    """The greatest sprite lift so far, for `ShotResult.apex`."""


class _Recording:
    """
    Frames of a shot played over `recorder`'s terrain, from `flight` as it is
    until first contact (`to_contact`) or until the ball stops.
    """

    def __init__(
        self,
        flight: ShotInFlight,
        recorder: _Recorder,
        apex: float,
        to_contact: bool,
    ) -> None:
        self.terrain = recorder.terrain
        self.roll = not to_contact
        """A roll must find its terrain; in the air any lie will do."""
        self.first = flight.ball.frames
        last = _Snapshot(copy(flight.ball), flight.screen_y, apex)
        self.snapshots = {self.first: last}
        while True:
            frame = flight.ball.frames
            if frame % SNAPSHOT_EVERY == 0:
                self.snapshots[frame] = last
            view = flight.ball.view
            flight.step()
            # $AA37-$AA9B: in the air, the view switch reads the lie only on a
            # falling ball, or when it ends the behind-the-golfer view, which
            # over plain fairway it always does once it acts.
            recorder.switching.append(
                is_negative(flight.ball.vz, 32) or flight.ball.view != view
            )
            # Leaving the playfield stops the ball before the probe; that frame
            # is left for the real ground.
            probed = len(recorder.probes) == frame + 1 - self.first
            if to_contact and flight.landing is not None or not probed:
                break
            apex = max(apex, flight.ball.height_px)
            last = _Snapshot(copy(flight.ball), flight.screen_y, apex)
            if flight.ball.stopped:
                break
        self.end = last.ball.frames
        """The frames recorded end here, and `snapshots` has the ball then."""
        self.snapshots[self.end] = last
        self.stopped = last.ball.stopped
        """The ball is at rest at `end`."""
        self.landing = flight.landing
        self.landing_frame = flight.landing_frame
        probes = recorder.probes
        self.contact = probes[self.end - self.first] if to_contact and probed else None
        """Where the probe looked on the frame of first contact."""
        self.positions = [(x, y) for x, y, _ in probes[: self.end - self.first]]
        self.runs: list[_Run] = []
        start = 0
        for frame in range(1, len(self.positions) + 1):
            if frame < len(self.positions) and (
                _pixels(probes[frame]) == _pixels(probes[start])
            ):
                continue
            switching = any(recorder.switching[start:frame])
            self.runs.append(
                _Run(
                    start + self.first,
                    frame + self.first,
                    *_pixels(probes[start]),
                    switching,
                )
            )
            start = frame
        # What carries on from a snapshot, apart from the ball.
        self.flight = flight

    def position(self, frame: int) -> tuple[int, int]:
        """The ball's x and y at the probe of `frame` (counting from 0)."""
        return self.positions[frame - self.first]

    def walk(
        self, dx: int, dy: int, ground: Ground, terrains: list[tuple[Terrain, ...]]
    ) -> int:
        """
        How many frames from launch the start (`dx`, `dy`) pixels from where
        this was recorded takes as they are, adding what the probe found on
        them to `terrains`, run by run. Over the air any lie will do; over a
        roll the ball must find the terrain it was recorded on.
        """
        for run in self.runs:
            pixel_x = (run.pixel_x + dx) & 0xFF
            pixel_y = (run.pixel_y + dy) & 0xFFFF
            if pixel_x >= PLAYFIELD_WIDTH or pixel_y & 0x8000:
                return run.start
            under = ground.classify(pixel_x, 0, pixel_y, 0)
            if self.roll:
                if under != self.terrain:
                    return run.start
            elif run.switching and under.lie == Lie.GREEN:
                # The view switch goes to the green view, and on to the cup.
                return run.start
            if run.above_y is None:
                terrains.append((under,))
                continue
            above = ground.classify(pixel_x, 0, (run.above_y + dy) & 0xFFFF, 0)
            if above.tree_trunk and under.tree_edge:
                return run.start
            terrains.append((above, under))
        return self.end

    def snapshot_before(self, frame: int) -> int:
        """The frame of the latest snapshot at or before `frame`."""
        if frame == self.end:
            return frame
        return max(self.first, frame - frame % SNAPSHOT_EVERY)


class Flight:
    """
    One shot, recorded over uniform ground to be finished from many starts.

    The ground it is finished over must, like `HoleGround`, classify a pixel
    the same whatever its fractions: the frames are walked a pixel at a time.
    """

    def __init__(
        self, shot: ShotInput, launch_terrain: Terrain, tables: PhysicsTables
    ) -> None:
        if not shareable(shot, launch_terrain):
            raise ValueError(f"a {launch_terrain.lie.name} shot cannot be shared")
        self.shot = shot
        self.launch_terrain = launch_terrain
        self.tables = tables
        recorder = _Recorder(_FAIRWAY)
        flight = ShotInFlight(
            replace(shot, y=RECORD_Y), recorder, tables, launch_terrain
        )
        self._start = copy(flight.ball)
        self._air = _Recording(flight, recorder, 0.0, to_contact=True)
        if not draws_at_launch(launch_terrain):
            ball = self._air.snapshots[self._air.end].ball
            assert ball.rng_state == shot.rng_state, "the RNG moved in the air"
        self._rolls: dict[tuple[int, Terrain], _Recording] = {}

    @property
    def air_frames(self) -> int:
        """Frames before first contact, or before the ball left the playfield."""
        return self._air.end

    def finish(
        self,
        x: int,
        y: int,
        rng_state: int,
        ground: Ground,
        flag: Flag | None = None,
    ) -> ShotResult:
        """
        The shot played from pixel (`x`, `y`) with `rng_state`, over `ground`:
        what `simulate` gives for `self.shot` moved there.
        """
        shot = replace(self.shot, x=x, y=y, rng_state=rng_state)
        launch_terrain = ground.probe(Ball(x=x << 16, y=y << 16))
        if launch_terrain != self.launch_terrain:
            raise ValueError("the ground at the start is not what the flight was for")
        if draws_at_launch(launch_terrain) and rng_state != self.shot.rng_state:
            raise ValueError("a shot from the rough draws on the RNG at launch")
        recording, frames, found = self._walk(shot, ground)
        if frames == recording.end and recording.stopped:
            return self._finished(shot, recording, found)
        frames = recording.snapshot_before(frames)
        return self._carry_on(shot, recording, frames, found, ground, flag)

    def shared(self, x: int, y: int, rng_state: int, ground: Ground) -> int:
        """
        How many frames of the shot a start at (`x`, `y`) takes as they were
        recorded, rather than playing them.
        """
        shot = replace(self.shot, x=x, y=y, rng_state=rng_state)
        return self._walk(shot, ground)[1]

    def _walk(
        self, shot: ShotInput, ground: Ground
    ) -> tuple[_Recording, int, list[tuple[Terrain, ...]]]:
        """
        The last recording `shot` reaches, how many frames it takes as they
        are, and what the probe found on them, run by run.
        """
        dx = shot.x - self.shot.x
        dy = shot.y - RECORD_Y
        found: list[tuple[Terrain, ...]] = []
        air = self._air
        frames = air.walk(dx, dy, ground, found)
        if frames < air.end or air.contact is None:
            return air, frames, found
        pixel_x, pixel_y, _ = _pixels(air.contact)
        pixel_x = (pixel_x + dx) & 0xFF
        pixel_y = (pixel_y + dy) & 0xFFFF
        if pixel_x >= PLAYFIELD_WIDTH or pixel_y & 0x8000:
            # The roll would break off at once: no need to record it.
            return air, frames, found
        landing = ground.classify(pixel_x, 0, pixel_y, 0)
        if not rolls_alike(landing):
            return air, frames, found
        roll = self._roll(shot.rng_state, landing)
        return roll, roll.walk(dx, dy, ground, found), found

    def _roll(self, rng_state: int, terrain: Terrain) -> _Recording:
        """The roll after first contact on `terrain`, recorded when first asked for."""
        key = (rng_state, terrain)
        roll = self._rolls.get(key)
        if roll is None:
            air = self._air
            snapshot = air.snapshots[air.end]
            flight = copy(air.flight)
            flight.ball = copy(snapshot.ball)
            if not draws_at_launch(self.launch_terrain):
                flight.ball.rng_state = rng_state
            flight.screen_y = snapshot.screen_y
            flight.landing = None
            flight.landing_frame = 0
            flight.ground = recorder = _Recorder(terrain)
            roll = _Recording(flight, recorder, snapshot.apex, to_contact=False)
            self._rolls[key] = roll
        return roll

    def _probed(
        self,
        shot: ShotInput,
        recording: _Recording,
        frames: int,
        found: list[tuple[Terrain, ...]],
        ball: Ball,
    ) -> tuple[int, int]:
        """
        Set the registers the probe writes on `ball`, as the real ground
        leaves them after `frames` frames: `Ball.observe` for each probe, then
        `ShotInFlight._physics`'s bookkeeping, a run of frames at a time. The
        next probe rewrites some of them before anything reads them, but every
        register is kept as the game would leave it. Returns where the ball
        was last over sand, for the bunker lip rule.
        """
        dx = (shot.x - self.shot.x) << 16
        dy = (shot.y - RECORD_Y) << 16
        start = self._start
        lie, rough_depth = start.lie, start.rough_depth
        in_green_box, green_flags = start.in_green_box, start.green_flags
        previous_lie, bunker_frames = start.previous_lie, start.bunker_frames
        drop = sand = (start.x, start.y)
        runs = self._air.runs
        if recording is not self._air:
            runs = runs + recording.runs
        for run, terrains in zip(runs, found, strict=False):
            if run.start >= frames:
                break
            for terrain in terrains:
                lie, in_green_box = terrain.lie, terrain.in_green_box
                if lie == Lie.ROUGH:
                    rough_depth = terrain.rough_depth
                if in_green_box or lie == Lie.GREEN:
                    green_flags = terrain.green_flags
            last_frame = min(run.end, frames) - 1
            owner = self._air if last_frame < self._air.end else recording
            last = owner.position(last_frame)
            if lie not in (Lie.WATER, Lie.OUT_OF_BOUNDS):
                drop = last
            previous_lie = lie
            if lie == Lie.BUNKER:
                sand = last
                bunker_frames = (bunker_frames + last_frame + 1 - run.start) & 0xFF
            else:
                bunker_frames = 0
        ball.lie, ball.rough_depth = lie, rough_depth
        ball.in_green_box, ball.green_flags = in_green_box, green_flags
        ball.previous_lie, ball.bunker_frames = previous_lie, bunker_frames
        ball.drop_x = (drop[0] + dx) & MASK24
        ball.drop_y = (drop[1] + dy) & MASK32
        return (sand[0] + dx) & MASK24, (sand[1] + dy) & MASK32

    def _moved(
        self,
        shot: ShotInput,
        recording: _Recording,
        frames: int,
        found: list[tuple[Terrain, ...]],
    ) -> tuple[Ball, tuple[int, int]]:
        """The ball in `recording` after `frames` frames, moved to `shot`'s start."""
        ball = copy(recording.snapshots[frames].ball)
        ball.x = (ball.x + ((shot.x - self.shot.x) << 16)) & MASK24
        ball.y = (ball.y + ((shot.y - RECORD_Y) << 16)) & MASK32
        if recording is self._air and not draws_at_launch(self.launch_terrain):
            ball.rng_state = shot.rng_state
        sand = self._probed(shot, recording, frames, found, ball)
        return ball, sand

    def _finished(
        self,
        shot: ShotInput,
        roll: _Recording,
        found: list[tuple[Terrain, ...]],
    ) -> ShotResult:
        """The shot taken whole from `roll`, moved to `shot`'s start."""
        ball, _ = self._moved(shot, roll, roll.end, found)
        assert roll.landing is not None
        landing = Point(
            roll.landing.x + shot.x - self.shot.x,
            roll.landing.y + shot.y - RECORD_Y,
        )
        apex = roll.snapshots[roll.end].apex
        return ShotResult(shot, ball, landing, roll.landing_frame, apex)

    def _carry_on(
        self,
        shot: ShotInput,
        recording: _Recording,
        frames: int,
        found: list[tuple[Terrain, ...]],
        ground: Ground,
        flag: Flag | None,
    ) -> ShotResult:
        """Play on from `recording` after `frames` frames, over the real ground."""
        ball, sand = self._moved(shot, recording, frames, found)
        snapshot = recording.snapshots[frames]
        flight = copy(recording.flight)
        flight.shot = shot
        flight.ground = ground
        flight.ball = ball
        flight.screen_y = snapshot.screen_y
        flight.origin_x = shot.x << 8
        flight.origin_y = shot.y << 8
        # Played again, the roll's first frame (first contact) lands in the same place.
        if recording is self._air:
            flight.landing, flight.landing_frame = None, 0
        else:
            assert recording.landing is not None
            flight.landing = Point(
                recording.landing.x + shot.x - self.shot.x,
                recording.landing.y + shot.y - RECORD_Y,
            )
            flight.landing_frame = recording.landing_frame
        # Only the bunker lip rule reads it, and never for a shot from off the sand.
        flight._bunker_snapshot = sand
        flight.cup = (
            None
            if flag is None
            else Cup(flag.x, flag.y, shot.aim, putting=False, tables=self.tables)
        )

        apex = snapshot.apex
        while not ball.stopped:
            flight.step()
            apex = max(apex, ball.height_px)
        landing = flight.landing or Point(ball.x_px, ball.y_px)
        return ShotResult(
            shot, ball, landing, flight.landing_frame or ball.frames, apex
        )


class Flights:
    """
    `simulate`, sharing each flight between every start that can use it. The
    flights depend on neither the hole nor the pin, so one `Flights` serves
    them all. It keeps the `max_flights` most recently used.
    """

    def __init__(self, tables: PhysicsTables, max_flights: int = 1024) -> None:
        self.tables = tables
        self.max_flights = max_flights
        self._flights: OrderedDict[tuple[ShotInput, Terrain], Flight] = OrderedDict()
        self.flown = 0
        """How many flights have been recorded."""
        self.shared = 0
        """How many shots have used a flight recorded for another."""

    def simulate(
        self, shot: ShotInput, ground: Ground, flag: Flag | None = None
    ) -> ShotResult:
        launch_terrain = ground.probe(Ball(x=shot.start_x, y=shot.start_y))
        # A flight is moved between starts a whole pixel at a time.
        if shot.x_fraction or shot.y_fraction or not shareable(shot, launch_terrain):
            return simulate(shot, ground, self.tables, flag=flag)
        rng_state = shot.rng_state if draws_at_launch(launch_terrain) else 0
        key = (replace(shot, x=0, y=0, rng_state=rng_state), launch_terrain)
        flight = self._flights.get(key)
        if flight is None:
            flight = Flight(shot, launch_terrain, self.tables)
            self.flown += 1
            self._flights[key] = flight
            if len(self._flights) > self.max_flights:
                self._flights.popitem(last=False)
        else:
            self.shared += 1
            self._flights.move_to_end(key)
        return flight.finish(shot.x, shot.y, shot.rng_state, ground, flag)
