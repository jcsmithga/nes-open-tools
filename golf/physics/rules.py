"""
What a finished shot means for the next one: where it is played from, and what
it cost. The play loop's handling of the lie the ball finished on (bank 13
$85E9-$8672), after `ShotSetupSequence` returns.
"""

from dataclasses import dataclass

from golf.physics.state import Ball, Lie, ShotInput

#: `LD_86C8` sets the lowest fraction byte of each axis to this when it puts
#: the ball back; `LD_86ED` never stored it.
RESTORED_FRACTION = 0x80


@dataclass(frozen=True)
class NextShot:
    """Where the ball is played from next, in `Ball`'s units."""

    x: int
    """BallX with fraction, 24 bits."""
    y: int
    """BallY with fraction, 32 bits."""
    bunker_depth: int
    strokes: int
    """What the shot added to the hole: 1, or 2 with a penalty."""
    holed: bool
    """The ball is in the cup, and the hole is over."""

    @property
    def pixel_x(self) -> int:
        return self.x >> 16 & 0xFF

    @property
    def pixel_y(self) -> int:
        return self.y >> 16 & 0xFFFF


def play_on(shot: ShotInput, ball: Ball) -> NextShot:
    """
    The next shot after `shot` finished as `ball`.

    - **Holed**: the hole is over.
    - **Water** (`$8636`): a stroke's penalty, and the ball is dropped where it
      was on the last frame it was over anything else (`Ball.drop_x/drop_y`).
    - **Out of bounds** (`$8667`): stroke and distance, back where `shot` was
      played from.
    - Anywhere else, where it stopped.

    Out of bounds keeps `shot`'s start fractions, and `LD_86C8` sets the low byte.
    """
    if ball.holed:
        return NextShot(ball.x, ball.y, ball.bunker_depth, 1, True)
    if ball.lie == Lie.WATER:
        return NextShot(ball.drop_x, ball.drop_y, ball.bunker_depth, 2, False)
    if ball.lie == Lie.OUT_OF_BOUNDS:
        x = shot.start_x | RESTORED_FRACTION
        y = shot.start_y | RESTORED_FRACTION
        return NextShot(x, y, shot.bunker_depth, 2, False)
    return NextShot(ball.x, ball.y, ball.bunker_depth, 1, False)
