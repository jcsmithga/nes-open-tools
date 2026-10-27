"""
Launch: from the swing to the ball's first velocity.

The first frame of `CalcLaunchVector` (bank 13 $AD0A, `ShotPhaseState` 0). In
order:

1. **Power.** The power-meter stop picks a base from `TimingPowerCurve`, then
   the club's distance multiplier, the lie's penalty and random variance, and
   the swing speed multiplier each scale it. Every scaling is an 8x8 multiply
   keeping the high byte, so each is a fraction of 256.
2. **Launch angle.** The club's loft index, moved by hi/lo, splits the power
   into vertical speed (sin) and ground speed (cos).
3. **Direction.** Ground speed is split along the aim into X and Y velocity.
4. **Backspin.** A budget proportional to power and to the club's base loft,
   pointing opposite the aim. In the air it produces lift; on the ground it
   brakes the roll until spent.
5. **Curve.** Accuracy-meter error beyond the club's forgiveness becomes a
   hook or slice strength, applied in the air until first contact.
"""

from golf.core.rng import lfsr_step
from golf.physics.arith import MASK8, MASK24, is_negative, mul8
from golf.physics.state import (
    PERFECT_ACCURACY,
    PUTTER,
    Ball,
    Lie,
    ShotInput,
    Spin,
    Terrain,
)
from golf.physics.tables import PhysicsTables

#: Penalty for stopping the power meter this far or further from full ($ADB1).
WEAK_SWING_STOP = 0x0A
#: Backspin is sin(base loft) times this ($AEB5, built as $1C << 2 + $60), times power.
BACKSPIN_LOFT_SCALE = 0xD0
#: Clubs below this use the wood column of the rough and bunker tables.
FIRST_IRON = 4


def hi_lo_offset(shot: ShotInput, tables: PhysicsTables) -> int:
    """
    `SwingHiLo` ($D8) for a shot: the club's hi/lo step, signed.

    `UpdateSwingHiLoAndAim` ($ACC3) adds it while Down is held and subtracts it
    while Up is held, so +1 here (a higher launch) is Down on the pad.
    """
    if shot.club == PUTTER or shot.hi_lo == 0:
        return 0
    step = tables.club_hi_lo[shot.club]
    return step if shot.hi_lo > 0 else -step & MASK8


def launch(shot: ShotInput, terrain: Terrain, tables: PhysicsTables) -> Ball:
    """The ball as the first frame of the shot leaves it."""
    ball = Ball(x=shot.start_x, y=shot.start_y, rng_state=shot.rng_state)
    ball.observe(terrain)
    ball.launch_lie = terrain.lie
    ball.bunker_depth = shot.bunker_depth
    hi_lo = hi_lo_offset(shot, tables)
    aim = shot.aim
    accuracy = shot.accuracy_stop

    perfect = (
        terrain.lie < Lie.ROUGH
        and shot.club == 0
        and shot.power_stop == 0
        and accuracy == PERFECT_ACCURACY
    )
    if shot.club == PUTTER:
        accuracy = PERFECT_ACCURACY

    ball.curve, ball.curve_direction = _curve(shot.club, accuracy, tables)

    # Power: the meter, then each multiplier in turn.
    power = tables.timing_power[0x38 - shot.power_stop]
    if not perfect:
        power = (power - 4) & MASK8
    penalty = 6 if shot.power_stop >= WEAK_SWING_STOP else 0
    if shot.spin >= Spin.BACK_1:
        penalty += 3

    if shot.club == PUTTER:
        multiplier = tables.putter_distance[shot.swing_speed]
        if terrain.lie != Lie.GREEN:
            # Off the green the putter wobbles: up to 16 aim steps either way.
            ball.rng_state, draw = lfsr_step(ball.rng_state)
            wobble = draw & 0x1F
            aim = (aim + ((wobble - 0x0F) & MASK8) + (wobble >= 0x0F)) & MASK8
            multiplier = tables.putter_distance_off_green[shot.swing_speed]
    else:
        multiplier = tables.club_distance[shot.club]
    power, _ = mul8(power, multiplier)

    if terrain.lie == Lie.ROUGH:
        depth = terrain.rough_depth
        penalty += depth >> 1
        ball.launch_depth = depth + 1
        column = (
            tables.rough_penalty_wood
            if shot.club < FIRST_IRON
            else tables.rough_penalty_iron
        )
        power = _lie_variance(ball, power, column[depth], tables.rough_variance[depth])
    elif terrain.lie == Lie.BUNKER:
        depth = ball.bunker_depth
        penalty += 2
        ball.launch_depth = depth
        if shot.club < FIRST_IRON:
            scale = tables.bunker_penalty_wood[depth]
            variance = tables.bunker_variance[depth + 1]
        else:
            scale = tables.bunker_penalty_iron[depth]
            variance = tables.bunker_variance[depth]
        power = _lie_variance(ball, power, scale, variance)

    ball.penalty = tables.penalty_to_first_bounce[penalty]
    power, _ = mul8(tables.swing_speed_power[shot.swing_speed], power)

    # Launch angle: sin for climb, cos for ground speed.
    loft = (tables.club_loft[shot.club] + hi_lo) & MASK8
    climb_hi, climb_lo = mul8(tables.trig[loft], power)
    ball.vz = climb_hi << 16 | climb_lo << 8
    speed_hi, speed_lo = mul8(tables.cos_unmasked(loft), power)
    ground_speed = speed_hi << 8 | speed_lo

    ball.vx, ball.vy = _ground_velocity(aim, ground_speed, tables)

    # Backspin scales with the club's base loft, ignoring hi/lo.
    spin_scale, _ = mul8(tables.trig[tables.club_loft[shot.club]], BACKSPIN_LOFT_SCALE)
    backspin, _ = mul8(spin_scale, power)
    reverse = aim ^ 0x80
    ball.backspin_x, _ = mul8(tables.sin(reverse), backspin)
    ball.backspin_x_sign = 0 if is_negative(reverse, 8) else 1
    reverse_y = (reverse - 0x40) & MASK8
    ball.backspin_y, _ = mul8(tables.sin(reverse_y), backspin)
    ball.backspin_y_sign = 0 if is_negative(reverse_y, 8) else 1

    # $AD28-$AD3B: the sand bookkeeping the bunker lip rule reads.
    ball.previous_lie = terrain.lie
    if terrain.lie == Lie.BUNKER:
        ball.bunker_frames = 3
        ball.bunker_exit_armed = 1
    if shot.club == PUTTER:
        # A putt never "lands": every frame is a roll frame from the start.
        ball.bounce_state = ball.landing_processed = 9
    return ball


def _curve(club: int, accuracy: int, tables: PhysicsTables) -> tuple[int, int]:
    """(`AimDeviationMag`, `AimDeviationDir`) from the accuracy-meter stop."""
    if accuracy >= PERFECT_ACCURACY:
        error, direction = accuracy - PERFECT_ACCURACY, 0
    else:
        error, direction = PERFECT_ACCURACY - accuracy, 1
    error = max(min(error, 0x18) - tables.club_forgiveness[club], 0)
    return error * 8, direction


def _lie_variance(ball: Ball, power: int, scale: int, variance: int) -> int:
    """Rough or bunker: scale the power down, then add +-random(variance) of it."""
    power, _ = mul8(scale, power)
    ball.rng_state, draw = lfsr_step(ball.rng_state)
    swing, _ = mul8(draw, variance)
    swing, _ = mul8(swing, power)
    if is_negative(ball.rng_state >> 8, 8):
        swing = -swing & MASK8
    return (power + swing) & MASK8


def _ground_velocity(aim: int, speed: int, tables: PhysicsTables) -> tuple[int, int]:
    """`InitGroundVelocity` ($B3ED): split a 16-bit ground speed along the aim."""

    def component(angle: int) -> int:
        s = tables.sin(angle)
        from_lo, _ = mul8(s, speed & MASK8)
        hi, lo = mul8(s, speed >> 8)
        velocity = (hi << 8) + lo + from_lo
        return -velocity & MASK24 if is_negative(angle, 8) else velocity

    return component(aim), component((aim - 0x40) & MASK8)
