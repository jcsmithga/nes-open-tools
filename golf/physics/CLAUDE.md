# golf/physics

A Python model of the game's ball physics, ported from bank 13 and the fixed bank so that
it matches the ROM register for register, frame for frame. `docs/shot_physics.md` explains
how a shot works; `docs/hole_difficulty.md` is what the model is for and what
comes next; ADR 0011 is why it is an exact port.

The model is of the vanilla ROM. Randomizer seeds carry `wind_fix`
(`golf/core/patches/wind_fix.py`), which changes the cos lookup `ApplyWindEffect` uses, so
the model's wind is wrong for eight directions on those ROMs (`docs/shot_physics.md`,
**Eight wind directions are distorted**).

## Tests

The model's checks against the ROM live in `tests/physics/`. The default `pytest` run
leaves them out because they are slow (every pixel of every hole, hundreds of shots through
an emulated 6502). **Run them whenever you change anything in `golf/physics/`**, and only
then:

```bash
uv run pytest --physics tests/physics
```

## The references

- `rom_oracle.py`: one routine at a time. `RomShot` runs `CalcLaunchVector` alone;
  `RomTerrainProbe` runs `ClassifyProbePosition` over a hole poked into RAM;
  `RomAimAtFlag` runs `CalcAimTowardFlag`.
- `nes.py` and `rom_game.py`: the whole game. `NesMachine` emulates MMC1 banking, the
  controller and vblank (running the game's NMI handler), and `RomGameShot` plays a shot
  through the game's own `InitHole` and `ShotSetupSequence` by pressing buttons. Prefer
  this for anything that happens between frames of the physics; a routine-level oracle
  misses code that runs elsewhere in the frame. `green_view_sprites` runs the putting
  setup the same way and returns the sprites the putting view shows. With `ppu=True`
  (`NesMachine`, `RomGameShot`) the machine also keeps the PPU's memory (`ppu.py`), so
  `golf/rendering/nes_screen.py` can draw what the game put on screen; it is checked by
  `tests/unit/test_ppu.py`, `tests/unit/test_nes_screen.py` and
  `tests/integration/test_nes_screen_rom.py`.

## The putting view

`green_view.py` ports where the putting view puts the ball, the cup, the aim cursor and
the meter's marker, and the aim every shot starts with (`docs/putting_view.md`). Its
checks are `tests/physics/test_green_view_rom.py`, against the sprites the game itself
leaves in RAM.

## Shared flights

Apart from the references and `ppu.py`, `flights.py` is the one module here that is not a
port. It is a faster way to run `shot.py`, and is checked against `simulate` rather than
the ROM, by `tests/integration/test_flights_rom.py` (in the default run, as it takes
seconds). It shares flights only between starts on whole pixels, and plays a start with
fractions alone. It depends on knowing which parts of a frame read the ground and which
registers the probe writes, so a change to `ShotInFlight` or `Ball.observe` that adds one
must be mirrored there.

## Rules for new code here

- **Check it against the ROM before building on it.** Every ported routine gets an oracle
  in `rom_oracle.py`, which runs the ROM's own code under py65 with the same inputs, and a
  test in `tests/physics/` that compares the results exactly, not approximately. Plant a
  few deliberate bugs to make sure the test can fail.
- **Keep the game's units and widths.** Registers stay ints at their real width, in the
  game's fixed point. Convert to pixels or yards only at the edges (`ShotResult`,
  `golf-shots`).
- **Port the quirks.** An 8-bit wrap, a lost carry or an out-of-bounds table read is part
  of the game. Keep it, and say in the docstring why the code looks odd.
- **Read tables from the ROM** you are given, not from copies in `data/` or the code.
- **Stay runnable under PyPy 3.11.** The difficulty solver runs this code under PyPy,
  which plays shots about ten times faster. Import nothing from `golf/core/patches/`
  (it uses 3.12-only syntax; `golf/core/rng.py` and `clubs.py` hold what the physics
  needs) and use no 3.12-only syntax here. `tests/meta/test_pypy_ready.py` checks both.
