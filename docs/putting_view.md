# The Putting View

> **Note**: This document was written by Claude, from a design worked out with jdharms.

Where the putting view puts the ball, the cup, the aim cursor and the power meter's
marker, and the aim every shot starts with. `golf/physics/green_view.py` ports it, and
`tests/physics/test_green_view_rom.py` checks it against the sprites the game itself leaves
in RAM (`green_view_sprites` in `golf/physics/rom_game.py`).

## A putt

On the green a putt draws nothing from the RNG (the putter's aim wobble is off the green
only) and feels no wind (`$EA-$EF` hold the slope instead), so it is decided by:

- **The spot**, in course pixels with a fraction (`ShotInput.x_fraction`/`y_fraction`).
  `tests/physics/test_green_view_rom.py` plays putts from spots between pixels through the
  game and requires every frame to match.
- **The aim**, `Aiming` (`$B7`), 256 steps to a turn. Every shot starts aimed at the flag
  (`CalcAimTowardFlag`, below), and the setup panels turn it a step for every other frame
  Left or Right is held (`UpdateSetupAim`, `$8C77`), all the way round.
- **The swing speed**, the putt speed the setup panels show (`$0125`).
- **The power stop**, the meter's value `$D6` when A is pressed: `$30` none, 0 full. A
  putt's meter runs at half the swing's rate and its backswing starts by itself, so at
  every speed a press can stop it on every value from 0 to 48.
- **The spin** is the one the setup panels leave on the green, TOP 2; the player cannot
  change it there.

## The sprites

The green view (`ViewMode` `$40`, `DrawGreenDetailView` bank 13 `$95A9`) shows the green's
24x24 pixel box from screen x `$40`: one course pixel is one 8x8 tile, and the top three
bits of a coordinate's fraction pick the screen pixel within it (`LD_9523_GreenView`). The
sprites a putt moves, each a metasprite drawn at an anchor point:

| Sprite | Anchor | Metasprite | Drawn by |
|---|---|---|---|
| Ball at rest | the ball's screen point | `$9488`: tile `$2E`, 3 left and 4 up | `LD_9164_GreenView`, `RenderGreenViewMetasprite` |
| Cup | `GreenFlagX + $40`, `GreenFlagY` | `$8F5D`: tile `$2D`, 2 left and 3 up | `LD_8F36`, `RenderMetasprite` |
| Aim cursor | the ball's, moved along the aim (below) | `$9243`: tile `$3D`, 2 left and 3 up | `$91DF`, `RenderGreenViewMetasprite` |
| Meter marker | x `$D6 + $5D`, y `$D0` | `$A970`: tile `$3F`, 16 right; `$A96B` (`$3E`) before the backswing | `RenderSwingMeterMarkers` (`$A8DD`) |
| Meter tick | x `$8D`, y `$D0` | `$A975`: tile `$3C`, fixed | the same |
| Swing speed | fixed per speed | four tiles: `$30-$33` at OAM y 191 (SLOW), `$34-$37` at 199 (MED.), `$38-$3B` at 207 (FAST) | the setup panels |

`RenderGreenViewMetasprite` drops a sprite whose offset takes it off the screen;
`RenderMetasprite` wraps it. A metasprite's y offsets are one row above where the PPU
draws it.

**The aim cursor** sits `r` screen pixels from the ball along the aim: x by
|sin(`Aiming`)| × `r` and y by |sin(`Aiming` − `$40`)| × `r` (`CalcScaledSineTerm`), each
from `TrigLookupTable` and signed by the half turn, then rounded to a byte by
`RoundScaledTerm`: a positive product to nearest, a negative one kept when its low byte
has its top bit set and taken one lower otherwise. `r` is `$10`, or `$1C` once
`PinDistance` is `$20` or more. With the ball on the green (`MaybePlayerHoleStatus` 2)
`PinDistance` is the distance from the ball's screen point to the cup's
(`CalcPinDistance`, `DistanceBetweenPoints`), and only its low byte is compared, so a ball
256 or more screen pixels away (corner to corner of a green) gets the near cursor again.

## The aim at the flag

`CalcAimTowardFlag` (`$B9A7`) runs before every shot (`$8289`). It takes the flag less the
ball on each axis (`FlagX/Y` against the ball's pixel and fraction), halves both, keeping
their signs, until each fits a signed byte, and counts the smaller over the larger, as a
fraction of 256, against `ArctangentThresholdTable` (`$BAA9`) for an angle of up to `$20`
from the nearer axis. `AngleQuadrantBaseTable` and `AngleQuadrantMirrorTable` turn that
into a full-turn aim by the two signs.

## Peach's panel

`DrawPeachPanel` (`$BE0B`) names what the putt is for, from par less one less
`CurrentHoleStrokes` (`LD_BEE2`): 0 is par (`$BEBE`), 1 and 2 the birdie and eagle art,
and 3, or anything below 0 (bogey or worse), "cup out". Its first line is written as the
swing begins and its second eight frames later, once the backswing has started. She
blinks while `FrameCounter & $7F` is below 12 (her eyes are picked at `$BE22`), and the
swing zeroes `FrameCounter` (`$AA1C`), so the swing's first frames always fall in a blink.
