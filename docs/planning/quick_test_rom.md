# Quick-Test ROM

> **Note**: This document was written by Claude based on an investigation requested by
> jdharms. The patch below was built as a throwaway prototype that was not kept; no patch
> module or CLI implements it yet.

A ROM built from a single hole file that boots straight onto that hole's tee in the game's
practice mode, and returns to the tee when the hole ends. It is meant for testing a hole
from the editor without playing through menus or a round.

## Status

- The prototype built through `PatchStack` and booted under `golf.physics.nes` (py65):
  reset to the shot screen in 146 frames, ball on the tee, wind 0, `ManualWindModeFlag`
  set. Pressing Start before a shot returned to a fresh tee through the loop.
- jdharms ran the prototype in Mesen on 2026-11-15 and reported that it works. Which
  paths were exercised was not recorded.
- Nothing is implemented in the repo. The whole prototype is reproduced under
  [The prototype](#the-prototype).

## How practice mode works

Practice mode is the club house's Training screen, and it is a thin wrapper around the
normal round code.

| Step | Where | What it does |
|---|---|---|
| Picker | bank 14 `$BD04` `OpenTrainingScreen` | Picks a course and hole, stores `CurrCourse` and `HoleNumber`, calls `$D9C3`, and loops back to the picker when that returns. B leaves. |
| Mode setup | fixed bank `$D9C3` `LD9C3_InitManualWindMode` | Sets `ManualWindModeFlag` (`$04F6`) to `$80`; zeroes `PlayerCount`, `GolfGameMode`, the wind and `ReplayPlaybackFlag`; copies `HoleNumber` to `GameProgress`; far-calls bank 13 `$8097`. |
| The hole | bank 13 `$8097` | The normal round setup, entered just after the point where it would zero `HoleNumber`. It falls into the per-hole code at `$8142` (`InitHole`, ball on the tee, signpost, shot loop). |
| Return | bank 13 `$8346`, `$82F3` | With the flag set, holing out (`$8341`) and Start before any shot (`$82EE`) both `RTS` out of the far call instead of scoring the hole and advancing. |

What else the flag changes while a hole is in play:

- `WindAdjustmentRoutine` (`$DA25`) returns at once, so the wind is never drawn.
- Player 2's A adds `$10` to `WindDirection` (bank 13 `$89C2`) and player 2's B adds 1 to
  `WindSpeed`, wrapping to 0 at 10 (`$89CF`). They are input events `$61` and `$62` in the
  shot setup dispatch table at `$892D`.
- Start does not open save-and-quit (`$89E9`). Before the first shot it ends the hole.
  After a shot it takes the `$8A21` path, which restores the saved shot position; by the
  disassembly that is a retake of the last shot, which has not been confirmed in play.
- Nothing is saved (`$DA17`), no replay is recorded (`$8195`), and the in-game menu's
  first item is refused (`$9728`).

## The patch

Reset ends with `JMP $8000` in bank 13 (`$CD70`). That address is the game's outer loop,
and its first 10 bytes call the title screen and menu chain in bank 12:

```
$8000  A9 00       LDA #$00
$8002  85 99       STA CurrentPlayerIndex
$8004  20 72 D3    JSR ExecuteFarCall
$8007  0C 00 80    .db $0C, $00, $80
```

The patch replaces those 10 bytes with a loop of the same length:

```
$8000  A9 00       LDA #$00
$8002  85 94       STA HoleNumber
$8004  20 C3 D9    JSR LD9C3_InitManualWindMode
$8007  4C 00 80    JMP $8000
```

`$D9C3` is in the fixed bank and far-calls the round itself, so calling it from bank 13 is
safe. Every return from the hole lands on the `JMP`, which runs the fresh-hole setup again:
`InitHole`, pin draw, ball on the tee.

Nothing the title screen or menus set up is needed. Reset clears RAM and initializes SRAM
before the jump, and Training is reachable in the vanilla game without a round ever having
been played. `CurrentPlayerIndex` is zeroed again at `$8097`.

The rest of bank 13's `$800A`-`$8096` becomes unreachable, along with the three other
`JMP $8000` sites (`$8061`, `$84B1`, `$858C`), which are all round-end paths that the flag
keeps the game away from.

## The prototype

```python
hole = HoleData()
hole.load(hole_path)
boot = BytePatch(
    "boot_to_practice",
    "Boot into practice mode on hole 1, looping",
    0x34000,
    bytes.fromhex("A900 8599 2072D3 0C0080"),
    bytes.fromhex("A900 8594 20C3D9 4C0080"),
)
steps = [
    WRAM_EXPANSION_PATCH,
    MULTI_BANK_CODE_PATCH,
    COURSE_MIRRORS_PATCH,
    CoursePatch([hole] * 18),
    WIND_FIX_PATCH,
    green_shortcut_patch(),
    SWING_SPEED_HUD_PATCH,
    boot,
]
rom = PatchStack(steps).build(vanilla).rom
```

`PatchStack` reported no overlapping writes for these eight steps.

## Left to do for a real tool

- **One hole in 18 slots.** `CoursePatch` requires 18 holes and writes each one's data
  separately, so the prototype stored the same hole 18 times. US hole 1 (455 bytes of
  terrain) used 9,162 of the 17,444 terrain bytes; a hole over about 960 bytes will not
  fit. The tool needs `CoursePatch` to share identical data between slots, or 17 small
  filler holes.
- **The signpost.** The pre-hole signpost is most of the 2.5 seconds before the shot
  screen, and with `CurrCourse` 0 it reads "JAPAN COURSE". It is one far call at bank 13
  `$81AB` (to bank 12 `$ABA5`).
- **Pins.** `InitHole` draws one of the hole's four pins from the RNG each time it runs,
  so the pin can change on each return to the tee. Whether the tool should leave that,
  cycle the pins or fix one is undecided.
- **The rest of the unfinished stack.** Not tried with this patch: `seeded_wind`,
  `wind_anchors`, `mercy_tap_in`, the music and signpost steps, `round_stats`,
  `scorecard_qr`, `scorecard_course_name`, `menu_trim` and `extended_sram_defaults`. The
  wind and round-end ones have nothing to act on in practice mode. `PatchStack` will
  report any that write `$8000`-`$8009`.
- **A whole course.** Looping on `OpenTrainingScreen` (a far call to bank 14 `$BD04`)
  instead of `$D9C3` keeps the vanilla hole picker, for testing an 18-hole course
  directory. With `course_mirrors` its course choice has no effect.
- **A CLI and an editor action**, and a place for the built ROM to go.
- **A test.** The py65 machine can boot the built ROM and check that it reaches
  `ShotSetupSequence` (bank 13 `$877A`) with the ball on the tee. The signpost's exit
  waits at bank 12 `$ABE1` for the PPU buffer to drain without calling `WaitForVblank`,
  which `NesMachine` never satisfies on its own; the prototype's harness ran an NMI from a
  breakpoint there.

## Starting the ball anywhere

Not built. The aim is to test an approach, a recovery or a putt without playing the hole
up to it.

### What is known

- The ball is not placed by `InitHole`. Bank 13's fresh-hole loop at `$8155` copies
  `TeeBlocksX`, `TeeBlocksY` and `TeeBlocksYHigh` (`$010E`-`$0110`) into the per-player
  ball position at `$0115,X`, `$0119,X` and `$011B,X` (`$8173`-`$8184`), after setting
  both fractions to `$80`, the middle of the pixel.
- Those three values come from the hole's `tee` metadata, in pixels. US hole 1 has `tee`
  (91, 242) and the ball starts at (91, 242, high byte 0).
- The tee position has no part in the lie. Its only readers are that copy and the tee
  marker sprites (bank 13 `$8EB6`-`$8EC9`). The lie is recomputed from the ball's position
  at the top of every shot (bank 13 `$87B5`, `ClassifyProbePosition`), and the tee lie
  (`BallLie` 1) is what terrain tiles `$35`-`$3C`, the tee box, classify as. See the lie
  table in `docs/shot_physics.md`.
- A ball inside the green box on a tile of `$30` or more gets the putting view, the putter
  and the putting physics with nothing else patched. See `docs/putting_practice.md`.
- The quick-test loop runs the fresh-hole loop on every return, so whatever start
  position the ROM holds is used again each time.

Checked by building the prototype from US hole 1 with `tee` moved, booting each ROM under
py65 and reading `BallLie` at the first shot:

| `tee` | `BallLie` |
|---|---|
| (91, 242), the hole's own | 1, tee |
| (91, 200), (91, 150), (100, 100) | 0, fairway |
| (60, 140) | 2, rough |
| (50, 135), (20, 200) | 5, out of bounds |

### The splice

The start position is written over the copy at bank 13 `$8173`-`$8184`, so the hole's
`tee` is left alone and the tee markers are drawn where the hole has them. Each of the
three `LDA` absolute instructions becomes an `LDA` immediate and a `NOP`, the same 18
bytes in place, with no payload elsewhere:

```
$8173  A9 xx       LDA #start_x
$8175  EA          NOP
$8176  9D 15 01    STA $0115,X        ; ball X
$8179  A9 yy       LDA #<start_y
$817B  EA          NOP
$817C  9D 19 01    STA $0119,X        ; ball Y low
$817F  A9 hh       LDA #>start_y
$8181  EA          NOP
$8182  9D 1B 01    STA $011B,X        ; ball Y high
```

The position is in pixels, the units of `tee`. The fractions set just above stay `$80`.
The three other `InitHole` callers (`$813C`, `$8D13`, `$8D66`) are resume paths and are
not touched. The `putting_practice` spike splices the same 18 bytes, so the two cannot be
stacked.

Built on the prototype as a `BytePatch` at PRG `0x34173` and booted under py65 with US
hole 1: a start of (91, 150) gave the ball at (91, 150), `TeeBlocksX/Y` still (91, 242),
`BallLie` 0, the view scrolled to the ball and REST reading 134 yards; (60, 140) gave
`BallLie` 2. Not yet run in Mesen.

Moving `tee` in a copy of the hole also places the ball, with no 6502, but the tee markers
move with it. jdharms prefers the markers in their real place, so the splice is the way.

### To find out

- What the game does with a start that is out of bounds or in water, and whether the
  build should refuse one.
- How the editor should let a start position be chosen, and whether it is saved with the
  hole or passed only to the build.
