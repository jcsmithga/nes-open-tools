# Hole Difficulty Analysis

> **Note**: This document was written by Claude from decisions made with jdharms.
> It reports a spike: rating how hard NES Open and Mario Open holes are from the game's
> own physics. What was built and decided, how to pick it up, and what it found.

**Status**: paused (ADR 0012). The physics model reproduces the game's shot loop exactly
(phases 1 and 2, apart from the Mesen spot checks), and `golf-difficulty` solves a hole
for one pin with no wind, under PyPy (phase 3). Every NES Open and Mario Open hole is
solved at skill 3 from its first pin: **Results** ranks all 144 against par, and
**Data** says where the solves are kept. What could come next is **Future work**.

## Goal

The expected score of any hole: how many strokes a scratch player takes from the tee on
average, with no wind, over the hole's four pins. The solver works this out from every
spot on the hole, so strokes-to-hole maps, difficulty against a featureless baseline and
per-hole summaries can be derived from it afterwards. Any of them could feed the
randomizer's catalog.

What the randomizer needs most (jdharms) is where the 90 Mario Open holes sit against the
54 NES Open holes. Its players know the Mario Open holes an order of magnitude less well,
and with every hole's expected score against par, the randomizer could build courses
that play close to their par instead of drawing holes that each play a little over it.
For that, the holes' places relative to one another matter more than the absolute
numbers or the exact skill.

The idea comes from Matthew Schoolfield's course-strategy simulator
([I spent the last month and a half...](https://golfcoursewiki.substack.com/p/i-spent-the-last-month-and-a-half)).
Its weakest parts were approximate physics and no putting. Here the physics is the game's
own, reproduced exactly, and putting is part of the same model.

In scope: the vanilla NES Open and Mario Open (JP) courses, and any hole in the course
JSON format. Out of scope for now: rating whole randomizer seeds, and any change to the
site.

## Terms

- **Model**: `golf/physics/`, the Python port of the game's shot loop. It agrees with the
  game on every register every frame (`docs/shot_physics.md`, ADR 0011).
- **Oracle**: the game's own code run under py65, to check the model against. There are
  two kinds (see **The mini-NES**): single routines (`rom_oracle.py`) and the whole game
  (`nes.py`, `rom_game.py`). Every new ported piece is checked against one before
  anything is built on it.
- **Ground**: what the model asks "what is under the ball?": `UniformGround` for a driving
  range, `HoleGround` (`golf/physics/terrain.py`) for a hole from course JSON.
- **Intent**: the shot a player means to play (`golf/difficulty/player.py`): club, swing
  speed, spin, hi/lo, aim, and the power and accuracy stops they want.
- **Skill**: how far a player's execution scatters around their intent: normal errors
  in frames on each meter press, and in aim steps on the aim.
- **Anchors**: a hole's wind direction and speed, dealt once by `InitHole`. Each shot's
  wind is the anchors plus a speed jitter, dealt at shot setup.
- **Expected strokes**, E(p): the average number of strokes to hole out from spot p,
  playing the best intent from every spot.
- **Baseline**: expected strokes from the same distance on uniform fairway with a cup.
- **Difficulty**: expected strokes minus baseline, for one spot or summed over a hole.

## Decisions

- **The output is expected strokes from the tee** (jdharms). Difficulty maps, baselines
  and per-hole summaries are derived from the value function later.
- **Skill is solved for, not assumed.** A scratch player is whoever averages 72 over a
  round, with no allowance for physical strength since NES Open has none. That skill is
  then carried to other holes and courses unchanged.
- **Skill 3 for placing the Mario Open holes** (jdharms). The U.K. round is 73.2 at skill
  3, so a scratch player's skill is a little under 3, about 2.7-2.9, and a skill that close
  moves individual holes by hundredths to a tenth. Fitting one number to one target
  cannot test the model, so a careful calibration waits; a rough one (one more U.K.
  round, near 2.5) is enough when a label is wanted.
- **Calibrate on the U.K. course.** It is harder than Japan ("course zero", with some
  beginner feel) and the US (the course menus put first), but easier than Mario Open's
  hardest. That leaves US and Japan as out-of-sample checks, with Mario Open as the hard
  end.
- **Perfect strategy.** The solver always picks the best intent; only execution is
  imperfect. Schoolfield's simulator did the same. A calibrated scratch player therefore
  executes a little worse than a real one, to make up for real players' strategy mistakes.
  The calibration absorbs this, and it is the same everywhere.
- **Skill is one number to start.** It scales every error source together, with their
  ratios as a stated assumption. A single target (72) can only pin down one number.
  Sensitivity runs would then vary the ratios (**Future work**).
- **Timing error is normal in frames about the intended press** (jdharms): a bell curve,
  rounded to whole frames, around the frame the player meant to press on. The intended
  accuracy stop is part of the intent, since hooks and slices on purpose are part of the
  game, so the error is measured from it, not from `$30`.
- **Aim error is normal in aim steps**, rounded to one of the 256, and one size for
  every aim: left and right move the aim the same amount a frame wherever the player
  aims (jdharms).
- **The solver plays from the vanilla 14-club bag**, not a seed's bag (jdharms).
- **No wind** (jdharms, for now): every shot is played at wind speed 0. The game deals
  wind to every hole and every player alike, headwind as often as tailwind, so its effect
  is taken to average out across holes, and the calibration absorbs what is left. Calm is
  rare in the game (a speed-0 anchor is 4 in 64), so this is a modeling choice, not the
  typical case. Revisit if the hole rankings disagree with real scores; the machinery for
  it is `golf/physics/wind.py`. Wind changes the best strategy on some holes (**Wind on
  two holes**, phase 3), which the average hides.
- **The solver has one pin** (jdharms): the hole's first. The other three
  (`Flag.for_pin`) are future work; a seed knows each hole's pin
  (`seed_holes.pin_index`), so they matter to the randomizer.
- **Only spots that play reaches are valued** (jdharms): the tee, and every spot the best
  play from it visits often enough. Most of a hole is rough nobody plays from.
- **Errors reach 2 standard deviations** (jdharms), a choice for the sensitivity runs to test
  (**Future work**).
- **The player aims from the overhead view** (jdharms). The behind-the-golfer scene is
  built once, along the aim at setup (`$BD`), and holds only what lies in its wedge, so
  aiming away before it is built and turning back afterwards leaves nearby trees out of
  it. That is taken as an obscure exploit, not strategy: every shot's scene is built
  along the aim it is played on (`ShotInput.scene_aim` left to default).
- **Expected score is averaged over the hole's four pins**, all equally likely, as
  `InitHole` deals them (`docs/seeded_wind.md`, `golf/physics/wind.py`).

## Picking this up

**Read first**, in this order:

1. This document.
2. `docs/shot_physics.md`: how a shot works in the game, with ROM addresses: the swing,
   the flight, the cup, penalties, and what the model does not cover.
3. `golf/physics/CLAUDE.md`: how to test, and the rules for code in `golf/physics/`.
4. ADR 0011 (`docs/adr/`): why the model is an exact port rather than idealized physics.
5. `docs/seeded_wind.md`, **The vanilla wind as probabilities**.
6. `golf/difficulty/player.py`: the player model, which the solver builds on.
7. `golf/difficulty/landing.py`, `green.py` and `solver.py`: the module docstrings say
   how the screen, the green and the rounds work.
8. The `nes-open-golf-rom-peek` and `nes-open-golf-label-conventions` skills, before any
   reverse engineering. Most of this work is reading 6502 in banks 8, 9 and 13.

**Run:**

```bash
uv run pytest --physics tests/physics     # all the model's checks against the game (~2 min)
uv run pytest tests/integration/test_player_model_rom.py   # the player model (~5 s)
uv run pytest tests/integration/test_flights_rom.py        # shared flights against simulate (~5 s)
uv run pytest tests/integration/test_difficulty_solver_rom.py  # landing table, solver helpers
uv run pytest tests/integration/test_green_rom.py          # the green table against outcomes()
uv run golf-shots nes_open_us.nes         # carry/total table from the model
uv run golf-difficulty nes_open_us.nes --course us --hole 1 --output us01.json
                                          # solve one hole (builds the landing table first)
uv run golf-rom-peek nes_open_us.nes --labels "NES Open Tournament Golf (USA).mlb" \
    disasm '$81C4' --bank 9 --count 200   # e.g. the cup routine
```

The label file (`NES Open Tournament Golf (USA).mlb`) is gitignored here: it lives in a
separate private repository, symlinked into the checkout. This work added a lot of labels;
if the file is missing, the addresses in the docs still work but disassembly will be less
annotated.

**Code map**, `golf/physics/` (exact ports, each checked against the game):

| File | What |
|------|------|
| `state.py` | `ShotInput`, `Ball` (the game's registers, named), `Terrain`, `Ground` |
| `tables.py` | Every ROM table the physics reads |
| `meter.py` | The swing meters and animation: the frames A is pressed on to meter stops and `frames_to_impact` |
| `launch.py`, `flight.py`, `landing.py` | Launch, air frames, ground contact |
| `terrain.py` | `ClassifyProbePosition`: `HoleGround` |
| `perspective.py`, `distance.py` | The behind-the-golfer projection and tree collision; the distance readout |
| `cup.py` | `UpdateBallAtCup`: holing out, lip-outs, rim-ins and the flagstick |
| `shot.py` | `ShotInFlight` (one `step()` per frame, in the game's order), `simulate()`, and `Flag` (`Flag.for_pin`: a hole's pins) |
| `rules.py` | `play_on`: where the next shot is played from, and the strokes it cost |
| `wind.py` | The winds and pins a hole deals, as probabilities |
| `flights.py` | Not a port: `Flight` and `Flights`, a shot recorded once and finished from any start, exactly as `simulate` would play it |
| `rom_oracle.py` | Single-routine oracles: `RomShot`, `RomTerrainProbe`, `read_ball()` |
| `nes.py`, `rom_game.py` | The mini-NES, and a shot played through the whole game |

`golf/difficulty/` (our own modeling, built on the physics):

| File | What |
|------|------|
| `player.py` | `Intent`, `Skill`, `Position`, `Hole`; `outcomes()`: an intent's results with probabilities |
| `landing.py` | The landing table: every intent's perfect, windless rest from each lie at 8 base aims, cached under `.cache/difficulty/`; `scatter`, an intent's rests under a skill's timing errors |
| `green.py` | A green solved whole: `build_pixel` plays every putt from a pixel once, `GreenSolver` values the green by lookups alone, at any skill |
| `solver.py` | `HoleSolver`: states, the screen, racing, rounds of play in worker processes, value iteration, reach |

`tools/research/difficulty.py` is the `golf-difficulty` CLI. `golf/core/rng.py` (the
game's RNG and wind) and `golf/core/clubs.py` live outside `golf/core/patches/` so the
physics and the solver import nothing that PyPy 3.11 cannot parse.

### The mini-NES

`golf/physics/nes.py` (`NesMachine`) is just enough of an NES to run the game's own frame
loop under py65. It exists because single-routine oracles kept missing code that runs
elsewhere in the same frame: the swing animation in bank 8 gates the view switch, bank 9
builds the scene and runs the cup, and the NMI reads the pad. It gives the game:

- **MMC1 PRG banking**: writes to `$8000-$FFFF` shift into the mapper's register, and the
  fifth write to the PRG register swaps `$8000-$BFFF`. The game only changes banks through
  `SetPrgBank` (`$D35A`), so far calls (`ExecuteFarCall`, `$D372`) just work.
- **The controller** at `$4016`: set `machine.buttons` (the `BUTTON_*` constants), and the
  game's NMI reads it like a real pad.
- **Frames**: when the game reaches `WaitForVblank`'s busy loop (`$CD7D`), the machine
  calls `on_frame(frame)` and then runs the game's own NMI handler (`$D2BF`).
- **Breakpoints**: `machine.add_breakpoint(address, callback, bank)` runs Python before an
  instruction. `rom_game.py` uses them to set the aim before the scene is built, snapshot
  the ball once per pass of the swing loop, capture the scene, and stop.

Not emulated: the picture (PPU writes land in plain memory, unless the machine is made
with `ppu=True`, which keeps the PPU's memory for `golf/rendering/nes_screen.py` to draw;
`$2002` reads alternate the vblank bit so polling loops end), sprite 0, IRQs, the second
pad. Nothing in a shot is
known to depend on them. That is what the Mesen spot checks in phase 1 are for.

`golf/physics/rom_game.py` (`RomGameShot`) uses the machine to play one shot:

1. `RomGameShot(rom, course, hole, rng_state=0)` sets the course and hole and runs the
   game's own `InitHole` (`$DA90`), which decompresses the hole into RAM exactly as the
   game does and deals the pin and wind anchors from `rng_state`. Only vanilla holes can
   be loaded this way; a custom hole would need writing into a ROM first (`golf-write`),
   which has not been tried.
2. `play(shot, swing)` pokes in what earlier play would have set (ball position, RNG,
   wind, club in bag slot 0, swing and putt speed, spin), then runs `ShotSetupSequence`
   (`$877A`), tapping A through the setup panels. It holds Up/Down for hi/lo for the
   whole shot (the game keeps reading it after launch), and presses A at the frames
   `Swing` gives to start the swing and stop each meter.
3. It returns a `RomShotRecord`: the shot as actually launched (`shot`, with the meter
   stops, RNG, aim, `frames_to_impact` and `scene_aim` read from RAM at launch), the ball
   after every frame (`frames`, as `Ball` via `read_ball()`), `view_modes`, the scene
   (`PerspectiveScene`, a WRAM snapshot) and the `flag`.
4. With `follow_through=True` it starts from the play loop (`$82AD`) instead, and runs on
   past the shot until the loop has dealt with the lie. It records the ball then
   (`after`: dropped after water, back after out of bounds) and the strokes the shot
   added (`strokes`). The ball's position before the shot, which out of bounds goes back
   to, is poked in as `LD_86ED` would have left it.

Things that will trip you up:

- **A frame is a pass of the swing loop** (`$AA2A`), not an NMI. They are one to one
  through the swing; after launch some passes wait for vblank twice, so NMI counts drift
  from the physics.
- **Read inputs at launch, not after the shot.** Display code rewrites `$D6/$D7`, and
  hi/lo changes until impact. `rom_game.py` reads them on the pass that launches.
- **Some registers are stale at launch**: scratch bytes (`$EA-$EF`) and readouts other
  code left behind. `CARRIED_FROM_LAUNCH` in `tests/physics/test_game_rom.py` copies them
  from the game's launch frame into the model before comparing.
- **A swing whose accuracy press comes too late whiffs**: the meter runs off the end and
  no ball launches (`WhiffError`). The tests check that `meter.swing` whiffs too, then
  skip the shot.
- **A putt's backswing starts by itself**, so `Swing` gives it one press, at
  `start + power`, which is backswing pass `start + power − 1`. It uses the putt speed
  (`$0125`), not the swing speed, and launches with spin TOP 2.
- **Presses need gaps.** The game ignores a press 1 or 2 frames after the last one, so
  a `Swing` with `power` or `accuracy` under 3 does not do what it says.
- **`$63-$68` are scratch outside the cup view.** `Ball` carries them (`cup_x` and so on)
  but does not compare them. What they decide shows up in `$0580-$0582` and `$0593-$0595`.
- **The pin and wind anchors come from the RNG state when `InitHole` runs**
  (`RomGameShot`'s `rng_state`). The shot's own RNG and wind are poked in afterwards.
- **To see a new register**, add it to `Ball` and `read_ball()`, and the frame-by-frame
  comparison picks it up. That is usually the quickest way to find where the model and
  the game part.
- **Scratch scripts** that sweep hundreds of random shots and print the first differing
  frame were how every bug here was found. `tests/physics/test_game_rom.py` is the
  template.
- **Shared flights must follow the frame loop.** `flights.py` knows which registers the
  probe writes and which checks read the ground (see its docstring). A change to
  `ShotInFlight` that adds either must be mirrored there; `test_flights_rom.py` compares
  `Flight.finish` with `simulate` on every NES Open hole and fails when they part.
- **The at-the-flag seeds are pinned.** `test_shot_at_the_flag` names the outcome each
  seed must produce. A change to its picker, or to the model near the cup, can move
  them. Find them again by running the picker over seeds until each outcome has enough
  cases.

### Data

The solves behind **Results** are kept in the repository, slimmed:

- `data/difficulty/holes.json`: one row per hole: its lineage as the catalog and
  `data/catalog/curation.json` name it (`nes_us/16`, `jp_uk/02`), par, yards, handicap,
  expected strokes from the tee, strokes over par, `unvalued` (visits a hole to spots too
  rare to value, which borrow their values), rounds, skill and pin.
- `data/difficulty/solves-skill3-pin0.tar.xz`: every hole's solve as `golf-difficulty
  --output` writes it, less each state's `played` list (every intent tried there, with
  its screen rank): per state its class, pixel, expected strokes, visits a hole and chosen
  intent. With it, the solve logs; a `<course>.resolve.log` holds the holes solved again
  after the cleanup-round fix (**Results**). To work with it, extract it to scratch:

  ```bash
  mkdir -p <scratch>/solves && tar -xJf data/difficulty/solves-skill3-pin0.tar.xz -C <scratch>/solves
  ```

- `golf-difficulty-report <solves>` (`golf/difficulty/report.py`) rebuilds both, and the
  tables in **Results**, from a solves directory: a directory of `hole_NN.json` per
  course, named as the catalog names the course (`nes_us`, `jp_uk`), and its logs beside
  them. It reads an extracted archive as well as `golf-difficulty`'s own output.

NES Open holes were solved on `nes_open_us.nes` (SHA-1 `53b47f2b68c353afbc822baee0a9172eb16e38af`),
Mario Open holes on `nes_open_wram.nes` (`1e62a2a2a26795a3d932a063d34a702926597b87`,
phase 1), whose physics tables are the same. A course takes 6-10 hours on 16-20
workers. `--output` is a file for one hole and a directory for several:

```bash
mkdir -p .cache/difficulty/solves/jp_uk
PYTHONPATH=. .cache/pypy/bin/python -u -m tools.research.difficulty \
    nes_open_wram.nes --course jp/jp_uk --hole 1-18 --skill 3 --workers 16 \
    --output .cache/difficulty/solves/jp_uk/ 2>&1 | tee .cache/difficulty/solves/jp_uk.log
```

Each log ends with the course's hole-by-hole table against par. A hole whose play still
visits an unvalued spot at least `reach` times a hole prints a warning; on the holes
solved since the cleanup-round fix that came to 0.002-0.009 a hole, and 0.027 on the NES
US 12th.

## How it was built

### 1. Real holes under the model

Done:

- **`ClassifyProbePosition`** (`$EDEA`), as `HoleGround` in `golf/physics/terrain.py`.
  It matches the ROM at every pixel of all 54 NES Open holes on the vanilla ROM, and of
  all 90 Mario Open holes on a ROM with the `wram_expansion` patch.
- **Holes over 48 rows**: the 7 tall Mario Open holes (Hawaii 5, 14 and 18, U.K. 9, 14
  and 18, France 18, all par 5s) need the `wram_expansion` patch, which moves the terrain
  buffer and grows the tables indexed by `ScrollLimit`. `TerrainTables` reads those
  tables and buffers through the operands of the instructions that use them, so it reads
  either ROM as it plays, and `HoleGround` refuses a hole taller than the ROM's terrain
  buffer (48 rows vanilla, 60 expanded). The randomizer plays every Mario Open hole on
  such a ROM, so the solver does too: `nes_open_wram.nes` (gitignored), built with
  `uv run golf-patch nes_open_us.nes -p wram_expansion -o nes_open_wram.nes`. Its physics
  tables are the vanilla ROM's, so it shares the landing table's cache.
- **Views, trees and the bunker lip rule**: the view switch, the overhead tree probe, the
  behind-the-golfer projection and scene collision, the distance readout and the lip
  rule, in `golf/physics/shot.py`, `perspective.py` and `distance.py`.
- **The game itself as the oracle**: `golf/physics/nes.py` and `rom_game.py` play shots
  through the game's own frame loop. The model matches it on every register, every frame,
  for random shots from random lies on random holes (`tests/physics/test_game_rom.py`).
- **View mode confirmed in Mesen** (jdharms): `$80` from the swing, then `$00`.

### 2. The rest of the rules

Done:

- **The cup** (`UpdateBallAtCup`, bank 9 `$81C4`), as `golf/physics/cup.py`: the cup
  view (`$C0`) and the physics it slows, holing out, rim-ins, lip-outs and the flagstick
  (`docs/shot_physics.md`, **The cup**). `test_shot_at_the_flag` plays putts and chips
  that the model predicts will reach the cup, and pins the outcome each case must
  produce. A scratch sweep of 291 more such shots (25 rim-ins, 11 lip-outs, 8 holed, 2
  off the flagstick) matched the game on every frame.
- **Penalties and drops**, as `play_on` in `golf/physics/rules.py` (`docs/shot_physics.md`,
  **After the shot**). Water costs a stroke and drops the ball where it was on the last
  frame it was over anything but water or out of bounds, in the air or not. Out of bounds
  is stroke and distance. `RomGameShot.play(follow_through=True)` runs on into the play
  loop, and every shot in `test_game_rom.py` checks where the game puts the ball next,
  and the strokes it counts, against `play_on`.
- **Wind generation** as probabilities, in `golf/physics/wind.py`: 64 equally likely
  anchor pairs (not 176, because neighboring draws share bits), an independent pin, and
  an independent jitter per shot. `test_wind_rom.py` checks `InitHole` and
  `WindAdjustmentRoutine` against it.
- **Meter timing**, in `golf/physics/meter.py` (`docs/shot_physics.md`, **The swing**):
  the frames A is pressed on give the meter stops and `frames_to_impact` exactly,
  bounces, auto-stops and whiffs included. Every shot in `test_game_rom.py` checks it,
  and `test_meter_rom.py` covers every kind of backswing.

### 3. The solver

Done:

- **The player model**, `golf/difficulty/player.py`. The player means to press on the
  frame whose meter reading comes nearest each target of their `Intent`, the earlier of
  two equally near. The accuracy target is taken on the meter as the swing actually
  went, so a late power press does not also spoil the accuracy. `Skill` gives the
  errors. `outcomes(intent, position, hole, wind, skill)` plays every combination
  through `meter.swing`, the physics and `play_on`, in the wind the shot was dealt, from
  four RNG states chosen to stand for all of them (**The model's own approximations**). It returns `Result`s (where the next shot is played from,
  what this one cost, or `HOLED`) with probabilities. A whiff costs a stroke and leaves
  the ball where it was. The game hands over the putter on the green, so any other club
  there is refused. Tests are in `tests/integration/test_player_model_rom.py`.
- **`Flag.for_pin`**: a hole's four pin positions from its JSON, as `InitHole` computes
  them (checked against it in `test_wind_rom.py`).
- **Shared flights**, `golf/physics/flights.py`. Until first contact a shot does the same
  thing from any start, except where the ground reaches in: a tree, the view switch over
  the green, the edges of the playfield. After contact it does the same over the same
  terrain. So a `Flight` plays a shot once over plain fairway, and each roll once over
  the terrain it lands on, keeping the pixels each frame's probe looked at and a snapshot
  every 8 frames. From a real start, `finish` walks those pixels over the real ground,
  works out what the probe would have written, and plays for real only from the snapshot
  before the first frame the ground changes. The result is `simulate`'s, register for
  register (`tests/integration/test_flights_rom.py`, on every NES Open hole). The solver
  does not use them: with the player's errors few shots share a flight, and recording one
  costs more than playing it (see **Speed**). `Hole` takes a `Flights` to share, and
  plays every shot with `simulate` without one.
- **The landing table**, `golf/difficulty/landing.py`. For each lie a full swing is
  played from (fairway and tee alike, rough at either depth, sand at each of three
  depths), every intent's perfect execution with no wind, played over plain fairway at 8
  base aims: 15 clubs and the putter, 3 speeds, every distinct power stop, 3 hi/lo, 3
  spins (the putter only NORMAL, straight, no hi/lo), and 5 accuracy targets (straight,
  and 8 and 16 either side of `$30`). 76,388 intents a lie, 3.7 million shots, 13 minutes
  on 23 workers; cached by a hash of the ROM's physics tables in `.cache/difficulty/`
  (gitignored), 11 MB. A shot at another aim is the nearest base aim's rest turned:
  within 2 pixels for a straight shot, 8 for a curved one (without wind a flight turns
  almost exactly; the hook and slice terms are uneven, hence the base aims).
- **The solver**, `golf/difficulty/solver.py`, and `golf-difficulty`:
  - **States**: off the green, cells of a 4-pixel grid split by lie class, the first real
    spot the ball reaches in a cell standing for it; on the green, every pixel; the tee.
  - **The green**, `golf/difficulty/green.py`, solved whole. A putt from the green reads
    the RNG only in sand or water, and feels no wind: wind acts on air frames alone, a
    putt never leaves the ground, and on the green the probe writes the slope over the
    wind registers (`Ball.observe`). So its result depends only on the start pixel, the
    putt speed, the power stop and the aim, and the press frame decides the stop.
    `build_pixel` plays every speed, stop and aim within 48 steps of the pin line from
    every pixel of the green, once: 266 pixels and 3.8 million putts on the US 1st, 200 s
    on 16 workers, cached per ROM, hole and pin in `.cache/difficulty/`. The player's
    errors, press frames and aim steps either way, are other entries of the same table,
    so `GreenSolver` values the green by value iteration over lookups alone, at any
    skill, and every intent is scored under the errors, every unit of them: all three speeds, every aimed
    press and every aim that leaves room in the window for the aim errors. The solver
    values the green again at the start of each round against what the rest of the hole
    is then worth (putts that leave the green), and keeps each pixel's best 3 intents
    as its transitions. `test_green_rom.py` checks the table's outcome distributions
    against `outcomes()`, exactly, at skill 1 and 3, and that putts ignore wind. The
    table's putts are played from `RNG_STATES[0]`, which is part of its cache key.
  - **The screen**, off the green, in two passes. First, every table intent with a club
    in the vanilla bag, at every other aim within a quarter turn of the pin, is scored by
    the current value of where its perfect execution comes to rest, on a value map
    blurred by 2 pixels (water and out of bounds count a stroke and this spot again). The
    best intent of each club, speed, hi/lo and spin is kept. Second, the best 32 of those
    (`candidates`) are scored under the player's own errors: each intent's timing errors
    are played on the hole from this spot (`landing.scatter`, kept per worker), the aim
    errors turn that scatter, and the intent is scored at every aim again, on the map
    blurred by 1 pixel. The best 16 (`shortlist`) go on. The second pass is what makes
    the screen see skill: at skill 3 it moves the US 1st's drive from a fast swing to a
    medium one, which the first pass cannot tell apart. BACK 2 is not screened where it
    plays exactly as BACK 1: for the woods, and from the rough (it differs only when the
    first bounce is on the green, for clubs 4 and up, not from the rough).
  - **Racing**: when more than 6 intents (`race`) are new on a spot's shortlist, all are
    played roughly first, with one RNG state and 5 errors a draw (`RACE_POINTS`), scored
    on the current values, and only the best 6 are played exactly. The rough results only
    choose; no value comes from them.
  - **Rounds**: value the green; screen every state off it that is new, or that the best
    play visits at least 0.01 times a hole (`refresh_reach`); race and play what is new on
    the shortlists (all of it for a new state, the top 4, `refresh`, for one screened
    before); value iteration from the green outward; then follow the best play forward
    from the tee and add every state it visits at least 0.001 times a hole. A state not
    yet valued borrows from valued neighbors of its class, or a guess from its distance
    (`guess`). A state whose value has moved half a stroke since it was screened is
    screened again however rarely play reaches it (`rescreen_move`, **Loops** below).
    Value iteration stops at 300 sweeps a round (`ROUND_SWEEPS`), as values carry over,
    and the solve ends with a full run. Screens, races and plays are separate tasks in a
    process pool. It stops when no state is added, none is due to be screened again and
    the tee moves less than 0.002. After 12 rounds no state is added, and up to 6 more
    screen again only (`CLEANUP_ROUNDS`). From the 12th round on, when what an intent
    reaches can no longer be added, any intent the best play chooses that would visit an
    unvalued spot at least 0.001 times a hole is set aside and the values iterated
    again, until play stays among valued spots (`HoleSolver.stays_valued`), however
    early the intent was played. Without that, the tee could choose a short hop into
    spots valued only by `guess`, which is a stroke or more optimistic 300 pixels out on
    a long hole: five Mario Open solves ended with every visit from the tee on such
    spots. The solve warns when its play still visits an unvalued spot that often.
  - **Results on the US 1st** (par 4, 328 yards), pin 0, no wind, under PyPy with 16
    workers, the green's table already built:

    | Skill | Settings | Tee | Intents played | Rounds | Time |
    |-------|----------|-----|----------------|--------|------|
    | 1.0 | default | 2.928 | 1,228 | 9 | 152 s |
    | 1.0 | shortlist 32 | 2.924 | 1,048 | 6 | 131 s |
    | 1.0 | shortlist 32, no racing | 2.914 | 3,013 | 6 | 161 s |
    | 1.0 | wide (below) | 2.860 | 5,209 | 9 | 338 s |
    | 3.0 | default | 3.609 | 1,601 | 5 | 305 s |
    | 3.0 | 2 RNG states | 3.595 | 1,598 | 6 | 242 s |
    | 3.0 | 5 errors a draw | 3.623 | 1,406 | 5 | 153 s |
    | 3.0 | wide (below) | 3.578 | 7,863 | 6 | 1,127 s |

    Every value is exact play, so with the same model a missed intent can only raise the
    tee's value: lower is better, between runs that differ only in how they search. (2
    RNG states and 5 errors a draw change the model itself, so their gap is the size of
    that approximation plus the search's own noise, about 0.01-0.02, not a better or
    worse search.) Putts from inside about 7 yards
    are nearly always holed at skill 1.0, and approaches from 40-80 yards average about
    1.8-2.0 strokes, so the drive to there decides the hole.
  - **How widely to search.** The answer depends on how much the solver searches, by
    about 0.07 strokes a hole at skill 1 and 0.03 at skill 3 between the default and the
    wide settings (`--shortlist 32 --candidates 64 --refresh 8 --race 0 --refresh-reach 0
    --tolerance 0.0005`). The screen ranks approach shots badly: near the green, intents
    differ by a tenth of a stroke, less than the screen's own error, and the wide run's
    chosen approaches were mostly 13th-25th on its screen, often another club from the
    default run's (a 2-iron where the default chose a 3-wood, 0.13 strokes better). The
    wide run's later rounds keep finding them, as each re-screen against slightly moved
    values proposes intents not yet played: the tee went from 2.907 to 2.860 over rounds
    3-8 at skill 1. A longer shortlist, more candidates and racing each closed only part
    of the gap on their own. `--output` keeps every intent played from each state with
    its screen rank and score, and the CLI prints how the screen ranked the chosen ones.
    These runs predate the RNG states and the green's errors below.
  - **Rounds of holes**: `golf-difficulty --hole 1-18` solves each hole in turn and prints
    the round's total; `--output` is then a directory of one file per hole.
- **The model's own approximations**, measured on real play with `HoleSolver.recheck`
  (`--recheck-error-points`, `--recheck-rng-states`): the policy a solve chose is played
  again under a finer model, at every state it visits at least 0.001 times a hole, with
  the green solved again, and valued with the policy held fixed. The gap to the solve's
  own value is how far its model flattered its own choices. At the solve's own settings
  it gives back the solve's value exactly. On the US 1st, pin 0:
  - **The RNG sample** was the largest. The old four states (`$0001 $4E6C $9A3B $D5C2`)
    drew the rough and bunker power variance at −0.53, +0.10, +0.14 and +0.74 of its
    reach, the same four on every shot: long on average, and never short by more than
    about half. At skill 3 their policy played from 32 evenly spread states cost 0.033
    strokes more, all of it from states in the rough (+0.035); tee and fairway starts
    moved −0.002. `player.rng_sample(n)` picks states whose draws sit at the middles of
    `n` equal slices, with both coin flips balanced (the fairway's, read before the draw
    and after it) and low bytes spread (a landing in sand reads its depth from it). 4, 8,
    32 and 64 such states agreed within 0.003, so `RNG_STATES` is now `rng_sample(4)`, at
    no extra cost. Solving again with them gave 3.639, so re-choosing the policy won back
    only 0.003 of the 0.033: the old states' error was bias, not exploitation.
  - **The error grid**: at skill 4 (a stride of 3 units) the old model flattered its
    policy by 0.071 strokes against every unit played, 0.068 of it on the green alone.
    The green is lookups, so `GreenSolver` now plays every unit. Off the green the stride
    grid stays: with the green at every unit, the US 1st solves to 3.661 at skill 3 and
    4.093 at skill 4, and every unit played moves those policies by −0.009 and +0.014,
    no steady direction, so no sign of the solver leaning on the grid.
  - **Turned down: a phase per RNG state.** Taking each RNG state's errors at another
    offset of the stride, so that between them every unit is played at no extra cost,
    made the model pessimistic instead: 0.019 at skill 3 and 0.010 at skill 4 against
    every unit, with a slightly worse policy (3.656 played at every unit, where the old
    grid's policy gave 3.647). A shifted grid never plays the intended press, and its
    outermost points fall past the 2σ reach.
  - **The screen against exact play**, by distance to the pin (`--output`, skill 3,
    weighted by visits; the screen's score leaves out the shot's own stroke):

    | Yards | Screen − exact | Mean error | Best played − screen's first |
    |-------|----------------|------------|------------------------------|
    | 0-40 | +0.21 | 0.22 | 0.040 |
    | 40-80 | +0.05 | 0.06 | 0.004 |
    | 80-120 | −0.03 | 0.06 | 0.011 |
    | 120-160 | −0.12 | 0.13 | 0.023 |
    | 160-200 | −0.09 | 0.09 | 0.017 |

    The screen is pessimistic about short shots around the green, optimistic about
    approaches from 120 yards and more, and good from 40-120 yards. Around the green it
    proposed only woods from the fringe, which do chip well (speedrunners play them
    too, jdharms), but played exactly, short irons and wedges it never proposed beat
    them by 0.08 and 0.13 strokes at the U.K. 18th's and 4th's most visited fringe
    spots. The table's rests are over plain fairway; a chip that stops on the green
    rolls farther there, and backspin bites only on the green, for clubs 4 and up. So
    from spots within 40 pixels of the pin, the first pass now plays on the hole every
    intent whose fairway rest stops within 24 of the pin (`NEAR_PIN`): the U.K. 18th
    solved 0.038 lower and the 4th 0.007, for about twice the screening on the 18th.
    Playing only the straight ones on the hole kept 0.022 of the 18th's 0.038.
- **Loops the solver builds.** The US 12th would not settle: over 12 rounds the tee
  swung between 4.9 and 5.8, and one round took 20 minutes of value iteration. Spots
  first screened against their neighbors' borrowed values chose short hops onto one
  another, and once played, each spot's value was a stroke more than the next's: no
  finite answer, so value iteration climbed to its cap, 44 spots reached 24 strokes,
  and their neighbors borrowed from them. Such spots are rarely visited, so they were
  never screened again. Screening a spot again once its value moves, and not stopping
  while any is due, settled the 12th at 5.412 in 16 rounds, highest value 6.5 (5.793
  since **The cleanup-round fix**). No
  U.K. or Japan hole had any such spot. Turned down on the way: escape shots round the
  whole circle (scored on the same stale values, they hop too), policy iteration (under
  PyPy a dense solve of 1,000 states takes 2-3 s, and filling the matrix is slow), and
  holding the borrowed values fixed within a round (no difference).
- **Trees in the behind-the-golfer view, measured** on eight U.K. holes (jdharms picked
  the ones where trees shape the tee shot or which parts of the fairway are good). For
  every off-green spot the solved play visits at least 0.002 times a hole, the best 8
  intents played there, the chosen one among them, were played again with the game's own scene
  for each start and aim, and valued one shot ahead against the solve's values; the sum
  over visits is the change to the tee's value.

  | Hole | No trees | Chosen intents kept | Best of those played at each spot |
  |------|----------|---------------------|-----------------------------------|
  | 1st | 4.216 | +0.147 | +0.050 |
  | 2nd | 3.760 | +0.067 | +0.012 |
  | 5th | 4.290 | +0.038 | +0.008 |
  | 10th | 3.802 | +0.076 | +0.029 |
  | 14th | 4.187 | +1.285 | +0.040 |
  | 15th | 4.098 | 0.000 | 0.000 |
  | 16th | 4.879 | +0.258 | +0.077 |
  | 18th | 4.489 | +0.004 | +0.001 |

  Trees change strategy far more than score: about 0.2 strokes over the eight holes once
  each spot plays the best intent it already had. The 14th's fast high drive meets trees
  close to the tee (penalties from 23% to 54%), where a medium 2W aimed wider and curved
  back loses 0.03. The rest is approaches whose line crosses trees, on the 1st, 10th and
  16th, and the west side of the 2nd's forest; most forest spots play high woods that
  clear the trees. The 15th's clumps are out of bounds, so play already avoids them.

  How it was done, in scratch scripts not kept: `RomGameShot`'s machine, with the ball
  and aim poked in and a breakpoint at `AFTER_SCENE_BUILT` that snapshots WRAM as a
  `PerspectiveScene` and stops (0.2 s a scene under PyPy); `player.shot_result` replaced
  by one that plays `ShotInFlight(..., scene=...)` with the scene for the shot's start and
  aim. Checked against the game: 324 shots from twelve of the 2nd's forest spots through
  `RomGameShot`, where the model with the game's scene matched all 324 and the trees
  changed 60. Only vanilla holes load this way, so Mario Open holes would need writing
  into a ROM first (`golf-write`, untried) or the scene builder ported.
- **Wind on two holes.** Played on the drive only, with the approach valued at no wind,
  the U.K. 7th's best drive is a slow, low 1W with backspin up the fairway (4.24) in
  calm, headwinds and crosswinds, and a fast, high 1W due north only in a strong
  tailwind (`$00`, speed 9: 4.19 against 4.22), when it carries past the lake. At skill
  2 in calm the fast drive is already level. On the Japan 17th a good NE or E wind lets
  the drive carry the river (jdharms). Wind changes which shot is right on such holes,
  which averaging it out hides.
Assumptions the results rest on:

- Errors reach 2 standard deviations (jdharms), so one intent at skill 1.0 is 5 × 5
  timings × 5 aims × 4 RNG states: 500 shots. The three are in the ratio 1 : 1 : 1 (frames,
  frames, aim steps).
- **About 7 errors a draw off the green** (`ERROR_POINTS` in `player.py`,
  `error_points` in the solver's settings). Past 7 whole units (skill above 1.5), every
  2nd, 3rd, ... unit stands for the units nearest it, which keeps the spread within a
  few percent and a shot's cost near 7 × 7 × 7 × 4 = 1,372 at any skill, where every
  unit would be 8,788 at skill 3 and 37,044 at skill 5. Measured at about ±0.01 strokes
  a hole at skills 3 and 4 (**The model's own approximations**). The green plays every
  unit.
- The physics RNG is 4 states, standing for all 65,534 (`rng_sample`; `rng_states` plays
  another number). A putt from the green is played from one of them: it reads the RNG
  only if it runs into sand or water. The landing table's perfect shots and racing use
  one state each (`$0001`, and `rng_sample(1)`); they only choose.
- **The green's table** plays aims within 48 steps of the pin line, and follows late
  presses 40 frames past the latest a player aims for; intents stay far enough inside
  the window for their aim errors. A putt starts from the pixel the last one stopped on,
  as every state does.
- **No trees in the behind-the-golfer view**: `outcomes()` has no scene to collide with
  until the scene builder is ported (**Future work**). Overhead-view trees work. On eight U.K.
  holes this costs about 0.2 strokes, and the wrong drive on the 14th (**Trees in the
  behind-the-golfer view, measured**).
- **The solver's approximations**: the 4-pixel cells (a cell's value is its first spot's),
  the screen and racing (only what they keep is played; every value is exact physics),
  the 5 accuracy targets, every other aim, and reach 0.001. Each is a setting of `Settings`
  or a constant in `solver.py` and `landing.py`, to vary in the sensitivity runs.

**Speed**, measured on the US 1st:

- `simulate` takes about 5 ms for a full shot under CPython (about 380 frames at 14 µs),
  under 1 ms for a putt. Under PyPy, warm, a full swing takes 0.15-0.25 ms and a putt
  0.6-1 ms (a putt across or off the green rolls a long way), with exactly the same
  outcomes. PyPy's JIT takes several seconds to warm up in each worker.
- **`Flights` cost more than they save in the solver**: at skill 3 real intents took
  2.3-3.4 ms a shot with `Flights` against 0.15-0.23 ms with plain `simulate`. Each
  timing and aim error is a flight of its own, shared only across the RNG states, and
  recording one costs more than playing it. So `Hole` plays with `simulate` unless given
  `Flights`.
- The machine is 12 cores with two threads each, so 16 workers share the cores: a
  worker's times run about twice the single-process ones.
- A spot's screen takes 0.5-0.9 s under PyPy on one process, about 3-5 s of worker time
  in the solver (shared cores, warm-up). With the default settings screening is about a
  third of the work; with the wide settings, every state screened every round, it is
  over half.
- **A U.K. hole** (the 1st, par 4, and the 13th, par 5) takes 450-710 s on 16 workers
  at skill 3 or 4, after its green's table. The main process peaks at 1.7-1.8 GB at
  skill 3 and 2.3-2.7 GB at skill 4 (800 against 1,000-1,100 states); the workers
  together at 4-8 GB resident, much of it shared. Free memory never fell below 8.8 GB
  of 15.6.
- The green's table is 200 s on 16 workers, once per hole and pin. Valuing the green
  takes 1-4 s a round in the main process.
- **Reading one element of a numpy array costs microseconds under PyPy** (it goes
  through cpyext). Value iteration read each borrowed value that way, hundreds of
  thousands a sweep, which made some rounds' iteration take minutes; the borrowed grids
  are now lists (`HoleSolver._borrowed`), about 5 times faster. Code in the main loop
  should keep scalars out of arrays.
- `Flight.finish` takes 0.11 ms for a shot that lands and rolls out on uniform
  terrain, 1-1.5 ms for one whose roll runs onto something else, and 2-3 ms for one that
  comes down over the green. Recording a flight costs about as much as `simulate`.

**Running under PyPy.** The project needs 3.12, so PyPy 3.11 gets its own environment
with the few packages the solver needs, and the repo on `PYTHONPATH`. The solver and
everything it imports is kept 3.11-clean (`tests/meta/test_pypy_ready.py`). A worker
peaks around 400 MB; 16 workers fit the 15 GB machine easily. The workers are spawned,
not forked: forked from a main process that had solved a few holes, each worker's
collector touched and so copied the parent's heap, and by the U.K. 4th 16 workers held
24 GB resident.

```bash
uv venv --python pypy@3.11 .cache/pypy
VIRTUAL_ENV=.cache/pypy uv pip install numpy py65 pillow
PYTHONPATH=. .cache/pypy/bin/python -u -m tools.research.difficulty nes_open_us.nes \
    --course us --hole 1 --workers 16 --output us01.json
```

## Future work

None of this is scheduled (ADR 0012).

- **The other three pins**, starting with the holes whose place against par is closest
  to a boundary the randomizer draws: half a stroke over par, and the expert holes'
  line (**Results**).
- **Expert holes weighted toward the 9th and 18th** (suggested by one of jdharms's
  league-mates on seeing the expert holes). The randomizer's `expert_cap` rule limits
  the expert holes on each nine (`docs/manifest.md`, **The expert cap**) and spreads them
  evenly over a nine's slots.
- **A per-nine ceiling on predicted strokes over par**, as a rule beside the cap. It
  needs each hole's `over_par` in curation, which holds only the `expert` tag.
- **Emulator spot checks** (jdharms, in Mesen): a perfect medium 1W drive (235 carry, 268
  total), and the `$40` wind distortion (`docs/shot_physics.md`). What they check now is
  the emulated machine: that nothing it leaves out (the PPU, sprite 0, IRQs) changes a
  shot.
- **The scene builder** (bank 9 `$8829`), ported, so the solver sees behind-the-golfer
  trees and chooses the right drive on holes like the U.K. 14th. It samples a 20 × 64
  grid (`docs/shot_physics.md`, **Not modeled yet**) and builds the maps in `$9C1C`,
  `$8EE4`, `$9CB8`, `$A30B`, `$8FE4`, `$99B2`, `$91C9` (282 instructions, and the RNG)
  and `$9A79`, some of which only draw. The scenes `RomGameShot` captures are the oracle.
  Shared flights assume no scene; with one, `Flight` would have to check the scene's
  collisions for each start too.
- **Wind**, at least for holes where it changes the best play (**Wind on two holes**).
  A seed knows each hole's wind (`seed_holes`), so the randomizer could use it directly.
- **A better screen.** It misses intents: on the US 16th the solve chose a drive worth
  3.721, where U.K. 2's drive, played there, is worth 3.619 (**Results**). It is 0.1
  optimistic about approaches from 120 yards out (**The model's own approximations**).
- **A better `guess`.** A spot never valued is guessed from its distance alone, a stroke
  or more optimistic 300 pixels out on a long hole. The solver no longer chooses on such
  guesses past its last adding round, but in earlier rounds they send it chasing short
  hops that value iteration then disproves, which costs rounds on long holes. A guess fit
  to the values already found would cost fewer.
- **Calibration and validation**:
  - Solve for the skill whose expected U.K. round is 72, and predict the US, Japan and
    Mario Open courses with it. The expected order is Japan, US, U.K., then Mario Open's
    hardest.
  - Check against real play: per-hole averages from the scorecard QR submissions and the
    site's rounds, with their pins and winds. Ranking the holes the same way real scores
    do matters more than matching the scores.
  - Sensitivity: vary the ratios between the three errors, the error reach and the RNG
    sample, and see whether the hole rankings hold.
- **Output from the value function**: strokes-to-hole and difficulty heatmaps over the
  hole renders (`golf/rendering/`), against **the baseline** (the same solver on uniform
  fairway with a cup), and a per-hole difficulty score for the randomizer catalog.

## Results

Every hole at skill 3, from its first pin, with no wind and the default `Settings`, by
the solver as it stood at ADR 0012: NES Open holes on the vanilla ROM, Mario Open holes on
`nes_open_wram.nes` (phase 1). The tables are `golf-difficulty-report`'s (**Data**).
"Over par" is expected strokes from the tee minus par; "Rare visits" is how often a hole's
best play visits spots too rare to value, which borrow their values (**The solver**).

### Courses

| Course | Out | In | Round | Over par | Par 3s | Par 4s | Par 5s | Holes over +0.5 | Spearman vs handicap |
|---|---|---|---|---|---|---|---|---|---|
| NES Japan | 36.57 | 36.81 | 73.38 | +1.38 | -0.01 | +0.17 | -0.07 | 1 | 0.68 |
| NES US | 35.82 | 38.20 | 74.01 | +2.01 | +0.20 | +0.04 | +0.20 | 2 | 0.61 |
| NES U.K. | 35.93 | 37.26 | 73.19 | +1.19 | -0.00 | +0.17 | -0.13 | 1 | 0.54 |
| Mario Japan | 36.57 | 36.84 | 73.41 | +1.41 | -0.06 | +0.16 | +0.02 | 0 | 0.13 |
| Mario Australia | 36.89 | 38.82 | 75.71 | +3.71 | +0.10 | +0.24 | +0.22 | 3 | 0.45 |
| Mario France | 37.88 | 39.75 | 77.63 | +5.63 | +0.26 | +0.22 | +0.59 | 3 | 0.53 |
| Mario Hawaii | 40.37 | 46.26 | 86.63 | +14.63 | +0.43 | +0.67 | +1.55 | 12 | 0.34 |
| Mario U.K. | 45.73 | 48.71 | 94.44 | +22.44 | +1.36 | +1.07 | +1.58 | 16 | 0.41 |

### Every hole against par

| # | Hole | Par | Yards | Handicap | Expected | Over par | Rare visits |
|---|---|---|---|---|---|---|---|
| 1 | NES US 2 | 5 | 481 | 13 | 4.454 | -0.546 | 0.078 |
| 2 | NES Japan 12 | 5 | 535 | 6 | 4.637 | -0.363 | 0.098 |
| 3 | NES US 1 | 4 | 328 | 17 | 3.670 | -0.330 | 0.036 |
| 4 | NES U.K. 6 | 4 | 357 | 7 | 3.696 | -0.304 | 0.048 |
| 5 | Mario France 5 | 4 | 350 | 16 | 3.696 | -0.304 | 0.048 |
| 6 | NES U.K. 9 | 5 | 528 | 13 | 4.737 | -0.263 | 0.122 |
| 7 | Mario Japan 8 | 5 | 524 | 6 | 4.737 | -0.263 | 0.122 |
| 8 | NES U.K. 2 | 4 | 393 | 12 | 3.760 | -0.240 | 0.053 |
| 9 | Mario Australia 6 | 4 | 386 | 14 | 3.760 | -0.240 | 0.053 |
| 10 | NES Japan 10 | 4 | 350 | 15 | 3.763 | -0.237 | 0.042 |
| 11 | NES U.K. 10 | 4 | 325 | 11 | 3.802 | -0.198 | 0.021 |
| 12 | NES U.K. 13 | 5 | 571 | 18 | 4.827 | -0.173 | 0.120 |
| 13 | NES US 4 | 3 | 154 | 16 | 2.843 | -0.157 | 0.005 |
| 14 | Mario Japan 13 | 5 | 581 | 1 | 4.860 | -0.140 | 0.111 |
| 15 | NES Japan 3 | 5 | 534 | 16 | 4.863 | -0.137 | 0.089 |
| 16 | NES Japan 6 | 3 | 166 | 11 | 2.871 | -0.129 | 0.014 |
| 17 | Mario Japan 5 | 3 | 171 | 14 | 2.874 | -0.126 | 0.010 |
| 18 | NES US 17 | 4 | 435 | 12 | 3.878 | -0.122 | 0.053 |
| 19 | Mario Japan 15 | 4 | 424 | 13 | 3.878 | -0.122 | 0.053 |
| 20 | NES U.K. 16 | 5 | 571 | 16 | 4.879 | -0.121 | 0.093 |
| 21 | Mario France 10 | 5 | 566 | 5 | 4.881 | -0.119 | 0.113 |
| 22 | Mario Australia 9 | 5 | 573 | 6 | 4.902 | -0.098 | 0.096 |
| 23 | Mario Hawaii 7 | 3 | 212 | 18 | 2.922 | -0.078 | 0.013 |
| 24 | NES Japan 7 | 5 | 535 | 10 | 4.929 | -0.071 | 0.130 |
| 25 | Mario France 9 | 4 | 338 | 14 | 3.937 | -0.063 | 0.038 |
| 26 | NES U.K. 4 | 3 | 221 | 10 | 2.954 | -0.046 | 0.022 |
| 27 | Mario Japan 3 | 3 | 220 | 8 | 2.954 | -0.046 | 0.022 |
| 28 | NES U.K. 12 | 3 | 162 | 14 | 2.961 | -0.039 | 0.006 |
| 29 | Mario Japan 11 | 3 | 164 | 9 | 2.961 | -0.039 | 0.006 |
| 30 | Mario Japan 16 | 3 | 174 | 17 | 2.964 | -0.036 | 0.013 |
| 31 | Mario France 8 | 3 | 198 | 10 | 2.966 | -0.034 | 0.005 |
| 32 | NES Japan 13 | 3 | 160 | 18 | 2.971 | -0.029 | 0.006 |
| 33 | NES US 3 | 4 | 446 | 7 | 3.980 | -0.020 | 0.069 |
| 34 | Mario Japan 12 | 4 | 433 | 15 | 3.980 | -0.020 | 0.069 |
| 35 | NES Japan 4 | 3 | 202 | 13 | 2.982 | -0.018 | 0.013 |
| 36 | NES U.K. 8 | 3 | 201 | 17 | 2.994 | -0.006 | 0.011 |
| 37 | NES US 14 | 4 | 400 | 4 | 4.008 | +0.008 | 0.067 |
| 38 | NES US 13 | 4 | 420 | 15 | 4.011 | +0.011 | 0.079 |
| 39 | Mario Australia 4 | 4 | 417 | 8 | 4.025 | +0.025 | 0.060 |
| 40 | Mario Australia 8 | 4 | 397 | 18 | 4.031 | +0.031 | 0.058 |
| 41 | Mario Hawaii 6 | 4 | 400 | 8 | 4.033 | +0.033 | 0.033 |
| 42 | NES U.K. 3 | 5 | 550 | 6 | 5.043 | +0.043 | 0.056 |
| 43 | NES US 6 | 4 | 400 | 11 | 4.045 | +0.045 | 0.054 |
| 44 | NES Japan 2 | 4 | 392 | 14 | 4.050 | +0.050 | 0.082 |
| 45 | Mario Japan 4 | 5 | 547 | 4 | 5.058 | +0.058 | 0.082 |
| 46 | NES Japan 1 | 4 | 400 | 17 | 4.062 | +0.062 | 0.108 |
| 47 | NES US 5 | 4 | 392 | 8 | 4.063 | +0.063 | 0.056 |
| 48 | Mario Australia 2 | 3 | 171 | 16 | 3.066 | +0.066 | 0.004 |
| 49 | Mario Australia 15 | 3 | 194 | 17 | 3.071 | +0.071 | 0.013 |
| 50 | NES U.K. 17 | 3 | 196 | 5 | 3.082 | +0.082 | 0.012 |
| 51 | NES US 10 | 3 | 217 | 14 | 3.084 | +0.084 | 0.018 |
| 52 | NES U.K. 15 | 4 | 410 | 8 | 4.098 | +0.098 | 0.076 |
| 53 | Mario Japan 6 | 4 | 397 | 12 | 4.098 | +0.098 | 0.076 |
| 54 | Mario Australia 10 | 3 | 216 | 7 | 3.111 | +0.111 | 0.005 |
| 55 | NES US 18 | 5 | 571 | 5 | 5.125 | +0.125 | 0.106 |
| 56 | Mario Australia 13 | 5 | 566 | 5 | 5.125 | +0.125 | 0.106 |
| 57 | Mario France 17 | 4 | 440 | 17 | 4.125 | +0.125 | 0.041 |
| 58 | NES Japan 5 | 4 | 410 | 8 | 4.127 | +0.127 | 0.060 |
| 59 | NES Japan 16 | 3 | 192 | 9 | 3.136 | +0.136 | 0.011 |
| 60 | Mario Japan 9 | 4 | 393 | 18 | 4.137 | +0.137 | 0.080 |
| 61 | Mario Australia 14 | 4 | 388 | 15 | 4.140 | +0.140 | 0.036 |
| 62 | NES Japan 8 | 4 | 464 | 12 | 4.140 | +0.140 | 0.082 |
| 63 | Mario Australia 16 | 4 | 459 | 9 | 4.142 | +0.142 | 0.069 |
| 64 | Mario Australia 7 | 3 | 200 | 10 | 3.146 | +0.146 | 0.027 |
| 65 | NES Japan 15 | 4 | 410 | 5 | 4.152 | +0.152 | 0.065 |
| 66 | NES US 7 | 3 | 167 | 18 | 3.153 | +0.153 | 0.009 |
| 67 | Mario France 4 | 3 | 200 | 18 | 3.157 | +0.157 | 0.012 |
| 68 | Mario Australia 11 | 4 | 405 | 11 | 4.162 | +0.162 | 0.075 |
| 69 | NES US 9 | 4 | 410 | 10 | 4.180 | +0.180 | 0.057 |
| 70 | Mario Australia 1 | 4 | 400 | 12 | 4.180 | +0.180 | 0.057 |
| 71 | NES U.K. 14 | 4 | 403 | 4 | 4.187 | +0.187 | 0.008 |
| 72 | Mario Japan 10 | 4 | 417 | 11 | 4.196 | +0.196 | 0.063 |
| 73 | Mario Japan 1 | 4 | 412 | 16 | 4.213 | +0.213 | 0.079 |
| 74 | NES U.K. 1 | 4 | 418 | 15 | 4.216 | +0.216 | 0.076 |
| 75 | Mario France 14 | 4 | 421 | 11 | 4.231 | +0.231 | 0.127 |
| 76 | NES US 11 | 4 | 421 | 9 | 4.231 | +0.231 | 0.069 |
| 77 | NES Japan 14 | 4 | 464 | 4 | 4.235 | +0.235 | 0.099 |
| 78 | Mario Japan 17 | 4 | 445 | 5 | 4.235 | +0.235 | 0.099 |
| 79 | Mario France 15 | 4 | 424 | 13 | 4.238 | +0.238 | 0.069 |
| 80 | NES U.K. 7 | 4 | 428 | 1 | 4.239 | +0.239 | 0.024 |
| 81 | Mario Japan 7 | 4 | 414 | 2 | 4.239 | +0.239 | 0.024 |
| 82 | Mario France 7 | 5 | 645 | 2 | 5.258 | +0.258 | 0.200 |
| 83 | Mario Japan 2 | 4 | 405 | 10 | 4.259 | +0.259 | 0.060 |
| 84 | NES Japan 18 | 5 | 605 | 2 | 5.286 | +0.286 | 0.115 |
| 85 | NES Japan 11 | 4 | 368 | 7 | 4.287 | +0.287 | 0.051 |
| 86 | Mario France 6 | 4 | 357 | 12 | 4.287 | +0.287 | 0.051 |
| 87 | NES U.K. 5 | 4 | 431 | 2 | 4.290 | +0.290 | 0.076 |
| 88 | Mario U.K. 7 | 3 | 227 | 10 | 3.326 | +0.326 | 0.019 |
| 89 | NES Japan 17 | 4 | 432 | 3 | 4.341 | +0.341 | 0.114 |
| 90 | Mario Japan 14 | 4 | 404 | 7 | 4.341 | +0.341 | 0.114 |
| 91 | NES US 15 | 4 | 428 | 6 | 4.348 | +0.348 | 0.056 |
| 92 | Mario Hawaii 2 | 4 | 440 | 14 | 4.352 | +0.352 | 0.083 |
| 93 | Mario Australia 3 | 5 | 609 | 2 | 5.359 | +0.359 | 0.098 |
| 94 | Mario Hawaii 3 | 3 | 240 | 12 | 3.360 | +0.360 | 0.017 |
| 95 | Mario France 11 | 4 | 440 | 9 | 4.385 | +0.385 | 0.092 |
| 96 | Mario France 1 | 4 | 438 | 8 | 4.400 | +0.400 | 0.077 |
| 97 | Mario France 16 | 3 | 235 | 7 | 3.417 | +0.417 | 0.009 |
| 98 | Mario Australia 5 | 4 | 440 | 4 | 4.424 | +0.424 | 0.064 |
| 99 | NES US 8 | 5 | 560 | 1 | 5.426 | +0.426 | 0.101 |
| 100 | Mario Japan 18 | 5 | 564 | 3 | 5.426 | +0.426 | 0.101 |
| 101 | Mario France 2 | 4 | 452 | 4 | 4.440 | +0.440 | 0.117 |
| 102 | Mario U.K. 3 | 4 | 452 | 12 | 4.465 | +0.465 | 0.155 |
| 103 | Mario Hawaii 15 | 4 | 452 | 13 | 4.475 | +0.475 | 0.126 |
| 104 | NES U.K. 18 | 4 | 460 | 9 | 4.489 | +0.489 | 0.086 |
| 105 | Mario France 13 | 4 | 440 | 3 | 4.491 | +0.491 | 0.074 |
| 106 | Mario Hawaii 8 | 5 | 678 | 4 | 5.495 | +0.495 | 0.207 |
| 107 | Mario Australia 17 | 5 | 619 | 1 | 5.507 | +0.507 | 0.106 |
| 108 | Mario France 12 | 3 | 224 | 15 | 3.513 | +0.513 | 0.010 |
| 109 | NES Japan 9 | 4 | 418 | 1 | 4.543 | +0.543 | 0.050 |
| 110 | Mario Hawaii 16 | 4 | 455 | 3 | 4.564 | +0.564 | 0.117 |
| 111 | Mario U.K. 15 | 3 | 251 | 17 | 3.588 | +0.588 | 0.015 |
| 112 | Mario Hawaii 17 | 3 | 238 | 7 | 3.600 | +0.600 | 0.008 |
| 113 | Mario Australia 18 | 4 | 468 | 3 | 4.626 | +0.626 | 0.059 |
| 114 | Mario Hawaii 9 | 4 | 412 | 6 | 4.643 | +0.643 | 0.092 |
| 115 | Mario Hawaii 4 | 4 | 405 | 10 | 4.662 | +0.662 | 0.063 |
| 116 | NES US 16 | 3 | 230 | 2 | 3.721 | +0.721 | 0.010 |
| 117 | Mario France 3 | 5 | 624 | 6 | 5.737 | +0.737 | 0.108 |
| 118 | NES US 12 | 5 | 642 | 3 | 5.793 | +0.793 | 0.136 |
| 119 | Mario Hawaii 1 | 4 | 435 | 16 | 4.846 | +0.846 | 0.071 |
| 120 | Mario Hawaii 12 | 3 | 231 | 15 | 3.854 | +0.854 | 0.011 |
| 121 | Mario U.K. 11 | 4 | 471 | 5 | 4.922 | +0.922 | 0.097 |
| 122 | NES U.K. 11 | 4 | 424 | 3 | 4.936 | +0.936 | 0.046 |
| 123 | Mario Australia 12 | 4 | 424 | 13 | 4.936 | +0.936 | 0.046 |
| 124 | Mario U.K. 17 | 4 | 450 | 7 | 4.938 | +0.938 | 0.089 |
| 125 | Mario U.K. 12 | 4 | 464 | 11 | 4.985 | +0.985 | 0.031 |
| 126 | Mario U.K. 16 | 4 | 464 | 13 | 5.005 | +1.005 | 0.060 |
| 127 | Mario U.K. 4 | 5 | 576 | 6 | 6.019 | +1.019 | 0.067 |
| 128 | Mario U.K. 2 | 3 | 226 | 16 | 4.022 | +1.022 | 0.004 |
| 129 | Mario U.K. 6 | 4 | 428 | 18 | 5.034 | +1.034 | 0.056 |
| 130 | Mario Hawaii 11 | 4 | 456 | 11 | 5.037 | +1.037 | 0.085 |
| 131 | Mario Hawaii 10 | 4 | 464 | 9 | 5.041 | +1.041 | 0.059 |
| 132 | Mario Hawaii 13 | 4 | 416 | 17 | 5.043 | +1.043 | 0.046 |
| 133 | Mario Hawaii 5 | 5 | 773 | 2 | 6.055 | +1.055 | 0.263 |
| 134 | Mario U.K. 13 | 4 | 447 | 15 | 5.137 | +1.137 | 0.062 |
| 135 | Mario U.K. 5 | 4 | 440 | 8 | 5.204 | +1.204 | 0.086 |
| 136 | Mario Hawaii 18 | 5 | 750 | 5 | 6.252 | +1.252 | 0.175 |
| 137 | Mario U.K. 1 | 4 | 470 | 14 | 5.437 | +1.437 | 0.040 |
| 138 | Mario France 18 | 5 | 700 | 1 | 6.471 | +1.471 | 0.106 |
| 139 | Mario U.K. 14 | 5 | 778 | 3 | 6.499 | +1.499 | 0.274 |
| 140 | Mario U.K. 8 | 4 | 464 | 4 | 5.557 | +1.557 | 0.035 |
| 141 | Mario U.K. 9 | 5 | 766 | 2 | 6.665 | +1.665 | 0.155 |
| 142 | Mario U.K. 18 | 5 | 838 | 1 | 7.122 | +2.122 | 0.169 |
| 143 | Mario Hawaii 14 | 5 | 762 | 1 | 8.398 | +3.398 | 0.067 |
| 144 | Mario U.K. 10 | 3 | 200 | 9 | 6.515 | +3.515 | 0.002 |

### Expert holes

The 21 Mario Open holes that play worse against par than every NES Open hole (the worst is NES U.K. 11, +0.936), hardest first.

| Hole | Par | Yards | Expected | Over par |
|---|---|---|---|---|
| Mario U.K. 10 | 3 | 200 | 6.515 | +3.515 |
| Mario Hawaii 14 | 5 | 762 | 8.398 | +3.398 |
| Mario U.K. 18 | 5 | 838 | 7.122 | +2.122 |
| Mario U.K. 9 | 5 | 766 | 6.665 | +1.665 |
| Mario U.K. 8 | 4 | 464 | 5.557 | +1.557 |
| Mario U.K. 14 | 5 | 778 | 6.499 | +1.499 |
| Mario France 18 | 5 | 700 | 6.471 | +1.471 |
| Mario U.K. 1 | 4 | 470 | 5.437 | +1.437 |
| Mario Hawaii 18 | 5 | 750 | 6.252 | +1.252 |
| Mario U.K. 5 | 4 | 440 | 5.204 | +1.204 |
| Mario U.K. 13 | 4 | 447 | 5.137 | +1.137 |
| Mario Hawaii 5 | 5 | 773 | 6.055 | +1.055 |
| Mario Hawaii 13 | 4 | 416 | 5.043 | +1.043 |
| Mario Hawaii 10 | 4 | 464 | 5.041 | +1.041 |
| Mario Hawaii 11 | 4 | 456 | 5.037 | +1.037 |
| Mario U.K. 6 | 4 | 428 | 5.034 | +1.034 |
| Mario U.K. 2 | 3 | 226 | 4.022 | +1.022 |
| Mario U.K. 4 | 5 | 576 | 6.019 | +1.019 |
| Mario U.K. 16 | 4 | 464 | 5.005 | +1.005 |
| Mario U.K. 12 | 4 | 464 | 4.985 | +0.985 |
| Mario U.K. 17 | 4 | 450 | 4.938 | +0.938 |

### New holes at NES Open level

The 34 Mario Open holes that are not expert holes and share no family with a NES Open hole (`data/catalog/curation.json`), easiest first.

| Hole | Par | Yards | Expected | Over par |
|---|---|---|---|---|
| Mario France 10 | 5 | 566 | 4.881 | -0.119 |
| Mario Hawaii 7 | 3 | 212 | 2.922 | -0.078 |
| Mario France 9 | 4 | 338 | 3.937 | -0.063 |
| Mario France 8 | 3 | 198 | 2.966 | -0.034 |
| Mario Australia 4 | 4 | 417 | 4.025 | +0.025 |
| Mario Hawaii 6 | 4 | 400 | 4.033 | +0.033 |
| Mario Japan 4 | 5 | 547 | 5.058 | +0.058 |
| Mario France 17 | 4 | 440 | 4.125 | +0.125 |
| Mario Australia 14 | 4 | 388 | 4.140 | +0.140 |
| Mario France 4 | 3 | 200 | 3.157 | +0.157 |
| Mario France 14 | 4 | 421 | 4.231 | +0.231 |
| Mario France 7 | 5 | 645 | 5.258 | +0.258 |
| Mario U.K. 7 | 3 | 227 | 3.326 | +0.326 |
| Mario Hawaii 2 | 4 | 440 | 4.352 | +0.352 |
| Mario Australia 3 | 5 | 609 | 5.359 | +0.359 |
| Mario Hawaii 3 | 3 | 240 | 3.360 | +0.360 |
| Mario France 11 | 4 | 440 | 4.385 | +0.385 |
| Mario France 1 | 4 | 438 | 4.400 | +0.400 |
| Mario France 16 | 3 | 235 | 3.417 | +0.417 |
| Mario France 2 | 4 | 452 | 4.440 | +0.440 |
| Mario U.K. 3 | 4 | 452 | 4.465 | +0.465 |
| Mario Hawaii 15 | 4 | 452 | 4.475 | +0.475 |
| Mario France 13 | 4 | 440 | 4.491 | +0.491 |
| Mario Hawaii 8 | 5 | 678 | 5.495 | +0.495 |
| Mario France 12 | 3 | 224 | 3.513 | +0.513 |
| Mario Hawaii 16 | 4 | 455 | 4.564 | +0.564 |
| Mario U.K. 15 | 3 | 251 | 3.588 | +0.588 |
| Mario Hawaii 17 | 3 | 238 | 3.600 | +0.600 |
| Mario Australia 18 | 4 | 468 | 4.626 | +0.626 |
| Mario Hawaii 9 | 4 | 412 | 4.643 | +0.643 |
| Mario Hawaii 4 | 4 | 405 | 4.662 | +0.662 |
| Mario Hawaii 1 | 4 | 435 | 4.846 | +0.846 |
| Mario Hawaii 12 | 3 | 231 | 3.854 | +0.854 |
| Mario U.K. 11 | 4 | 471 | 4.922 | +0.922 |

### Findings

- **Mario Open's Japan plays like a NES Open course; Australia and France play 2-4
  strokes harder; Hawaii and the U.K. far harder.** 28 of those two courses' 36 holes
  play more than half a stroke over par, where the 54 NES Open holes have 4. Holes differ
  far more than courses: the three NES Open rounds come out within a stroke of one
  another (U.K. 73.19, Japan 73.38, US 74.01), where the expected order is Japan, US,
  U.K.
- **21 expert holes**: Mario Open holes that play worse than every NES Open hole.
  Curation tags each one `expert` (`docs/catalog.md`), and that tag, not this table, is
  what the randomizer's rules read.
  Fourteen are on the U.K. course, six on Hawaii, one on France, and all seven par 5s
  over 48 rows are among them. The hardest are the U.K. 10th (+3.52), a par 3 from an
  island tee to an island green, where every shot into the water drops back at the tee
  island's edge (the game's own drop rule, **Penalties and drops**), the Hawaii 14th
  (+3.40) and the U.K. 18th (+2.12). The line between expert and not is close: the worst
  NES Open hole, U.K. 11th, is +0.936 (as is its twin, the Mario Australia 12th), and
  the Mario U.K. 11th (+0.922) and 17th (+0.938) sit either side of it.
- **34 new holes at NES Open level**: Mario Open holes neither expert nor in a family
  with a NES Open hole. 4 play under par, 20 within +0.49 (where most NES Open holes
  are), and 10 from +0.5 to +0.93, as only NES Open's 4 hardest do. France (13) and
  Hawaii (12) give most of them, then the U.K. (4), Australia (4) and Japan (1): nearly
  every Mario Japan hole is a NES Open hole's twin.
- **Length predicts difficulty within a par**: over all 144 holes, the Spearman
  correlation of yards with strokes over par is 0.65 for par 3s, 0.66 for par 4s and 0.85
  for par 5s. **The game's handicaps predict it less well**: 0.54-0.68 on the NES Open
  courses, 0.13-0.53 on Mario Open's.
- **The long par 5s lean most on borrowed values**: the U.K. 14th visits spots too rare
  to value 0.27 times a hole, the Hawaii 5th 0.26 and 8th 0.21, where most holes visit
  them 0.01-0.15 times.

### NES US 16th and Mario U.K. 2nd

The U.K. 2nd (+1.02) is the US 16th changed (+0.72), with the same tee, green box and
pins and the same river carry. It plays about 0.4 strokes harder for two reasons:

- **Less land around the green.** The rough between the green and water or out of
  bounds narrows from 5-13 pixels to 1-5 on the east, 5-21 to 3-9 on the north, and 19-22
  to 5-14 on the west. The tee shot reaches the green as often on both (25.5% against
  25.6% for the U.K. 2nd's chosen drive), but near misses that stayed in rough on the
  US 16th go in the water: 38% of drives are dropped beside the green, against 25%.
  The new bunker short of the river is never reached.
- **The green is a crown, where the US 16th's is a bowl.** Both greens are four
  quadrants around a flat cross. The US 16th's quadrants slope gently toward the center
  (light tiles `$90-$93`, slope 40.40). The U.K. 2nd's slope away from it, about three
  times as steeply (`$40`/`$42` north and south, dark, scaled ×2.5; `$89`/`$8B` east and
  west, light, ×2; slope 120.C0). From the same spot a putt drifts toward the center on
  one and toward the edge on the other. Putting from the east and north quadrants is
  worth about 0.15 more a hole, and the chip from the drop beside the green 2.86 against
  2.53.

Played with the same drive, the U.K. 2nd's, the gap is 0.40: 0.18 from more drives in
the water and 0.22 from what follows them. The US 16th's own solve chose a drive worth
3.721, 0.10 worse than that one, so the screen missed it (**Future work**).

### The cleanup-round fix

After round 12 the solver adds no new states (**The solver**, Rounds). Until the fix, a
state could still switch to an intent whose outcomes landed on spots never valued, and
nothing could correct it: the tee chose short hops into cells priced by `guess` alone.
Five Mario Open holes ended with every visit from the tee on such spots (rare visits
1.0). Now intents chosen past that point must stay among valued states
(`HoleSolver.stays_valued`). The holes that had run past round 12, solved again:

| Hole | Before | After | Tee shot before → after |
|---|---|---|---|
| Mario France 3rd | 5.095 | 5.737 | 2W at power 30 → 1W |
| Mario France 18th | 5.668 | 6.471 | PW at power 37 → 1W |
| Mario Hawaii 14th | 7.699 | 8.398 | SW at power 24 → 1W |
| Mario Hawaii 18th | 5.983 | 6.252 | SW at power 1 → 1W |
| Mario U.K. 18th | 6.263 | 7.122 | 3W at power 28 → 1W |
| Mario U.K. 14th | 6.498 | 6.499 | unchanged |
| Mario U.K. 13th | 5.137 | 5.137 | unchanged |

The NES US 12th is the one NES Open hole that runs past round 12 (15 rounds). It solves
to 5.793, where a solve before the fix gave 5.412 in 16 rounds (**Loops the solver
builds**); that solve's file was not kept, so what its tee shot was is not known. The
Mario France 3rd is the same hole changed (`data/catalog/curation.json`), and the two
now agree: 5.793 and 5.737, both with a medium wood from the tee aimed well left of the
pin (aims 224 and 220). The 12th's play still visits 23 never-valued spots 0.027 times
a hole, the most of any hole (**Data**).
