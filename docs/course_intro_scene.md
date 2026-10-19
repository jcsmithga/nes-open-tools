# Course Intro Scene

> **Note**: This document was written by Claude based on investigation requested by jdharms.

The full-screen landscape that appears after you pick a course — trees, a lake, clouds,
`JAPAN COURSE` / `U.S. COURSE` / `U.K. COURSE` in outlined "floating" letters, and a text
box underneath. Renders decoded straight out of the ROM are in `renders/course_intro/`.

![Japan course intro](../renders/course_intro/scene_japan.png)

Three separate systems meet here:

1. a **scene routine** in bank 12 that loads the graphics and runs the frame loop,
2. a **sprite-0 raster split** that swaps the background pattern table mid-screen,
3. a **byte-code text script** in bank 11 that types the dialogue.

## Reaching the scene

Bank 13 `$8000` is "start a round", entered from the menu's `$FF` terminal code.

```
$801A  LDA LoadSavedGameFlag ($04D7)
$801D  BNE $806A                 ; CONTINUE -> skip the scene entirely
$8029  LDA MaybeTwoPlayerFlag
$802C  BNE $8064                 ; 2 players -> straight to the club house walk-out, bank 12 $AB87
$802E  LDX GolfGameMode
$8031  CPX #$05
$8033  BCC $804F                 ; stroke/tournament-stroke
$8035  ...                       ; match play: pick opponent, far call bank 9 $B322
$804F  JSR ExecuteFarCall
$8052  .db $0C, $62, $92         ; -> bank 12 $9262   <<< the scene
$8055  BIT MenuState ($068F)
$8058  BPL $8064                 ; B was pressed during the scene -> back out
```

So the scene runs on **NEW GAME, 1 player** only. `LoadSavedGameFlag` is why CONTINUE
never shows it.

### Scene setup — bank 12 `$9262`

```
$9262  LDA #$00
$9264  STA $071D                 ; portrait/palette variant index
$9267  STA $071E                 ; sunset palette flag
$926A  LDA #$40 : STA $070D      ; accepted-button mask (B only, at this point)
$926F  LDA GolfGameMode : CLC : ADC #$01
$9275  JSR DispatchInlineJumpTable ($D227)
       $01->$9291  $05->$929C  $02/$03->$92A7  $06/$07->$92B2  $08->$92DB
       (no match)  -> $928E  JMP $9478
```

Each handler stores a **script pointer** into `$06E7/$06E8` and returns; the `$928E`
fall-through then jumps into the scene proper. For plain stroke play (`GolfGameMode = 0`,
so key `$01`) the handler at `$9291` sets the script pointer to bank 11 `$A0EE`.

| Key | `GolfGameMode` | Handler | Script (bank 11) |
|---|---|---|---|
| `$01` | `$00` stroke play | `$9291` | `$A0EE` |
| `$02`/`$03` | `$01`/`$02` 18H/36H tournament (stroke) | `$92A7` | `$A33C` |
| `$05` | `$04` match play | `$929C` | `$A2D5` |
| `$06`/`$07` | `$05`/`$06` tournament match | `$92B2` | via `$92C3` table, keyed on `OpponentGolferIdentity` |
| `$08` | `$07` bet on 1 hole | `$92DB` | keyed on `OpponentGolferIdentity` |

The match-play handlers also copy `OpponentGolferIdentity ($0131)` into `$071D`, which
selects the opponent's sprite tiles and palette below.

> `$071D` is labeled `SuppressMenuHistoryPushFlag` in the `.mlb`. That is its meaning in
> the title-menu driver (`docs/menu_system.md`); this scene reuses the same byte as a
> variant index. Both readings are correct in their own context.

### The sunset entry — bank 12 `$92EC`

The same scene is shown again after the scorecard at the end of a round, at sunset. That
entry is `$92EC`: it clears `$071D`, sets `$071E` to 1, dispatches on `GolfGameMode` the
same way to choose a script (`$931D` for stroke play, which starts from bank 11 `$AA2F`),
and jumps to `$9478`. With `$071E` set, the 16 bytes of `CourseIntroAltPaletteData`
(`$9837`) are copied over the background half of the palette buffer:
`19 36 30 27  19 36 09 27  19 1C 09 28  19 29 09 28`. When it is shown comes from
jdharms; the code path is read from the ROM.

## Where the graphics live

`$9478` loads four compressed blobs. Each pointer is a **graphics table**, not raw data;
`LD494_DecompressGraphicsTable ($D494)` reads its header and feeds each stream to the
`$D4C3` decompressor:

```
dest_lo, dest_hi     ; starting PPU address
count                ; number of streams
ptr_lo, ptr_hi       ; x count  (stream N starts where stream N-1 stopped)
```

| What | Table | Streams | PRG offsets | PPU |
|---|---|---|---|---|
| Landscape tiles + text font | bank 6 `$A781` | `$A786` | `0x1A786-0x1B1B3` | `$1000-$1EDF` |
| Course letters, shared part | bank 8 `$9BDB` / `$A452` | `$9BE2` | `0x21BE2-0x21F0C` | `$0000-$04AF` |
| Course letters, **Japan** | bank 8 `$9BDB` | `$9F0D` | `0x21F0D-0x22451` | `$04B0-$0BCF` |
| Course letters, **US + UK** | bank 8 `$A452` | `$A459` | `0x22459-0x22944` | `$04B0-$0B2F` |
| Portrait sprite tiles (`$071D`) | bank 8, see below | | | `$0C00-$0EFF` |
| Nametable, **Japan** | bank 8 `$B723` | `$B728` | `0x23728-0x2391A` | `$2000-$23FF` |
| Nametable, **US** | bank 8 `$B91B` | `$B920` | `0x23920-0x23B03` | `$2000-$23FF` |
| Nametable, **UK** | bank 8 `$BB04` | `$BB09` | `0x23B09-0x23CEC` | `$2000-$23FF` |

The tile-graphics table is selected by `CurrCourse ($0102)` from the pointer table at
bank 12 `$96A7`; the nametable table from `$96AD`. Japan has its own letter stream; US and
UK share one and differ only in twelve nametable tiles (the `S` vs `K` of `U.S.`/`U.K.`).

Every stream ends exactly where the next begins and every decode lands on a clean
boundary (`$23FF` for a nametable, i.e. 960 tiles + 64 attribute bytes), which is what
confirms the region boundaries above.

Indexed by `$071D` (`0` for every 1-player stroke/tournament game):

| Table | Purpose | Entries |
|---|---|---|
| `$96C7` | portrait CHR (bank 8, PPU `$0C00`) | `$A945 $AB9F $B2A7 $ADED $B4DF $B035 $B035` |
| `$96D5` | portrait object definitions (bank 12) | `$990E $9920 $9956 $9932 $9968 $9944 …` |
| `$96B9` | 32-byte palette (bank 12) | `$9777 $9797 $97F7 $97B7 $9817 $97D7 $97D7` |

The seven palettes are identical in their background half; only the sprite half changes,
which is how the portraits get different clothing colors.

`GolfGameMode` also picks a 32-tile header row written to PPU `$20E0` (nametable row 7)
from `CourseIntroModeTextPtrTable` (`$96B3`): `$988B` = 18-hole, `$98AB` = 36-hole,
`$986B` = bet-on-one-hole. `GolfGameMode & 3 == 0` — plain stroke or match play — skips
that write entirely, which is why the ordinary stroke-play screen has no header line.

That row is spelled with a **second, smaller font** occupying CHR `$0000`-`$02CF`, loaded
by the shared first stream. `04 07 0B 14 0D 0A` is `18HOLE`. Its existence is what pins
the raster split below row 7.

## The mid-frame pattern-table switch

**This is the thing that makes the scene hard to breakpoint.** The course-name letters and
the landscape do not live in the same pattern table, so the scene is a sprite-0 raster
split.

`$9574`, before the loop starts, clears bits 3 and 4 of the `PPUCTRL` cache `$10`, so the
frame *starts* with background pattern table `$0000` — the floating letters. Then every
frame, in `CourseIntroFrameTick` (`$95B4`):

```
$95B8  BIT $2002 : BVS $95B8    ; wait for the sprite-0 flag to clear
$95BD  BIT $2002 : BVC $95BD    ; wait for the sprite-0 HIT
$95C2  LDY #$9C : DEY : BNE     ; burn ~780 cycles (~7 scanlines)
$95C7  LDA $10 : ORA #$10
$95CB  STA $2000                ; background pattern table -> $1000, mid-screen
```

Above the split the nametable indexes CHR `$0000` (the small font and the course-name
letters); below it, CHR `$1000` (landscape and the dialogue font). In a tile viewer you
only see the text once you have advanced past the split scanline — the same tile indices
mean different things above and below it. `$95A4` puts bit 4 back into `$10` on the way
out.

The boundary falls on **tile row 8**: nametable rows 0-7 (sky, the logo, and the mode-text
row at `$20E0`) come from CHR `$0000`, rows 8-29 from CHR `$1000`. That is consistent both
ways — the mode-text row is spelled in the CHR `$0000` font, and row 8's cloud tiles
(`$03`, `$0C`-`$2C`) only exist as clouds in CHR `$1000`.

## Frame loop

```
$957D  LDA Controller_NewPress_Tmp ($18)
       STA MenuInputByte ($0681)
       JSR $F8CA                 ; OAM / object update
       JSR $95B4                 ; the split above, then input, then $95F3
       JSR $FF7E
       LDA $0682 : BEQ $957D     ; loop until the exit flag is set
```

`$95B4` masks the new-press byte with `$070D` before acting on it: `$80` (A) sets the exit
flag `$0682`; `$40` (B) sets `MenuState = $FF` *and* the exit flag, which is what `$8055`
in bank 13 reads to back out to the menu.

`$95F3` dispatches on the scene phase `$070A`:

| `$070A` | Handler | Does |
|---|---|---|
| `0` (no match) | falls through to `$960A` | nametable write, descriptor `$9610` |
| `1` | `$960A` | same |
| `2` | `$9615` | walks 12 steps from `TextBoxOpenStepPtrTable` (`$96E3`) using `CourseIntroPhaseStep` as the counter — the text box opening outward from its center — then `INC $070A` |
| `3` | `$9672` | `INC $070A`, then the box's left and right edges (`$98CB`, `$98DA`) |
| `4` | `$9680` | the box's top and bottom edges (`$98E9`, `$98EE`), clears `ScriptResumePtr`, sets `ScriptDelayCounter = $16` (22-frame pause) and `ScriptTextBufferPage = $70`, `INC $070A` |
| `5` | `$96A0` | `ExecuteFarCall` bank 11 `$9033` — run the text script |

The course-name letters are **not** animated by these phases; they come straight out of
the compressed nametable. Every phase-2 rect targets `$2244`-`$234B`, i.e. rows 17-26.

### Nametable descriptors

Two entry points share one descriptor format (`dest_lo, dest_hi, flags|width, height`):
`WriteNametableTiles` (`$CE84`) follows it with inline tile data, while
`WriteNametableTilesMode1` (`$CE75`) follows it with a **single byte that fills the whole
rectangle** — `$CF0C` skips the source increment when `$29` is 1. Bit 7 of the width byte
replaces the inline data with a 2-byte source pointer; bit 6 is the fill flag for the
`$CE84` entry point.

`$9615` assembles its descriptors in `NametableDescriptorBuffer` (`$0410`): four bytes
copied from the step list, then `$02` as the fill byte.

## The text script

The dialogue is a bank 11 text script, run one token a frame by `RunTextScript` (bank 11
`$9033`) from scene phase 5. The table under "Scene setup" says which script each game mode
starts; the interpreter, its opcodes, the character encoding and every script's entry point
are in `docs/text_scripts.md`, which also walks through the stroke-play script `$A0EE` as
an example.

## Mario Open's version

Mario Open Golf (`mario_open_jp.nes`) shows the same landscape after you choose stroke
play and before the course menu, so it carries no course name: the sky where NES Open
draws its letters is more cloud.

![Mario Open course intro](../renders/course_intro/scene_mario_open.png)

The routine is bank 12 `$A018`, the counterpart of `$9478`, reached by `JMP` from `$9F4E`
and `$9F8D`. Every graphics table is a fixed address in bank 8, with no per-course lookup:

| What | Table | Stream | PRG offsets | PPU |
|---|---|---|---|---|
| Sky and cloud tiles | bank 8 `$9D1A` | `$9D1F` | `0x21D1F-0x223C0` | `$0000-$08EF` |
| Landscape tiles | bank 8 `$AD1F` | `$AD24` | `0x22D24-0x2313A` | `$1000-$17CF` |
| More landscape tiles and the font | bank 8 `$9483` | `$9488` | `0x21488-0x218CD` | `$1800-$1C8F` |
| Nametable | bank 8 `$B13B` | `$B140` | `0x23140-0x232AF` | `$2000-$23FF` |

Other data the routine reads, all in bank 12:

| Address | What |
|---|---|
| `$A9D6` | nametable descriptor written at `$A04B`: 32 bytes to PPU `$1FE0`, the patterns of tiles `$FE` and `$FF` |
| `$A844` | seven 32-byte palette pointers, indexed by `$0617` (the US `$96B9`); entry 0 is `$A906` |
| `$A852` | four bytes copied over sprite palette 1 (`$048A`), the original kept at `$0654` until phase 1 restores it |
| `$A856`, `$A864` | portrait CHR and portrait object pointers (the US `$96C7`, `$96D5`) |
| `$AA22` | the three object records (the US `$98F3`); the first is sprite 0 |
| `$A9C6` | the 16-byte sunset background palette (the US `$9837`, same bytes), copied over the background half when `$0618` is nonzero |
| `$A1AF` | phase 1's fill, 7x3 of tile `$01` at `$236A`, which erases a diagonal stroke the nametable has on the green |

`$0618` (the US `$071E`) is cleared by the entry at `$9F41` and set to 1 by a second entry
at `$9F51`, the counterpart of the US sunset entry `$92EC`, which dispatches on `$0659`.

![Mario Open course intro, sunset palette](../renders/course_intro/scene_mario_open_sunset.png)

The raster split is the same code (`$A126`, with `LDY #$80` for the delay) but lower:
sprite 0 is at Y `$57` where the US scene has `$37`, so rows 0-11 come from CHR `$0000` and
rows 12-29 from CHR `$1000`. Row 11 is tile `$03` throughout, which is solid sky in both
pattern tables, so the exact scanline does not show.

### Against the US scene

The 16 background palette bytes are identical. Rendered, the two screens (before the US
text box opens) match pixel for pixel in tile rows 0-2, 8, 9 and 11-23. They differ in
rows 3-7 (course name against clouds), by 33 pixels of cloud in row 10, and in rows 24-29,
where each nametable has its diagonal stroke in a different place and erases it with a
different rectangle (US `$2305` 6x3, Mario Open `$236A` 7x3).

The pattern data is laid out differently, though: nearly every tile of CHR `$1000` differs
between the two, and the US scene draws cloud rows 8-11 from CHR `$1000` where Mario Open
draws them from CHR `$0000`.

### The sky patch

`course_intro_sky` (`golf/core/patches/course_intro_sky.py`) draws Mario Open's sky over
the course name. It leaves the split at row 8 and takes only Mario Open's rows 0-7: with
rows 8-29 still the US nametable's, the result differs from Mario Open's screen by the 33
pixels in row 10 and nothing else. No code, timing or sprite data changes.

- The shared first stream of the letters table (`$9BE2`, tiles `$00`-`$4A`: the small
  font and 30 cloud tiles) stays. Mario Open's eight rows use 98 distinct tiles; 27 are
  already in that stream, and the other 71 replace the Japan letters stream at `$9F0D`,
  from tile `$4B`.
- The Japan nametable stream (`$B728`) is rewritten with rows 0-7 renumbered to those
  tiles.
- The US and UK entries of `CourseIntroTilePtrTable` and `CourseIntroNametablePtrTable`
  point at the Japan tables, so every course shows the same screen. The US and UK tables
  stay where they are, unreferenced.

Sprite 0 constrains what can go in the sky. It is the first of the scene's three object
records (`$98F3`: x `$F8`, y `$37`, sprite `$06`), and its metasprite (bank 10 `$AEA6`,
frame 1 of sprite `$06`, which no other record's stream uses) is a single sprite of tile
`$1B`: one pixel, in the shared stream. The frame tick waits for its hit on every frame,
so the background under it has to be opaque. The patch refuses a sky with a transparent
pixel anywhere.

The mode-text row is still written in the tournament and bet modes, and its 32 tiles
include the US scene's row 7 clouds, which do not meet the new row 6. The randomizer's
`menu_trim` leaves only stroke play, which never writes that row.

The patch takes its sky from an image (`read_sky_image`), which a recipe step names as
`image`. The image is 256 pixels wide, its top 64 rows are the sky, and every pixel
there is one of background palette 0's colors 1-3 (`$3C`, `$30`, `$21`) as
`NES_SYSTEM_PALETTE` shows them; anything below is ignored, so a whole-screen export
works. The new tiles have to fit in the 117 between the shared stream and the portrait,
and the two streams in the Japan ones' space (1,349 and 499 bytes).

The default image is Mario Open's sky, `golf/core/patches/data/course_intro_sky.png`.
`golf-intro-sky mario_open_jp.nes` writes it from that ROM, and no build reads the ROM.
The randomizer's stack does not have the patch yet. When it does, every seed gets this
sky, whether or not the seed requires the Mario Open ROM (ADR 0019).

## Season palettes

The scene's look comes from the 16 background bytes of its palette (bank 12 `$9777` in the
US ROM), so a different season is a 16-byte change:

| Palette | Colors 1-3 | Covers |
|---|---|---|
| 0 | cloud shade, cloud white, sky | tile rows 0-11, and the course-name letters |
| 1 | cloud shade, trees, sky | the tree line |
| 2 | water, shrubs and shadow, sand | the far bank |
| 3 | light grass, shrubs and shadow, sand | the foreground, and the text box |

Color 0 is shared by all four: it is the mid grass, the lighter half of every tree, the
fill of the course-name letters. The trees in palette
1 can take a color of their own without touching the shrubs in palettes 2 and 3, which is
what makes blossom or red-leaf trees possible. The text box's fill is palette 3's "shrubs
and shadow" color.

`renders/course_intro/render_seasons.py` holds the palettes tried and renders each on both
scenes into `renders/course_intro/seasons/`:

| Name | Bytes |
|---|---|
| summer (the ROM's) | `1A 3C 30 21  1A 3C 0A 21  1A 11 0A 28  1A 2A 0A 28` |
| spring | `1A 35 30 21  1A 35 15 21  1A 21 0A 38  1A 2A 0A 38` |
| spring_fresh | `29 35 30 21  29 35 14 21  29 21 19 38  29 2A 19 38` |
| fall | `18 3C 30 21  18 3C 16 21  18 11 17 37  18 28 17 37` |
| fall_pale | `28 37 30 21  28 37 16 21  28 11 17 37  28 38 17 37` |
| winter | `31 3C 30 21  31 3C 0C 21  31 2C 0C 3D  31 30 0C 3D` |
| winter_overcast | `31 3D 30 10  31 3D 0C 10  31 2C 0C 3D  31 30 0C 3D` |
| sunset (the ROM's, `$9837`) | `19 36 30 27  19 36 09 27  19 1C 09 28  19 29 09 28` |

![Fall](../renders/course_intro/seasons/mario_open_fall.png)
![Winter](../renders/course_intro/seasons/mario_open_winter.png)

These are renders only. With the US course name on screen, the winter palettes leave the
letters pale on pale, since their fill is color 0. How the dialogue text and the portrait
sprites read against each palette has not been looked at.

## Breakpoints

Working from the outside in:

| Where | Breakpoint | Catches |
|---|---|---|
| Entry decision | exec bank 13 `$804F` | the far call into the scene; `$0100`/`$04D7`/`$0101` are already set |
| Scene setup | exec bank 12 `$9478` | before any graphics load |
| Graphics load | exec `$D684` (fixed bank), watch `$22/$23` and `$27` | each of the four blobs; `$22/$23` is the graphics-table pointer, `$27` the bank |
| Decompressor | exec `$D4C3`, watch `$50/$51` (source) and `$54/$55` (PPU dest) | per stream |
| The raster split | exec bank 12 `$95CB` | the exact scanline the pattern table flips |
| Script step | exec bank 11 `$9066` | one script token; `$20/$21` points at it, `$06E7/$06E8` is the PC |
| Character print | exec bank 11 `$90AE` | each printed character, in A |
| Exit | write to `$0682` | A or B accepted |

A memory breakpoint on `$06E7` (write) is the quickest way to find which script a given
game mode runs, since every handler in the `$9275` dispatch writes it.

Every address named in this document now has a label in
`NES Open Tournament Golf (USA).mlb`, so `golf-rom-peek` annotates the scene and
renders its tables as `.db` rather than decoding them as code.

## Editing notes

- The scene's text is uncompressed ASCII in bank 11 and can be edited in place as long as
  the byte count is preserved; `FB` is the line break and the window geometry from the
  `F9` opcode bounds it.
- The course-name letters are a bitmap: each cell in nametable rows 2-6 has its own tile,
  so changing the wording means editing both the nametable stream and the CHR stream.
  US and UK share a CHR stream and differ only in the twelve nametable tiles that spell
  `S` versus `K`.
- `golf/core/graphics_codec.py` is a Python implementation of the `$D4C3` stream
  decompressor (all four literal modes and the three VRAM-lookback modes) plus the
  `$D494` table walker; `renders/course_intro/render_scene.py` rebuilds the PNGs with it.

  Two details of `$D4C3` are easy to get wrong and produce plausible-looking corruption
  rather than an obvious failure. Streams inside one table are **chained**: `$54/$55`
  carries over, so stream N starts where stream N-1 stopped, and `$52/$53` (the lookback
  base) is re-latched from it per stream. And the backwards-lookback mode `$C0` reads
  `$2007` twice before its copy loop (`$D670` and `$D673`) where the forward modes read it
  once, so it copies `mem[src-n+1 .. src]` reversed, not `mem[src-n .. src-1]`.
