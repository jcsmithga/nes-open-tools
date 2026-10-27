# Shot Physics

> **Note**: This document was written by Claude based on investigation requested by jdharms.

How the game moves the ball from the swing to where it stops: launch, flight, wind, bounce
and roll. Everything here comes from reading bank 13, and the Python model in
`golf/physics/` reproduces it frame for frame. None of it has been checked against the game
running in an emulator yet (see **Confirming it in the game**).

`golf-shots <rom>` prints carry and total for every club and swing speed from the model.

## Where it lives

`SwingSequenceEntry` (bank 13 `$AA09`) calls `CalcLaunchVector` (`$AD0A`) once per frame.
The first call (`ShotPhaseState` 0) launches the ball. Each later call (`ShotPhaseState` 1)
advances it one frame, until something sets `ShotPhaseState` to 2. The code runs from
`$AD0A` to `$B9A0`. Apart from that it uses only the fixed bank's `Mult8x8to16` (`$E743`),
`TrigLookupTable` (`$E7CB`), the RNG (`$D29C`) and the terrain probe `ProbeBallPosition`
(`$EDBC`).

## Units

- **Distance**: one pixel is two yards. The in-flight readout (`LD_A246`) takes the
  straight-line pixel distance with `IntegerSqrt` and doubles it, so both axes use the
  same scale.
- **Position**: `BallX` `$AE` and `BallY` `$B1/$B2` have 16 fraction bits below them
  (`$AC/$AD`, `$AF/$B0`). The playfield is `$B0` (176) pixels wide, and `BallX >= $B0` is
  out of bounds on any frame.
- **Velocity**: `VelocityDeltaX/Y` (`$DA-$DC`, `$DD-$DF`) are 24-bit two's complement in
  the same fixed point, so they are in pixels per frame. Even a full drive starts below one
  pixel per frame.
- **Height**: `$B3-$B6`, with `VerticalVelocity` (`$E0-$E3`) in the same units. Height
  `>> 23` is how many pixels the ball sprite lifts off its shadow.
- **Angles**: 256 to a turn. `Aiming` 0 is up the screen and `$40` is to the right.
  `WindDirection` uses the same scale for the direction the wind blows toward.

## The swing: meters and animation

The swing states (bank 13 `$AAED-$AC91`) turn the frames A is pressed on into the
launch's inputs. They run once per pass of the swing loop, one pass a frame. A press
is dropped if it comes 1 or 2 frames after the previous one; 3 or more frames after, it
counts.

- **The rate**: `SwingMeterRate` (`$AB46/$AB49`) by swing speed is 1, 1.3125 or 1.625
  meter steps a frame. It is halved for a putt.
- **Backswing**: a press on the ready screen starts it. A putt's starts on the first
  pass, without one (`$AAED`). The power meter (`$D6`, fraction `$0587`) falls from
  `$30` by the rate each pass. Below 0 it is set to 0, losing the overshoot, and turns
  round. Climbing back to `$30` stops it there by itself, for no power. A press stops
  it after that pass's move. For a putt that also launches the ball.
- **Downswing**: the accuracy meter (`$D7`, fraction `$0588`) starts from the power stop,
  fraction and all, and climbs by twice the rate. Reaching `$4C` whiffs (`$AC44`): a
  stroke, and no ball. After an auto-stop, the next press stops it, even if that press
  was meant for power.
- **Animation**: `$D0/$D1` climbs by the rate each backswing pass. At the power press it
  is reflected about `$31` (`$62 − $D1`), and `SwingImpactFrame` (`$058F`) is read from
  `$AC0A` by the animation frame then (5 for the putter). After that it climbs by twice
  the rate while the frame is short of impact. The frame (`$CF`) is how many of bank 8's
  thresholds (`$80E7`) `$D1` exceeds. `RenderGolferAndClub` works it out each pass before
  the swing states run, and only in the behind-the-golfer view, so a putt never reaches
  impact. Hi/lo is read on every pass the animation advances.

The ball launches on the pass after the last press. How many passes of flight come
before the animation reaches impact is `frames_to_impact`: until then the view cannot
change (see **Views**). `golf/physics/meter.py` models all of this.

## Launch

The power is a chain of scalings. Each one is an 8x8 multiply that keeps the high byte, so
each is a fraction of 256:

1. **Timing**: `TimingPowerCurve[$38 − stop]`, where `stop` is where the power meter
   stopped (`$D6`). 0 is full power. Every shot loses 4 from this, except a *perfect
   drive*: 1W, full power, dead-center accuracy, from tee or fairway. `$0592` flags that
   case; the label file had it as part of `BallSpeedMagnitude`.
2. **Club**: `ClubDistanceBaseTable`. The putter uses `PutterDistanceBySpeedTable` on the
   green, and `PutterDistAltTable` plus up to ±16 steps of random aim anywhere else.
3. **Lie**: in rough or sand, a penalty scale and then ± a random share of the power
   (`RoughPenalty*`/`RoughVariance`, `BunkerPenalty*`/`BunkerVariance`, one RNG draw).
   Clubs 0-3 read the wood columns.
4. **Swing speed**: `SwingSpeedMult`, 84%, 89% or 93% for slow, medium and fast.

The launch angle is `ClubLoftIndexTable[club]` plus the hi/lo step. `$ACFA` holds each
club's step: 1 for the woods up to 4 for the wedges, 0 for the putter. Holding Down adds the
step (a higher launch) and holding Up subtracts it. sin(angle) × power is the vertical
speed and cos(angle) × power the ground speed. The ground speed is then split along the aim
into X and Y velocity.

**Backspin** is a second budget, sin(base loft) × `#$D0` × power, pointing opposite the aim
(`VelocityScaleX/Y` `$E4-$E7`). `#$D0` is a constant, 208/256, built at `$AEB5` as
`$1C << 2 + $60`; it is not zero page `$D0`. It ignores hi/lo and the spin setting. In the
air it gives lift; on the ground it brakes the roll until it is spent. Despite the name
`VelocityScale`, it is not a scale.

**Curve**: how far the accuracy meter stopped from center (`$D7` against `$30`), capped at
`$18`, minus the club's `ClubAimForgivenessTable` entry, times 8, is `AimDeviationMag`. The
side it stopped on is `AimDeviationDir`. Only the air frames use it, and first contact
clears it.

`PenaltyAccumulator` (`$D3`) collects 6 for a power stop of `$0A` or more, 3 for BACK 1 or
BACK 2, half the rough depth, or 2 in sand. `PenaltyMultiplierTable` turns the total into a
friction value for the first bounce, used when the shot has backspin (see below). TOP 1 and
TOP 2 do nothing ([topspin.md](topspin.md)).

## Every frame

In this order:

1. **Move**: position += velocity.
2. **Gravity**: vertical speed −= `$E001`. Height += vertical speed. If the height goes
   negative it is set back to zero and the frame counts as ground contact.
3. **Drag**: each axis loses 1/256 of itself, taken as the velocity's middle byte. It
   applies on the ground too.
4. **Out of bounds**: `BallX >= $B0` stops the ball with lie 5.
5. **Probe**: `ProbeBallPosition` sets the lie for the spot under the ball (see **Lies on
   a real hole**). In the overhead view only, a second probe offset by the ball's height
   looks for trees first (see **Trees**).
6. **Bunker lip**: a shot from sand can be put back in it (see **The bunker lip**).
7. **Air or ground**: `ApplyWindEffect` in the air, `ProcessLanding` on contact.

Once the ball is rolling, every frame is a contact frame. Gravity pulls the vertical speed
negative, the height dips below zero, and the bounce leaves nothing.

## In the air: `ApplyWindEffect`

- **Wind** moves the ball's *position* directly, twice per frame, from the tenth air frame
  on (`WindDelayCounter`). It never changes the velocity. Its strength is `WindSpeed` ×
  min(height/2 + 2, 7), using the height's integer byte, so a high ball feels up to 3.5
  times the wind of one skimming the ground. The code computes a doubling for clubs 0-2 at
  `$B508` and then overwrites it at `$B511`, so it never applies.
- **Lift**: along each axis, airspeed (velocity minus the wind push) × backspin × 8 is
  added to the vertical speed when the ball moves against the spin, which is the normal
  case. This is why a high-lofted club hangs in the air.
- **Hook and slice**: airspeed × `AimDeviationMag` pushes sideways. The X-to-Y and Y-to-X
  terms are not symmetric. Descending, the X-to-Y term doubles and the Y-to-X term roughly
  quadruples, and their signs run opposite ways.

### Eight wind directions are distorted

`LE7C3` reads cos(a) as `TrigLookupTable[a + $40]`, but the table only has 128 entries (a
half turn). `ApplyWindEffect` passes it `WindDirection & $7F`, so for directions whose low
seven bits are `$40` or more it reads the code after the table. The X component is right in
every case. Y is right in sign but not in size:

| Direction | Blows toward | Y the game uses | Correct Y |
|-----------|--------------|-----------------|-----------|
| `$40` / `$C0` | right / left | ±162 | 0 |
| `$50` / `$D0` | right-down / left-up | ±165 | ±98 |
| `$60` / `$E0` | down-right / up-left | ±1 | ±180 |
| `$70` / `$F0` | down-right / up-left | ±96 | ±236 |

(Out of 255; positive is down the screen.) So a crosswind to the right also pushes the ball
down the screen at 64% strength, and one to the left pushes it up. A wind at `$60` blows
almost due right instead of diagonally. Wind anchors are multiples of `$10`, so 8 of the 16
possible directions are affected.

The `wind_fix` patch corrects the lookup, and every randomizer seed from unfinished build
version 6 has it ([wind.md](wind.md), **The fix**). The Python model ports the vanilla
lookup (`PhysicsTables.cos_unmasked`), so it gives the vanilla ROM's wind and is wrong
about those eight directions on a patched ROM. With no wind the two agree.

It matters for shots aimed up the screen. A high PW at medium speed goes 101 yards with no
wind. With wind 9 it goes 87 yards in a pure crosswind to the right (`$40`), and 130 in one
to the left (`$C0`), as far as a diagonal tailwind (`$20`) takes it.

## On the ground: `ProcessLanding`

1. **First contact only.** On fairway or depth-0 rough, a random sideways kick
   proportional to the Y speed (`LD_B952`, table `$B9A1`). It reads the RNG state without
   advancing it. On the green, BACK 2 doubles the remaining backspin and BACK 1 multiplies
   it by 1.5, but only for clubs 4 and up and not for shots played from the rough.
2. **Bounce.** Vertical speed reverses and keeps 3/8 of itself. When nothing is left in its
   top byte it is zeroed and the ball rolls. The curve is cleared.
3. **Friction.** Each axis loses k/256 of itself. k depends on the lie, with a larger
   one-off value on first contact. A low shot takes up to `$3F` off any k of `$40` or more.
4. **Backspin brake.** 1.5 × the remaining backspin is taken from the velocity, then the
   lie's spend is subtracted from the budget. A budget that wraps past `$F0` counts as zero.
5. **Stop.** Once the backspin is gone and both velocities' middle bytes are within
   ±`stop` of zero, the ball stops.

| Lie | k per frame | k on first contact | Spin spent per frame | `stop` |
|-----|-------------|--------------------|----------------------|--------|
| Tee, fairway | 6; 7 high; 4 low | `$50` or `$30` on the RNG's low bit; +`$40` high, +`$10` low; BACK 1/2 not low, `PenaltyAccumulator` instead | 2 | 2 |
| Rough | depth × 4 + 15 | `PenaltyAccumulator` + `$40` | 5 | 4 |
| Bunker | `$14` | `$60` | all of it | 6 |
| Green | 2; 3 high; 1 low; +7 when `$CA` bit 7 is set | `PenaltyAccumulator` + `$20` (not low) | 1 | 1 |
| Water | 3, after a skip | — | 4 | 3 |
| Out of bounds | `$14` | `$70` | 8 | 6 |

- **Green**: the slope pushes the ball too. That model, and why it feels weak, is in
  [green_slope_physics.md](green_slope_physics.md).
- **Bunker**: the bounce and the spin are canceled on contact. How hard the ball hit
  (`$0C` and `$19` in the vertical speed's top byte) and the RNG decide `BunkerDepth` 0-2.
  Any landing harder than the softest one plugs the ball on the spot.
- **Water**: the ball sinks, unless it arrives shallow (below `$30`) and the RNG draws
  `$E6` or more (about 1 in 10). Then it skips once, at half its vertical speed.

## The bunker lip

A shot played from sand can be stopped at the edge of the sand (`$B073-$B0D0`). The test
runs each frame after the probe, and acts on the first frame that meets all of these:

- the shot started in sand: `SwingSequenceEntry` (`$AA1E`) sets `BunkerExitArmedFlag`
  (`$05A4`) only then;
- the previous frame's lie (`$05B3`) was sand and this frame's is not;
- the ball had been over sand for 3 frames or more (`BunkerFrameCount`, `$05A3`). Launch
  from sand sets the count to 3, so the first exit always passes. A frame off the sand
  zeroes it;
- the height's top byte (`$B6`) is zero: the sprite is lifted less than 2 pixels.

That frame clears the flag, so a shot is tested once. The ball is then put back if any of
these holds:

- the club is the putter;
- the club is 1W-2I (`ClubSelection + 1 < 7`, the middle entry of
  `MaybeBunkerExitClubThresholdTable`; Y is always 1);
- the power meter stopped at `$18` or later (`$D6`, the weaker half of its travel) and the
  ball is still low in the behind-the-golfer view (`BallScreenY` `$A8` or more).

Putting it back copies the last position it had over sand (`$059C-$05A2`, saved every sand
frame by `$B0F4`), probes there, clears the height, sets lie 3 and stops the ball.
`BunkerDepth` is not touched. A ball that leaves the sand higher than that keeps the flag,
so the test can still fire later in the same shot, in another bunker it has been over for
3 frames: coming down across the far edge under 2 pixels of lift, or landing in the sand
and moving out of it on the next frame. The ROM's physics does both under py65 over a
synthetic ground. Either way the ball moves back less than a pixel and drops less than 2,
and comes to rest in sand a pixel or two short of where it would have landed.

How far the ball gets in those first frames is what the launch decides: club, power stop,
swing speed, hi/lo, `BunkerDepth` and the sand's random power variance.

### What players should know

Distances are from the model at `BunkerDepth` 0, measured along the aim line from the ball
to the edge of the sand. A pixel is 2 yards.

- **The test is on the meter, not the power.** A power stop past the middle of the bar
  (`$D6` below `$18`) counts as a strong swing. The middle of the bar is about 67% of full
  power.
- **3I through SW get out.** On a strong swing they are never caught. On a weaker one,
  only a ball on the last pixel of sand is.
- **1W through 2I need room.** On a strong swing they are caught when the edge is within
  3-5 pixels (6-10 yards).
- **Weak swings with those clubs are worse.** Around three-quarters of the way down the
  meter the ball never lifts 2 pixels, so it is catchable for its whole carry, up to 16
  pixels (32 yards). A high shot there clears after 3-5 pixels.
- **High helps a little, low hurts a little.** About half a pixel each way on a strong
  swing.
- **The putter cannot leave a bunker.** It is stopped at the edge every time.
- **A caught ball still moves.** It stops at the last sand it was over, not where it was
  hit from, and the stroke counts.

## After the shot: water and out of bounds

The play loop (bank 13 `$85E9-$8672`) deals with the lie the ball finished on:

- **Water** (lie 4): the ball goes back to `$05A5-$05AB` (`$B114`), with one penalty
  stroke. Every frame of the physics, after the probe (`$B029`), and once at launch,
  `LD_B0FF` copies the ball's position there, fractions and all, unless the lie under it
  is water or out of bounds. So the drop is where the ball was on the last frame it was
  over anything else, in the air or on the ground. The model keeps it as `Ball.drop_x`
  and `drop_y`, compared with the game every frame.
- **Out of bounds** (lie 5): stroke and distance. `LD_86C8` puts the ball back where it
  lay before the shot. `LD_86ED` stores that for each player after every shot (`$0113-$011D`:
  position without its lowest fraction bytes, which become `$80`, and `BunkerDepth`). It
  clears the height and adds one penalty stroke.

Everywhere else the next shot is played from where the ball stopped. A holed ball ends
the hole (`$832E`). `play_on` in `golf/physics/rules.py` gives where the next shot is
played from and what the shot cost. `test_game_rom.py` follows every shot into the play
loop and checks both against what the game did.

## Where randomness enters

Every draw uses the RNG ([seeded_wind.md](seeded_wind.md)). Only three things advance it:
the putter's aim wobble off the green, rough and bunker power variance at launch, and the
water-skip test. The first-contact kick, the fairway coin flip and the bunker depth read
the state without advancing it. So a shot from the tee or fairway that lands on grass is
decided by the starting RNG state. The coin flip is the only part of that which changes the
distance much (a medium 5I off the fairway finishes at 177 or 182 yards). The kick moves the ball a few
tenths of a pixel sideways.

## Lies on a real hole: `ClassifyProbePosition`

`ClassifyProbePosition` (`$EDEA`) takes a pixel and decides the lie from two things: the
palette of its 16x16 supertile, and its 8x8 terrain tile. The palette says what the plain
surface is: 1 fairway, 2 sand, anything else water. The tile then decides:

| Tile | Lie |
|------|-----|
| `$25` | light rough (`RoughDepth` 0) |
| `$27` | the palette's surface |
| `$35-$3C` | tee (lie 1; fairway is lie 0) |
| `$40-$7F` | shaped edges: a 1-bit mask per pixel (`$F020`); set is the palette's surface, clear is rough (light next to fairway, deep elsewhere) |
| `$80-$9B` | shaped rough: mask at `$F220`; set is deep rough, clear is out of bounds |
| `$3E`, `$9C-$9F` | trees in deep rough |
| `$A0-$BB` | trees, out of bounds |
| `$BC-$BF` | trees over the palette's surface |
| `$DF` | deep rough |
| everything else | out of bounds |

Tree tiles also report whether the pixel is trunk (color 2 of a 2-bit mask at `$F3E2`,
flag `$0597`) or clear next to foliage (`$0598`).

Inside the green's 24x24-pixel box (`GreenX/Y`), each pixel is one tile of the green's own
24x24 grid. Tiles below `$30` are fringe and fall back to the terrain. Anything else is
lie 6. Tiles `$48-$87` set `$CA` bit 7 (more roll friction), and tiles `$30-$47` and
`$88-$9F` carry a slope, which `LF300` writes to `$EA-$EF`. The `$88-$9F` set also sets
`$CA` bit 6, which scales the slope differently.

Anything at `BallX >= $B0`, or at or below `TerrainBottomY` for the hole's scroll limit, is
out of bounds.

## Views, and what they change

`ViewMode` (`$98`) is set by the routines that draw each view: `$80` behind the golfer
(`LD_A499`), `$00` overhead (`LD_9AEF`), `$40` the green (`LD_A381`), `$C0` the close-up
of the cup (bank 9 `$8000`, see **The cup**), `$FF` at hole start and on resume. A shot
starts behind the golfer, or on the green view for a putt.
Confirmed in Mesen: `$80` from the swing until the view changes, then `$00`.

After the physics each frame, the main loop (`$AA37-$AA9B`) decides whether to switch:

- **Behind the golfer**: on touchdown at any time. Otherwise not before the golfer's swing
  animation (bank 8, `$CF`) reaches its impact frame (`SwingImpactFrame`, `$058F`), and
  then once the ball starts to fall, leaves the scene (`$C5` = 0), or is `$3D` pixels
  (about 122 yards) out on the distance readout.
- **Overhead and green**: on touchdown, or once the ball is falling.
- **The cup**: every frame.

When it does switch, it goes to the green view if the ball is inside the green's box, not
in sand or water, and either on the green or already in the green view. If it is already
in the green view or the cup view, the cup routine runs instead (see **The cup**).
Otherwise any view other than overhead goes to overhead.

Switching to overhead redraws the course, and that redraw (`LD_A170`) zeroes the distance
readout. So does plugging a ball in sand, which redraws too.

### The cup

`LD_A884` (bank 13) far-calls `UpdateBallAtCup` (bank 9 `$81C4`) on each frame the view
switch reaches it in the green view, and on every frame of the cup view. The routine
takes the ball's offset from the flag (`FlagX/Y`, `$A7-$AA`) times 8 and rotates it by the
aim plus `$80` (`RotateVector16`), so the shot runs up the screen. It then places the ball
on the close-up's screen: `$63` is `$80` plus 20 × the offset across the aim line (in
pixels/256), and `$64` is `$C6` less about 6 × (the offset along it + 4). The ball is in
reach when it is less than 6 pixels to either side (5 to open the close-up), and
`$64` is at least `$54` (and below `$C4` to open it). It must also be less than `$200` high
in `$B6:$B5`, or under `$80` after the first bounce. Out of reach, `$64` is set to `$F0`.

In reach, a green view opens the cup view: `ViewMode` `$C0`, and `$0582` = `$FF`. Out of
reach, a cup view closes. Unless a lip-out is playing, the ball's velocity is halved
(twice on a putt, `LD_A8B6`), its height is halved, and the green view is redrawn.

The hole is an outline of screen pixels: for each of 40 columns from the left edge to the
center, mirrored for the right half, rows `$83D5[x]` to `$83FD[x]`. `$0594` counts frames
the ball is over it at height 0. `$0580/$0581` keep the last screen position there, and
`$0582` the first row. Each frame over the cup:

- **Holing out**: on the 2nd frame or later, if the ball's speed is under `$1C`. The speed
  is |vx| + |vy|, from the velocities' middle bytes. `ShotPhaseState` becomes 2 and
  `MaybeHoleCompleteFlag` (`$05B9`) counts the ball in.

When a landed ball leaves the outline after being over it, and it left through the far
side (`$64` of `$B5` or more), one of these happens. It does not if the ball has already
bounced off the flagstick and is moving at `$18` or more.

- **Rim-in**, below speed `$28`: it drops in anyway. Off to one side of the center (`$63`
  outside `$6A-$94`), `$05C2` records how fast, for the drop animation.
- **Lip-out**, at `$28` or more with `$63` in `$60-$9F`: `$0593` becomes `$FF`, and its
  velocity is halved (twice on a putt). While `$0593` is `$FF`, the physics runs only every
  4th frame (`$0585`, which counts frames, `$AF3A`). The cup routine animates a hop in
  `$65-$68`. It starts at speed −min(speed, `$50`) × `$12`, gains `$4E` a frame, and ends
  when it comes back down.

**The flagstick**: on a shot other than a putt, a ball that reaches `$63` `$6F-$91` and
`$64` `$AF-$B9` sets `$0595` to `$FF`. The next frame's physics (`$AF49`) reverses the
velocity and halves it, and sets `$0595` to 1. That happens once per shot.

**In the cup view** the physics changes. The move uses half the velocity (a quarter on a
putt). The rest of the frame (gravity, drag, the probe, the landing) runs only on every
2nd frame of `$0585`. On a putt, once the ball is on the ground, it runs every 4th.

`$63-$68` are zero-page bytes that other code uses as scratch outside the cup view.
Nothing reads them there, and `$0580-$0582` and `$0593-$0595` are what carry the state.

The shot ends when the ball is holed, before `BallDropAnimationEntry` (bank 9 `$8050`)
plays the drop.

### Trees

Each view has its own tree rule.

- **Behind the golfer**: `LD_BB6D` projects the ball into the scene (`LE87C`). The
  ball's position relative to the shot's origin (`$B8-$BC`) is rotated by the aim the
  scene was built along (`$BD`), then divided by depth, all in pixels: depth is 15 + the
  distance ahead; screen x is `$90` ± 208 × sideways ÷ depth; screen y is `$65` − 52 ×
  (5 × height − 22) ÷ depth. `LE9C8` then looks up the scene's depth and tile maps at
  that screen pixel (`$7AE6` and `$77E6`, 32 tiles a row). A tile standing 1-3 depth
  units behind the ball whose shape covers the pixel is a hit, and `$0599` becomes the
  tile number. Near the bottom of the screen it tests the pixels 3 either side. One
  quirk: the shape test repoints the depth map pointer at a mask table and never puts it
  back, so the second of two samples reads its depth from there.
- **Overhead**: `$AFF3` probes the spot the ball's sprite covers, `($B6:$B5) >> 7`
  pixels up the screen, while that is under 14. A trunk there and a leafy edge under the
  ball decrement `$0599` from 0 to `$FF`.

Whichever way it was set, `$0599` is acted on the next frame (`$B02C`). The first time, a
value of `$CC` or more stops the ball dead: ground velocity and backspin zeroed, still
falling. Less than that sends it back at a quarter speed on both axes. After that, for as
long as `$0599` stays set, the Y velocity is reversed and quartered again every frame.

### Hi/lo is read after the swing

`UpdateSwingHiLoAndAim` runs on every frame of the swing, not just while aiming, and keeps
running after launch until the swing animation reaches impact (`LD_ACB4`, from `$AC8F`).
The launch angle uses `SwingHiLo` at launch. The landing friction (a low shot's softer
first bounce and lower roll friction, a high shot's higher) uses it as it is when the ball
lands. So letting go of Up or Down just after the swing changes how the ball bites.

## The model

`golf/physics/` ports all of the above:

- `launch.py` and `flight.py` cover the launch and the air.
- `landing.py` covers ground contact.
- `terrain.py` is `ClassifyProbePosition`, over a hole from course JSON (`HoleGround`).
- `perspective.py` and `distance.py` cover the behind-the-golfer projection, its tree
  collision and the distance readout.
- `cup.py` is the cup and the flagstick, which `ShotInFlight` runs when it is given the
  flag. Without one there is no cup.
- `rules.py` is what happens after the shot: water, out of bounds and holing out.
- `meter.py` turns the frames A is pressed on into the meter stops and
  `frames_to_impact`.
- `wind.py` gives the winds a hole and a shot can be dealt, with their probabilities
  ([seeded_wind.md](seeded_wind.md)).
- `shot.py` runs the frame loop: the physics, the scene, then the view switch.
- `green_view.py` is where the putting view draws the ball, the cup, the aim cursor and
  the meter's marker, and the aim every shot starts with ([putting_view.md](putting_view.md)).
- `flights.py` is not a port: it plays a shot once over uniform ground and finishes it
  from other starts, giving exactly what `shot.py` gives, much faster. Its docstring says
  which parts of a frame read the ground.

It reads its tables from whatever ROM it is given, and it takes the terrain from a
`Ground`: `UniformGround` for a driving range, or `HoleGround` for a real hole.

Three references check it (`tests/physics/`, run with `--physics`):

- `rom_oracle.py` runs the ROM's `CalcLaunchVector` alone under py65, with the terrain
  routed to the same `Ground`, and `RomTerrainProbe` runs `ClassifyProbePosition` over a
  hole loaded into RAM. `test_shot_rom.py` plays random shots through both over uniform
  and striped ground. `test_terrain_rom.py` compares every pixel of all 54 NES Open holes
  on the vanilla ROM, and of all 90 Mario Open holes on a ROM with the `wram_expansion`
  patch, which holes over 48 rows need. `TerrainTables` finds the tables and buffers that
  patch moves through the operands of the instructions that read them.
- `nes.py` is just enough of an NES to run the game's own frame loop: MMC1 banking, the
  controller, and the game's NMI handler at each vblank. `rom_game.py` loads a hole with
  the game's `InitHole` and plays a shot through `ShotSetupSequence` by pressing buttons,
  with the setup panels, the swing, the scene builder, the views and the trees all running
  as they do on a console. `test_game_rom.py` replays random shots from random lies on
  random holes in the model, and every register must agree after every frame.
  `green_view_sprites` there runs the putting setup alone and returns the sprites it
  leaves in RAM, for `test_green_view_rom.py`.
- `test_scene_rom.py` calls the projection, the distance and the scene collision directly,
  with thousands of random inputs, over scenes the game's builder made.

## Not modeled yet

- **The scene builder** (bank 9 `$8829`, from `ShotSetupSequence`): it probes 64 × 20
  points ahead along the aim and draws the scene into the maps. The model takes a
  `PerspectiveScene` the ROM built; without one, the behind-the-golfer phase has no trees.
  Row `r` of the probe grid lies `$8ED0[r]` pixels ahead (99, 67, 49, ... 1, 0, −1), and
  its 64 points `$89D0[64r + i]` pixels to the side (about ±70 on the farthest row),
  turned by the aim; nothing farther than 99 pixels ahead is in the scene. The builder
  draws on the RNG (`$91C9`): between RNG states, one tile of the tile map near the
  horizon differs (`$D7`, `$E7`, `$E8` or `$FC`), and the depth map does not.
- **View `$FF`**, which the physics treats like the cup view (both test bits 6 and 7). It
  does not occur during a normal shot.
- **The drive-distance statistic** accumulated in `$05C0/$05C1`.

## Confirming it in the game

`test_game_rom.py` compares the model with the game's own frame loop, so what is left to
check on a console is that machine: that nothing it does not emulate (the PPU, sprite 0,
IRQs) changes a shot. Two cheap checks in Mesen:

- **A perfect 1W drive from the tee** (full power, dead-center accuracy, medium speed, no
  wind) onto fairway should carry 235 yards and finish at 268 (`golf-shots` shows the
  same). Run in practice mode with the wind set to 0.
- **The wind distortion**: in practice mode set the wind to `$40` and hit a high wedge
  straight up the screen. The model says it lands short as well as right of a no-wind
  shot.
