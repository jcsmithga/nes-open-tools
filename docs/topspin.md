# Topspin Does Nothing

> **Note**: This document was written by Claude based on investigation requested by jdharms.

Setting the ball spin to `TOP 1` or `TOP 2` has no effect on play in the vanilla US
ROM. Both behave exactly like `NORMAL`. The only difference is the text and the cursor
position on the spin gauge.

The same is true of Mario Open Golf (JP), so this is not something the US release broke.

## The variable

Ball spin for the shot being set up lives in `ShotSpinSetting` at `$012E`:

| Value | Gauge reads |
|-------|-------------|
| 0 | `TOP 2` |
| 1 | `TOP 1` |
| 2 | `NORMAL` |
| 3 | `BACK 1` |
| 4 | `BACK 2` |

The menu handler clamps it to that range: Up (`$8C25`) does `DEC $012E` and undoes it on
`BPL` failing, Down (`$8C39`) does `INC $012E` and undoes it if the result reaches `#$05`.
At round start `$80AF` sets `$0127` and `$0128` (the two per-player saved settings) to 2,
so the game starts on `NORMAL` unless the SRAM default at `$6F9B` overrides it.

The value-to-text mapping above is not taken on faith from the label file. The gauge text
comes from `SpinIndicatorStringPtrTable` at bank 13 `$A144`, indexed by spin times two,
and decoding those five tile strings against `data/chr-ram.txt` gives `TOP 2`, `TOP 1`,
`NORMAL`, `BACK 1`, `BACK 2` in that order.

## Every read of `$012E`

There are exactly fifteen absolute accesses to `$012E` in the ROM, all in bank 13.
`find-refs` reports them, and a scan of all 256KB for any absolute-addressing opcode with
`$012E` as its operand returns the same fifteen and nothing else.

Nine are bookkeeping: `$8BCE` loads the setting at the start of a shot, `$8C29`-`$8C47`
are the D-pad adjustments, `$8C56` and `$8C6B` write it back to the per-player slot at
`$0127,X`, and `$BDE5` copies it into the saved-shot record at `$051B,X`.

Two are display: `$A0F4` picks the gauge text, `$A14E` positions the gauge cursor.

The remaining four are the ones that affect play, and all four gate the same way:

| Address | Test | What happens when the test passes |
|---------|------|-----------------------------------|
| `$ADBB` | `CMP #$03` / `BCC` | `PenaltyAccumulator` (`$D3`) += 3 |
| `$AF2E` | `CPY #$03` / `BCC` | overrides the landing-effect index in `$F2` with `#$08` |
| `$B150` | `CMP #$03` / `BCC` | on the green, doubles or 1.5x's `VelocityScaleX/Y` |
| `$B2A3` | `CPY #$03` / `BCC` | passes `$D3` to `LD_B451` instead of the fall-through value |

Every one of them is a "3 or higher" test. Values 0, 1 and 2 take the identical path in
all four. There is no lower-bound test anywhere, so no code can tell topspin from normal.

`$B14A` is worth reading in full, since it is the only place the two backspin levels
differ from each other:

```
LD_B14A_CheckSpinEffect:
$B14A  A5 CD       LDA ClubSelection
$B14C  C9 04       CMP #$04
$B14E  90 2F       BCC LD_B17F_ApplyBounce      ; clubs 0-3 skip spin entirely
$B150  AD 2E 01    LDA ShotSpinSetting
$B153  C9 03       CMP #$03
$B155  90 28       BCC LD_B17F_ApplyBounce      ; TOP 2 / TOP 1 / NORMAL all leave here
$B157  A4 C9       LDY BallLie
$B159  C0 06       CPY #$06
$B15B  D0 22       BNE LD_B17F_ApplyBounce      ; green only
$B15D  AC B4 05    LDY PreviousLieType
$B160  C0 02       CPY #$02
$B162  F0 1B       BEQ LD_B17F_ApplyBounce
$B164  C9 04       CMP #$04
$B166  D0 07       BNE LD_B16F_SuperBackspin
$B168  06 E4       ASL VelocityScaleX           ; BACK 2: x2
$B16A  06 E6       ASL VelocityScaleY
$B16C  4C 7F B1    JMP LD_B17F_ApplyBounce
LD_B16F_SuperBackspin:                          ; BACK 1: x1.5
$B16F  A5 E4       LDA VelocityScaleX
...
```

So the five-position gauge resolves to three behaviours, and even those three are thin:
`$ADBB` gives `BACK 1` and `BACK 2` the same `+3`, and `$B2A3` treats them identically.
They only diverge at `$B14A`, and only on the green with a club of 4 or higher.

## Looking for a cut feature

Nothing in the ROM looks like a topspin implementation that was disabled or branched
around. Four things were checked:

- **A counterpart to the backspin velocity scaling.** `$B168` and `$B16F` scale
  `VelocityScaleX/Y` up. Topspin would scale them down, but there is no `LSR $E4` or
  `LSR $E6` in any executable code. The six byte matches for those opcodes across the ROM
  are all inside compressed terrain in banks 0 and 2.
- **A subtract path on `$D3`.** `PenaltyAccumulator` is zeroed at `$AD9C` and from there
  only ever accumulated with `ADC` (`$ADC2`, `$AE05`, `$AE2C`, ...). Topspin would have to
  reduce it. Nothing decrements it.
- **An orphan entry in the shot menu dispatch.** The `DispatchInlineJumpTableFF` table at
  `$8C08` has six entries, all with live targets.
- **An unread column beside the replay tables** (see below). The two tables are exactly
  fifteen bytes each and code resumes immediately after them at `$A83C`.

This is signature-based, not a proof. No full reachability sweep of bank 13 was done, so a
vestige that does not look like any of the four shapes above could still be sitting
somewhere unexamined.

## The replay record reserves five spin values

The one place the ROM still treats spin as five distinct things is the highlight replay
format. Aces, albatrosses, eagles and birdies are saved to SRAM (`$0FB0` onwards) as the
*inputs* to the shot, not as a ball path: aiming, club, swing speed, spin, hi/lo, wind and
the hole's starting RNG state. Playback re-simulates from those.

The encoder at bank 8 `$9AE8` packs swing speed and spin into a single nibble:

```
$9AE8  AD 17 05    LDA $0517          ; swing speed
$9AEB  0A          ASL A
$9AEC  0A          ASL A
$9AED  6D 17 05    ADC $0517          ; * 5
$9AF0  6D 1B 05    ADC $051B          ; + spin
$9AF3  0A          ASL A              ; << 4
...
$9AF7  0D 19 05    ORA $0519          ; | club
$9AFA  9D 64 06    STA $0664,X
```

`LoadReplayShotRecord` at bank 3 `$A796` unpacks it with two fifteen-byte tables:

```
ReplayShotSwingSpeedTable  $A81E: 00 00 00 00 00 01 01 01 01 01 02 02 02 02 02
ReplayShotSpinTable        $A82D: 00 01 02 03 04 00 01 02 03 04 00 01 02 03 04
```

That is three swing speeds times five spin settings, with the spin column running the full
0 to 4. The format sets aside a distinct encoding for `TOP 1` and `TOP 2` and round-trips
them through SRAM, which only makes sense if all five values were meant to change the
shot. Two replays that differ only in `TOP 1` versus `NORMAL` play out identically; the
only thing that changes is the gauge.

The gauge sprite carries the same assumption. `$A14E` draws the cursor at a fixed X of
`$28` and a Y of `$BF + spin * 4`, five evenly spaced stops suggesting a gradient over a
mechanic that is really a step function.

## Labels added

Range labels covering the above are in the label file:

| Address | Label |
|---------|-------|
| bank 13 `$A113-$A12F` | `SpinIndicatorPanelTiles` |
| bank 13 `$A130-$A143` | `SpinIndicatorStringTable` |
| bank 13 `$A144-$A14D` | `SpinIndicatorStringPtrTable` |
| bank 13 `$A16B-$A16F` | `SpinIndicatorCursorMetasprite` |
| bank 3 `$A81E-$A82C` | `ReplayShotSwingSpeedTable` |
| bank 3 `$A82D-$A83B` | `ReplayShotSpinTable` |
| bank 3 `$A796` | `LoadReplayShotRecord` |

## Confidence

The enumeration of readers is exhaustive rather than pattern-matched, so no consumer of
`$012E` escaped it. What static analysis cannot rule out is an indexed or computed access
landing on `$012E`; of the indexed bases near it, `$0127,X`, `$0129,X` and `$012B,X` are
all two-entry arrays initialised at `$80AF`, so none of them reach it.

This has not been confirmed on hardware or in an emulator. The cheap check is a read
breakpoint on `$012E` in Mesen: play a shot at `TOP 2` and one at `NORMAL` and confirm the
only reads that fire are `$A0F4`, `$A14E` and `$BDE5`.
