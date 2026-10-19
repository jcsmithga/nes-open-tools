# ROM Map: Where to Look

> **Note**: This document was written by Claude, at jdharms's request.

An index from a topic to the part of the US ROM that implements it: the bank, an address
and the label to start reading from, and the doc that explains it, if there is one. It
is a starting point, not a description; the label file's comments and the docs carry the
detail.

Mario Open Golf (the JP ROM) has its own index, [jp_rom_map.md](jp_rom_map.md): its
addresses differ from the ones here, and the label file does not cover it.

**Keep it growing.** Whenever you have to search to find where something lives (a
routine, a table, a RAM variable), add a row here before you finish, even if you also
wrote a doc. A row is cheap; the search it saves is not.

Conventions: bank 15 is the fixed bank (`$C000`-`$FFFF`); every other address is in the
switchable window (`$8000`-`$BFFF`) of the bank named. Labels are names in
`NES Open Tournament Golf (USA).mlb`; `golf-rom-peek ... find-label <name>` resolves one.
Stub names (`LC_84FA_...`) are stable but may be renamed to a full name later; search by
address if a name is gone.

## Banks at a glance

| Bank | Holds |
|---|---|
| 0 | Japan course terrain; golfer CHR (Luigi, Tony); graphics tables |
| 1 | US course terrain; golfer CHR (Mario, Steve, Mark); graphics tables |
| 2 | Menu scene object data (`$8000`-`$837E`); UK course terrain; Billy's CHR; the scorecard screen; the tournament field simulation |
| 3 | Greens decompression tables and all 54 greens; replay code; CPU opponents' recorded shots |
| 4 | CHR graphics streams, the club sprite CHR |
| 5 | CHR graphics, the title screen background |
| 6 | CHR graphics, the course intro landscape |
| 7 | CHR graphics and nametables |
| 8 | Golfer body and club metasprites and their renderer; course intro letters; notable-score replay saving |
| 9 | Green and cup views; the behind-the-golfer perspective scene; SRAM initialization; the player stats screen; 32-bit math |
| 10 | Scene object data for cutscenes: metasprites, frame tables, streams |
| 11 | Text script interpreter and every dialogue script; the options screen |
| 12 | Title screen; the main menu chain; course intro; pre-hole signpost; prize money and wager scenes |
| 13 | The round: hole flow, shot setup panels, the swing, ball physics, course and green views, the in-game menu, HUD |
| 14 | Audio engine and music data; club house screens (choose clubs, hall of fame, clear save, training) |
| 15 | Fixed bank: reset/NMI, bank switching and far calls, PPU and graphics loaders, input, hole loading and decompression, lie classification, the object engine, metasprite renderers, DPCM samples, shared math |

Byte-level layout of the course data banks: the `nes-open-golf-rom-layout` skill.

## System

| Topic | Bank | Start here | Doc |
|---|---|---|---|
| Reset | 15 | `$CD00` `LCD00`; each bank ends in a reset stub (`LFFF3_Mmc1ResetStub`) | |
| NMI | 15 | `$D2BF` `NMIHandler` | |
| Bank switching | 15 | `$D352` `BankSwitchRoutine`, `$D35A` `SetPrgBank`; shadow `CurrentBankShadow` (`$5F`) | |
| Far calls | 15 | `$D372` `ExecuteFarCall` (inline bank, lo, hi) | rom-layout skill |
| Mirroring, CHR bank | 15 | `$D318` `WriteMMC1Control`, `$D32C`-`$D33C` | |
| Inline dispatch and lookup tables | 15, 12, 11 | `$D227` `DispatchInlineJumpTable`, `$D267` `...FF`; bank 12 `$8A14` `LookupInlineByteTable`, `$8A56` `LookupInlineRangeTable` | rom-peek skill |
| Inline word arguments | 15 | `$D8A2` `ReadInlineWordParameter` (reads its caller's caller) | rom-peek skill |
| RNG | 15 | `$D29C` `LSFR_RNG_ALGO`, state `RngState` (`$42`) | `wind.md` |
| 16-bit math, distance | 15 | `$E522` `DistanceBetweenPoints`, `$E5A9` `Multiply16Bit`, `$E5EE` `IntegerSqrt`, `$E743` `Mult8x8to16`, `$E84B` `Divide16`; square tables `HalfSquareTableLo/Hi` (`$CB00`, `$CC00`) | |
| 32-bit math | 9 | `$BEDE` `Multiply32Bit`, `$BF08` `Divide32Bit`; registers MathA `$62`, MathB `$66`, MathC `$6A`, high byte first | |
| BCD | 15, 9 | `$D131` `AddBcdByte` (maybe dead); bank 9 `$BC53` `ConvertToBCD` | |

## Graphics and the PPU

| Topic | Bank | Start here | Doc |
|---|---|---|---|
| Compressed graphics (CHR, nametables) | 15 | `$D45F` `LoadCompressedGraphics` (inline bank, lo, hi), `$D684` `...FromPtr`; stream codec `$D4C3` | `course_intro_scene.md`, `golf/core/graphics_codec.py` |
| Nametable writes | 15 | `$CE84` `WriteNametableTiles` (inline descriptor), Mode1 `$CE75`, Mode2 `$CE7E`; `$CDEF` `TileXYToNametableAddr` | `menu_system.md` |
| PPU write buffer | 15 | `$D033` `BeginBufferWrite` ... `$D06D` `ProcessPpuBuffer` | |
| Palettes | 15 | `$D80A` `Load32BytesToBuffer` (inline source) into `PaletteBuffer` (`$0476`) | `seasonal_terrain.md` |
| Attribute bytes | 15 | `$D8E9` `SetTileAttributePalette` | |
| Inline memory copies | 15 | `$D41A` `CopyInlineMemoryBlock` (src, dst, length) | |
| Metasprite renderers | 15, 13 | `$FEBD` `RenderMetasprite`, `$FDCE` `RenderMetaspriteClipped`, `$FF38` `RenderMetaspriteWithAttr`; bank 13 `$9492` `RenderGreenViewMetasprite`; `$FF7E` `HideUnusedSprites` | `rom_disassembly.md` (four formats) |
| Golfer and club sprites | 8 | `$8000` `RenderGolferAndClub` (per frame from bank 13 `$AAA2`); `...BodyMetaspriteData`, `ClubMetaspriteData` | `golfer_sprites.md` |
| Golfer CHR | 0, 1, 2 | `LuigiSpriteChrStreams` (bank 0 `$A23D`) and siblings | `golfer_sprites.md` |
| Golfer cutscene poses (club house walk-out, hole result, other club house scenes) | 10, 12 | bank 10 sprite ids `$01`, `$02`, `$03`, `$05` (frame tables `$811D`, `$8994`, `$923B`, `$A2B1`); bank 12 `$B094` the hole result (from bank 11 `$BEE5`; `$B2F3` classifies the score, `$B35B` picks the record), `$AB87` `InitClubHouseWalkOutScene` (`$AC01` `LC_AC01_PlaceClubHouseGolfer` loads it), `$B7D9` the hole in one (`$B81B` shared with the long drive and near-pin scenes), `$A4A4` the tournament placing celebration | `cutscene_sprites.md` |
| Golfer cutscene CHR | 6, 7, 0 | bank 6 `$8000` (Mario), `$8AB2` (Luigi); bank 7 `$8000` (club house walk-out, both); bank 0 `$B2E2` (wager) | `cutscene_sprites.md` |
| Golfer outfit color | 5, 12 | the shot screen: bank 5 `$BF1B` `GolferShirtColorTable`, one byte per golfer; a cutscene: color 1 of sprite palette 0, its palette + `$11` in bank 12 (walk-out `$ADB5`, hole result `$B691`) | `cutscene_sprites.md`, `golfer_sprites.md` |
| Club house walk-out scene | 12, 5, 7 | bank 12 `$AB87` `InitClubHouseWalkOutScene`; background bank 5 `$A22F` (tiles), bank 7 `$88BF` (nametable); palette `$ADA4` | `cutscene_sprites.md` |

## Input

| Topic | Bank | Start here | Doc |
|---|---|---|---|
| Controllers | 15 | `$D0CD` `ReadBothControllers`, `$D1A7` `ProcessBothControllers` (in the NMI) | |
| Input event queue | 15 | `$D213` `PushInputEvent`, `$D188` `PopInputEvent`, `$D222` `FlushInputEvents`; `InputEventQueue` (`$0400`) | |

## Course and hole data

| Topic | Bank | Start here | Doc |
|---|---|---|---|
| Hole setup | 15 | `$DA90` `InitHole`, `$DAB4` `LoadHoleParameters`, `$DB5D` `LoadTerrainAndAttrs`, `$DB3F` `CalcFlagXPosition` | `wram_expansion.md` |
| Pointer and metadata tables | 15 | `$DBBB`-`$E106` (course offsets, terrain/greens pointers, par, distance, tee, green, flags) | rom-layout skill |
| Terrain decompression | 15 | `$E107` `DecompressTerrain`; tables `$E1AC`-`$E3AB` | `golf/core/compression.md` |
| Greens decompression | 15, 3 | `$E3AC` `DecompressGreen`; tables bank 3 `$8000`-`$81BF`, data `GreensCompressedData` | `golf/core/compression.md` |
| Terrain data | 0, 1, 2 | `JapanCourseTerrainData`, `USCourseTerrainData`, `UKCourseTerrainData` | `terrain_data_locations.md`, `multi_bank_terrain.md` |
| Lie classification | 15 | `$EDBC` `ProbeBallPosition`, `$EDEA` `ClassifyProbePosition`; `BallLie` (`$C9`) | `seasonal_terrain.md` |
| Trees | 15 | `$F632` `ReadTreePixelColor`, `TreeColorMaskTable` (`$F3E2`) | |
| Wind | 15, 13 | `$DA25` `WindAdjustmentRoutine`; anchors drawn at `$DBA0`, or read from the `wind_anchors` table at `$E00B`; manual wind bank 13 `$89C2`/`$89CF`; in flight bank 13 `$B4FF` `ApplyWindEffect`, its cos lookup `$E7C3` `LE7C3` over `TrigLookupTable` (`$E7CB`) | `wind.md`, `seeded_wind.md` |
| Random par 5 / par 3 holes (tournament) | 15 | `$DA55` `PickRandomPar5AndPar3Holes` | |
| Course music | 15 | `$D9FE` `StartCourseBgm`, `CourseBgmTable` | `music_format.md` |

## The round (bank 13)

| Topic | Bank | Start here | Doc |
|---|---|---|---|
| Hole and round flow | 13 | `$8490` `AdvanceHoleAndCheckRoundEnd`, `$868C` `IncrementStrokeCount` | |
| Shot setup panels | 13 | `$877A` `ShotSetupSequence`; `$88C9` swing speed, `$8ADF` club, `$8BBD` spin; `$8A47` `AutoSelectClub` | `topspin.md` |
| Aiming | 13 | `$8C77` `UpdateSetupAim`, `$BD57` `UpdateSwingAim` | |
| Swing and meter | 13 | `$AA09` `SwingSequenceEntry`, `$A9A6` `ShotInitialization`, `$A8DD` `RenderSwingMeterMarkers` | `practice_swing.md` |
| Ball physics | 13 | `$AD0A` `CalcLaunchVector`, `$B11F` `ProcessLanding`, `$B4FF` `ApplyWindEffect`, `$B451` `ApplyRollFriction`, `$B952` `ApplyLandingKick`, velocity helpers `$B6F0`-`$B893` | `shot_physics.md`, `green_slope_physics.md` |
| Bunker lip rule (a sand shot put back in the sand) | 13 | `$B073`; armed at `$AA1E`, `BunkerFrameCount` (`$05A3`), `BunkerExitArmedFlag` (`$05A4`), sand snapshot `$059C`-`$05A2` (`$B0F4`), `MaybeBunkerExitClubThresholdTable` (`$B8AE`) | `shot_physics.md` |
| Course (overhead) view | 13 | `$8DA6` `DrawCourseGameplayView`, `$8DCE` `LoadCourseViewTileset`, `$8FC2` `DrawBallSprites` | |
| Green detail view | 13 | `$95A9` `DrawGreenDetailView`, `$95C6` `LoadGreenDetailViewTileset` | `green_shortcut.md` |
| Cup view, ball drop | 9 | `$8000` `LoadGreenViewTileset`, `$8050` `BallDropAnimationEntry`, `$81C4` `UpdateBallAtCup` | |
| Behind-the-golfer scene | 9, 13, 15 | bank 9 `$8829` `BuildPerspectiveScene`; bank 13 `$BB6D` `UpdateBallInScene`; `$E9C8` `CollideBallWithScene` | `perspective_scene.md` |
| HUD, distance readouts | 13 | `$A170` `ShowDistanceToPin`, `$A246` `UpdateShotDistanceReadout`; `CourseCodeFirstLetterTable` (`$9C47`) | `hud_course_abbrev.md` |
| Ball lie popup | 13 | `$A5DE` `ShowBallLiePopup` | |
| Peach (caddie) panel | 13 | `$BE0B` `DrawPeachPanel` | |
| In-game (Select) menu | 13 | `$96B1` `RunInGameMenu`, `$9723` `ConfirmInGameMenuSelection`, `$98B7` `ResumePlayAfterMenu`; Select/Start splice `$89B0`/`$89E5` | `green_shortcut.md` |
| Scorecard | 2 | `$AE76` `DrawScorecardScreen` | `scorecard.md`, `scorecard_qr.md` |
| CPU opponents | 3 | far-called from bank 13 `$8142` to `$A869`; `...OpponentShotLists` | `opponent_shots.md` |
| Tournament field | 2 | `$BC98` `MaybeSimulateFieldGolferHole`, `$BC3D` `SetFieldScoreBias`, `FieldHoleOddsTable` | |
| Replays | 3, 15, 8 | bank 3 `$A774`-`$A922`, `$A7FF` `ReplayNextStroke`; `$F6CE` `InitializeHoleReplay`; saving bank 8 `$9B21` `MaybeSaveNotableScoreReplay` | |

## Screens and scenes

| Topic | Bank | Start here | Doc |
|---|---|---|---|
| Title screen | 12 | `$80EC` `TitleScreenUpdate` | `title_screen.md` |
| Main menu chain | 12 | `$856E` `InitMainMenu`, `$8698` `MenuTick`, `$899F` `CallMenuChoiceHandler`, `MenuTextListData` | `menu_system.md` |
| Club house entries | 12 | `$84F5`-`$855B` (`LC_84FA_OpenPrizeMoney` and siblings) | `menu_system.md` |
| Course intro | 12 | `$9262` `SetupCourseIntroScene`, `$9478` `ShowCourseIntroScene`; `$92EC` the sunset showing after the scorecard, palette `$9837`; sprite 0 for the raster split is record `$98F3`, metasprite bank 10 `$AEA6` (tile `$1B`) | `course_intro_scene.md` |
| Pre-hole signpost | 12 | `$ABA5` `DrawPreHoleSignpost` | `prehole_signpost.md` |
| Prize money | 12 | `$8F34` | `prize_money.md` |
| Options | 11 | `$8B1B` `RunOptionsScreen` | |
| Player stats | 9 | `$B519` `RunPlayerStatsScreen` | |
| Choose clubs | 14 | `$AE14` `OpenChooseClubsScreen` | |
| Hall of fame holes | 14 | `$B420` `OpenHallOfFameHolesScreen` | |
| Clear saved data | 14 | `$B8C7` `OpenClearSavedDataScreen`, `ClearSavedDataMessages` | |
| Training | 14 | `$BD04` `OpenTrainingScreen` | |
| Scene objects (cutscene sprites) | 15, 2, 10 | `$F7C0` `AllocateObjectRecords`, `$F881` `SetObjectClipWindow`; data in banks 2 and 10 | `scene_objects.md` |

## Text

| Topic | Bank | Start here | Doc |
|---|---|---|---|
| Dialogue scripts | 11 | `$9033` `RunTextScript`, `$90AE` `PrintScriptCharacter`, opcodes `$913E`-`$9373`; `ScriptPtr` (`$06E7`) | `text_scripts.md` |
| Menu strings | 12 | `MenuTextListData` (`$8B8D`), drawn by `$8845` `DrawMenuEntryText` | `menu_system.md` |
| Stroke play coach (before and after each round) | 11, 12 | before: `StrokePlayIntroScript` (`$A0EE`); after: `$AA2F` ("Nice Round!", promotion offer) or `$B105` ("a disappointing score", when the round is worse than the average of the last two), chosen at bank 12 `$931D` | `text_scripts.md` |
| Money as text | 11 | `$9504` `FormatCurrentWagerString`, `$9511` `FormatTotalMoneyString` | |
| Default roster names | 9 | `DefaultRosterNamesTable` (`$AD65`) | |
| Club house screen text (training, clear data, hall of fame, name entry) | 7, 14 | bank 7 nametable tables `$98C3`-`$BAD5`, `ClearSavedDataMessages` and `HallOfFameScoreNames` in bank 14; the `clubhouse` font | |
| Stats and options screen text | 7, 9 | bank 7 nametables `$B6BD`-`$BECE`; `MaybeStatsHeaderDescriptor` in bank 9; the `stats` font | |
| Any on-screen text | all | `golf-rom-peek ... find-text "<text>"` searches every encoding, decoded nametables included | rom-peek skill |

## Audio (bank 14)

| Topic | Bank | Start here | Doc |
|---|---|---|---|
| Engine | 14 | `$8000` `AudioEngineMain`; requests `MusicRequest` (`$F4`), `SfxRequest` (`$F0`) | `music_format.md` |
| Music sequencer and data | 14 | `$8899` `MusicSequencerTick`, `MusicPatternHeaderTable` (`$8F2E`), `MusicStreamData` (`$913E`) | `music_format.md`, `composing.md` |
| Sound effects | 14 | `$8578` `UpdateSfxSlot2`, `$8784` `UpdateSfxSlot3` | |
| DPCM samples | 15 | `DmcDrum2Data` (`$C000`), `DmcDrumData`, `DmcUnknownData` (to `$CA3F`) | `music_format.md` |

## Save RAM

| Topic | Bank | Start here | Doc |
|---|---|---|---|
| SRAM init and magic | 9 | `$ACBC` `InitializeSram` (via `$D932` `CallInitializeSram`); `SramMagic` "5S" at `$6001` | |
| Saved state | SRAM | `PlayerName` (`$6004`), `TotalMoney` (`$600E`), `CurrentWager` (`$6014`), `Player1ClubBag` (`$6027`) | `prize_money.md` |

## Commonly read RAM

| Variable | Address |
|---|---|
| `HoleNumber`, `GameProgress` | `$94`, `$95` |
| `WindDirection`, `WindSpeed` | `$96`, `$97` |
| `ViewMode` (`$80` behind the golfer, `$00` overhead, `$40` green) | `$98` |
| `CurrentPlayerIndex`, `PlayerCount` | `$99`, `$9A` |
| `BallX`, `BallY`, `BallHeight` | `$AE`, `$B1`, `$B3` |
| `Aiming`, `BallLie`, `SwingSpeed` | `$B7`, `$C9`, `$CE` |
| `GolfGameMode`, `CurrCourse`, `ScrollLimit` | `$0100`, `$0102`, `$010D` |
| `MenuState` | `$068F` |
| Object slot arrays | `$7811`-`$7B01` (WRAM) |
