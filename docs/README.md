# Documentation Index

One line per document. Command-line tools are indexed in the top-level `README.md`.

## Plans and design

| Doc | About |
|-----|-------|
| [adr/README.md](adr/README.md) | Architecture decision records: why things are the way they are, and when to revisit |
| [randomizer.md](randomizer.md) | Randomizer design: goals, logic, what gets randomized |
| [randomizer_devplan.md](randomizer_devplan.md) | Randomizer site architecture, data model, routes and the ordered development plan |
| [planning/download_settings.md](planning/download_settings.md) | Plan: saved download settings, the new SRAM option defaults and finish ABI 2 |
| [planning/hosted_roms.md](planning/hosted_roms.md) | Plan: an admin builds a seed's ROM for players who claim its slots from their phones |
| [deployment.md](deployment.md) | Running the randomizer site on its server: setup, releases, rollback and database restores |
| [test_suite_performance.md](test_suite_performance.md) | Test runtime measurements, slow-test coverage review and workload splitting |
| [documentation.md](documentation.md) | Philosophy and practices followed for documentation for the development process |
| [catalog.md](catalog.md) | The randomizer hole catalog: frozen index, curation, ids, versions and families |
| [derived_holes.md](derived_holes.md) | Derived holes: a vanilla hole and a checked-in delta, the delta format, publishing with `golf-derive`, and how a seed asks for them |
| [manifest.md](manifest.md) | Randomizer seed manifests: settings, the concrete course, and how generation fills them |
| [hole_transforms.md](hole_transforms.md) | Hole transforms: mirroring and hazard redraws, their names, versions and golden test |
| [yardage_book.md](yardage_book.md) | A seed's yardage book: its holes as built, at its pins, with tee-shot wind; the stored holes and the render cache |
| [thoughts_on_par_6.md](thoughts_on_par_6.md) | Design note: fitting par 6 (and any scarce par) into layout generation |
| [generation_rewrite.md](generation_rewrite.md) | Spike report: the hole draw as rules and a search, what ADR 0025 kept of it, and how the draw measures against the old one and a uniform one |
| [jp_extraction.md](jp_extraction.md) | Extracting Mario Open Golf (JP) courses for import into the US ROM |

## How the vanilla ROM works

| Doc | About |
|-----|-------|
| [rom_map.md](rom_map.md) | Where to look: each topic's bank, address and starting label, and its doc. Add a row whenever you had to search |
| [jp_rom_map.md](jp_rom_map.md) | Where to look in Mario Open Golf (the JP ROM): the same index, for its own addresses |
| [rom_disassembly.md](rom_disassembly.md) | The label file's coverage and invariants, the method for measuring data, the traps found, and what is left to confirm in Mesen |
| [../golf/core/compression.md](../golf/core/compression.md) | Course terrain/greens compression: RLE + dictionary, horizontal transitions, vertical fill |
| [terrain_data_locations.md](terrain_data_locations.md) | Byte ranges of the three vanilla courses' compressed terrain |
| [course_data.md](course_data.md) | The hole data model and JSON format the tools and editor share |
| [title_screen.md](title_screen.md) | The title screen: setup, the rank letter, its pattern-table split, and the credits combo |
| [menu_system.md](menu_system.md) | The data-driven title-screen menu chain |
| [course_intro_scene.md](course_intro_scene.md) | The full-screen landscape shown after picking a course |
| [golfer_sprites.md](golfer_sprites.md) | How the swinging golfer and club are drawn |
| [cutscene_sprites.md](cutscene_sprites.md) | Mario and Luigi outside the shot screen: walking out of the club house, the hole result and the other club house poses |
| [scorecard.md](scorecard.md) | The pause-menu scorecard screen |
| [prize_money.md](prize_money.md) | The PRIZE MONEY clubhouse cutscene |
| [text_scripts.md](text_scripts.md) | The bank 11 dialogue scripts: interpreter, opcodes, entry points and native calls |
| [scene_objects.md](scene_objects.md) | The object engine that animates menu and cutscene sprites: records, streams, metasprites |
| [perspective_scene.md](perspective_scene.md) | The behind-the-golfer scene in bank 9: course probes, neighbor tile passes and drawing the ball in |
| [hud_course_abbrev.md](hud_course_abbrev.md) | The course abbreviation in the in-game HUD |
| [music_format.md](music_format.md) | The audio engine and track format, including inserting tracks |
| [topspin.md](topspin.md) | Why the TOP 1 / TOP 2 spin settings have no effect on play |
| [opponent_shots.md](opponent_shots.md) | How CPU opponents play: recorded shot lists replayed per hole, chosen by level and chance |
| [wind.md](wind.md) | Wind anchors, per-swing speed, flight physics and the crosswind bug |
| [shot_physics.md](shot_physics.md) | Ball physics from swing to rest: launch, wind, lift, curve, bounce and roll, and the Python model of it |
| [jp_putting_physics.md](jp_putting_physics.md) | Putting in Mario Open against NES Open: the same tables and rolling code, plus a doubled slope in the JP cup close-up |
| [hole_difficulty.md](hole_difficulty.md) | Rating holes by expected strokes from the tee, solved over that model: how it was built, and every NES Open and Mario Open hole against par |

## Patches

| Doc | About |
|-----|-------|
| [patch_stack.md](patch_stack.md) | Building a ROM from an ordered stack of patches, and IPS output |
| [multi_bank_terrain.md](multi_bank_terrain.md) | Writing a course across terrain banks 0 and 1 (`golf-write`) |
| [wram_expansion.md](wram_expansion.md) | Growing the terrain buffer past 48 rows |
| [seeded_wind.md](seeded_wind.md) | Pin positions and wind as a function of a build-time seed |
| [wind_profiles.md](wind_profiles.md) | A seed's wind speed and direction profiles: the bands, the intensity ramps and the cones |
| [practice_swing.md](practice_swing.md) | Practice swings that cost no stroke |
| [green_shortcut.md](green_shortcut.md) | B then Select opens the green detail view, B then Start the scorecard |
| [putting_practice.md](putting_practice.md) | Starting every hole as a putt (design note, not shipped) |
| [green_slope_physics.md](green_slope_physics.md) | Experimental: green slopes as constant acceleration rather than speed-scaled |
| [mario_open_free_play.md](mario_open_free_play.md) | Mario Open (JP): course unlocks, the score limit that ends a round, and the patch removing both |
| [prehole_signpost.md](prehole_signpost.md) | The pre-hole signpost card, and replacing its banner art |
| [scorecard_qr.md](scorecard_qr.md) | End-of-round QR code submission, and its 6502 port |
| [scorecard_qr_mask_sweep.md](scorecard_qr_mask_sweep.md) | Results of the QR mask/capture-condition validation sweep |
| [seasonal_terrain.md](seasonal_terrain.md) | Spike: recoloring terrain for seasons; palette sites, the ball's shared color slots, unknowns and a plan |

## Guides

| Doc | About |
|-----|-------|
| [composing.md](composing.md) | Writing music for the game, for a composer |

## Editor

| Doc | About |
|-----|-------|
| [../editor/CLAUDE.md](../editor/CLAUDE.md) | Editor architecture and how to add editor tools |
| [forest_notes.md](forest_notes.md) | The forest fill algorithm |
| [feature_brush.md](feature_brush.md) | The Feature Brush: painting fairways, bunkers and water as shapes, and the fit that chooses their border tiles; the Out of Bounds Brush and the Green Brush built on it |
