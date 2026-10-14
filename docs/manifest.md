# Seed Manifests

> **Note**: This document was written by Claude.

A manifest is a randomized seed as JSON: everything that decides the seed's unfinished ROM,
and the settings it was generated from. The site stores it on the seed row and serves it
at `/h/<id>.json`; `golf-randomize` reads and writes it as a file.

Code: `golf/randomizer/manifest.py` (the model and its JSON), `golf/randomizer/generate.py`,
`golf/randomizer/pool.py`, `golf/randomizer/music.py` and `golf/randomizer/words.py`.

## Example

With the hole list trimmed to one of its 18 slots:

```json
{
  "schema": 4,
  "generator_version": 2,
  "build_version": 6,
  "finish_abi_version": 2,
  "catalog_version": 1,
  "curation_stamp": "f640f8d1…",
  "settings": {
    "prng_seed": "3f9a0c61d2e84b07",
    "par": 72,
    "sources": ["nes_open_us", "mario_open_jp"],
    "include": [],
    "exclude_tags": [],
    "allow_family_repeats": false,
    "draw_rule": {"rule": "expert_cap", "per_nine": 1},
    "wind_speed_profile": "storm_rolling_in",
    "wind_direction_profile": "vanilla",
    "music": "random",
    "mercy_point": 9,
    "clubs": {"max": 14, "banned": [], "required_bag": null}
  },
  "course": {
    "holes": [
      {"id": "nes_uk/09", "par": 5, "transforms": [], "wind_seed": 32048,
       "wind_direction": 144, "wind_speed": 2}
    ],
    "music": "jp_france",
    "mercy_point": 9,
    "clubs": {"max": 14, "banned": [], "required_bag": null},
    "magic_words": ["DIVOT", "CADDY", "BOGEY"],
    "sram_magic": 21063
  }
}
```

## Structure

| Part | Holds | Read by |
|---|---|---|
| Version fields | `schema`, `generator_version`, `build_version`, `finish_abi_version`, `catalog_version`, `curation_stamp` | Loading, building, finishing and auditing |
| `settings` | Every input to generation, the PRNG seed included | The seed page |
| `course` | Concrete values | The build |

After checking the build and finish-ABI versions, an unfinished build reads only `course`,
and nothing in `course` needs interpreting: hole ids rather than filters, a music slug
rather than "random", wind seeds rather than the string they were derived from. A slot's
`transforms` are the exception. They name code whose output may change from one release
to the next (ADR 0024), so the site stores every hole as built beside the seed
(ADR 0021) rather than working it out again. Loading
is strict: a missing or unknown field is an error. `golf/randomizer/build.py` turns
`course` into the seed's unfinished ROM, and finishes that ROM per player
(`docs/randomizer_devplan.md`).

**Versions.**

- `schema` fixes the manifest's JSON shape and the meaning of its fields. It changes when
  either of those changes. A loader may read historical schemas even when the release no
  longer implements their unfinished buildchain.
- `generator_version` records which generator made the manifest. It is bumped when a
  change to generation is worth telling seeds apart by, such as a change to how holes
  are drawn; nothing regenerates a stored manifest, so it is provenance only (ADR 0024).
- `build_version` identifies the recipe that turns the concrete manifest, catalog hole
  data and vanilla US ROM into the unfinished ROM, as a label for what players get. It is
  bumped when a change to that recipe makes seeds play or look different enough to tell
  apart, such as a new patch or a fix like `wind_fix`, and always when the change touches
  anything the finisher or the scan route reads from a stored artifact
  (`tests/fixtures/build4/README.md`). A change that only moves bytes, such as better
  compression, need not bump it (ADR 0024). `golf-randomize build` refuses a build version
  other than the installed release's rather than silently reinterpreting it.
- `finish_abi_version` identifies the locations, preimages and meanings the stored
  unfinished artifact exposes to per-download finishing. Multiple build versions may
  share an ABI. Finishing dispatches on this version and promises safe personalization,
  not byte-identical output across releases. See `docs/patch_stack.md`.
- `catalog_version` and `curation_stamp` record what generation read, as provenance.
  Neither is needed to build, and nothing upstream of a stored seed is read again to show
  it (ADR 0024).

### Schema history

| Schema | Build version | Finish ABI | Change and support |
|---|---:|---:|---|
| 1 | 1 (implicit) | 1 (implicit) | The randomizer 1.0 manifest. Current code reads it for display and finishes its stored artifact through ABI 1, but does not rebuild it. |
| 2 | 2 | 1 | Adds the required `build_version` and `finish_abi_version` fields. Current code reads it and finishes its stored artifact through ABI 1, but does not rebuild it. |
| 2 | 3 | 1 | `wram_expansion` moves the terrain attribute buffer into WRAM, replacing `attr_streaming`, and the unfinished stack adds `green_shortcut`. Current code finishes its stored artifact through ABI 1, but does not rebuild it. |
| 2 | 4 | 2 | The unfinished stack adds `extended_sram_defaults`, a table the finisher fills with the BGM, swing, putt and spin defaults, and a seed with club rules leaves CHOOSE CLUBS out of the club house. Current code finishes its stored artifact through ABI 2, but does not rebuild it. |
| 2 | 5 | 2 | The unfinished stack adds `round_stats`, which counts fairways hit and penalty strokes, and its scorecard QR sends them in payload protocol version 2. Current code finishes its stored artifact through ABI 2, but does not rebuild it. |
| 2 | 6 | 2 | The unfinished stack adds `wind_fix`, which makes the wind push the ball the way its arrow points on all 16 directions (`docs/wind.md`, **The crosswind bug**). Schema 2 is the last without a draw rule: its seeds were drawn uniformly. |
| 3 | 6 | 2 | `settings` gains `draw_rule`, `wind_speed_profile` and `wind_direction_profile`, and each hole gains `wind_direction` and `wind_speed`, which the unfinished stack's `wind_anchors` patch writes into the ROM (ADR 0017). Schema 3 is the last without `include`: its pools held vanilla holes only. |
| 4 | 6 | 2 | `settings` gains `include`, the kinds of hole added to the pool beside the vanilla ones (ADR 0023). The unfinished buildchain is unchanged. This is the current schema and unfinished buildchain. |

Loading schema 1 supplies `build_version = 1` and `finish_abi_version = 1` in memory and
serializes it back in its original shape without adding either field. Loading schema 1
or 2 supplies the `uniform` draw rule the same way: their `settings` have no `draw_rule`,
refuse one, and serialize back without it. The same goes for the wind: their settings
have no wind profiles and their holes no anchors, so both profiles load as `vanilla` and
each hole with the anchors its wind seed deals, which is the wind those ROMs play. Loading
schema 1, 2 or 3 supplies an empty `include`, which they refuse and serialize back
without. The website stores
and serves the original JSON text as well as the unfinished IPS, so neither artifact of an
existing seed is rewritten by a schema update. Reading an old manifest and rebuilding it
are deliberately separate: the site needs the former to keep seed and round pages
working, while its stored IPS makes the latter unnecessary.

### Settings

| Field | Default | Meaning |
|---|---|---|
| `prng_seed` | drawn | The string every random choice comes from |
| `par` | 72 | Course par: 72, 71 or 70 |
| `sources` | both ROMs | Which vanilla ROMs' holes the pool draws from |
| `include` | none | Kinds of hole added to the pool beside the vanilla ones: `derived`, `community` |
| `exclude_tags` | none | Curation tags that keep a hole out of the pool |
| `allow_family_repeats` | false | Whether two holes of one family may share the course |
| `draw_rule` | at most 1 expert hole a nine | How the holes are drawn from the pool, below |
| `wind_speed_profile` | `vanilla` | How the holes' wind speeds are chosen ([wind_profiles.md](wind_profiles.md)) |
| `wind_direction_profile` | `vanilla` | How the holes' wind directions are chosen |
| `music` | `random` | A music slug, or `random` |
| `mercy_point` | 9 | The stroke a hole ends on with a tap-in; `null` leaves the patch out |
| `clubs` | no limits | Club rules, below |

Generation draws a `prng_seed` of 16 hex characters when the settings have none, and
records it. The web UI offers neither the PRNG seed, the mercy point, included kinds nor
excluded tags, so a seed from the site has a drawn seed, the default mercy point, vanilla
holes only and no excluded tags; the CLI sets all four.

`include` adds to the pool where `exclude_tags` takes away. A hole's kind is read from its
catalog source (`docs/catalog.md`): a vanilla hole is always in the pool, and a derived
hole ([derived_holes.md](derived_holes.md)) or a community hole only when its kind is
included. A new hole in the catalog is therefore in no pool that did not ask for its
kind, without a tag anyone has to remember (ADR 0023). Tags then narrow what was added:
`--include derived --exclude-tags short` draws derived holes other than the short ones.

### Draw rules

| `draw_rule` | Meaning |
|---|---|
| `{"rule": "uniform"}` | Every family in the pool is as likely as any other |
| `{"rule": "expert_cap", "per_nine": N}` | At most N expert holes on each nine, N from 0 to 9. 0 leaves them out |

An expert hole is one curation tags `expert` (`docs/catalog.md`): the 21 Mario Open holes
that play worse against par than every NES Open hole (`docs/hole_difficulty.md`,
**Expert holes**). The site's form offers `uniform` and the cap at 0 and 1; the CLI takes
any cap.

Predicted strokes over par for a par 72 course from both sources, as the sum of each
hole's `over_par` in `data/difficulty/holes.json` over 3,000 seeds a rule:

| Rule | Mean | Std dev | Worst seed | Expert holes a seed |
|---|---|---|---|---|
| `uniform` | +7.9 | 2.5 | +17.3 | 3.4 |
| `expert_cap`, 2 | +7.1 | 2.0 | +14.5 | 2.8 |
| `expert_cap`, 1 | +5.7 | 1.6 | +12.0 | 1.7 |
| `expert_cap`, 0 | +3.5 | 1.2 | +7.0 | 0 |

### Course

| Field | Meaning |
|---|---|
| `holes` | 18 slots in play order |
| `music` | The course theme's slug |
| `mercy_point` | Copied from the settings |
| `clubs` | Copied from the settings |
| `magic_words` | Three words for the title menus, the scorecard title and the seed page |
| `sram_magic` | The 16-bit value that marks a save as this seed's, neither byte `$00` or `$FF` |

A slot is a catalog hole `id`, its `par` (a copy of the catalog's, for readability),
`transforms`, a `wind_seed`, and the hole's wind anchors: `wind_direction`, a multiple of
16 from 0 to 240 (`$00` up the screen, a tailwind; `$80` a headwind), and `wind_speed`,
0 to 10 (`docs/wind.md`). `transforms` names hole transforms, such as `mirror@1`
or `hazards@1:<seed>`, applied to the hole in order when the course is built
(`docs/hole_transforms.md`). A name this release does not know, or a malformed seed,
fails when the manifest loads. Generation adds no transforms yet. The wind seed is the 16-bit state the ROM's own RNG starts the hole from
(`docs/seeded_wind.md`), not a seed for generation. No hole id appears twice.

The SRAM magic is what the ROM's save initialization compares a save against at boot
(`sram_defaults`). A save holding any other magic, from the vanilla game or another seed,
is wiped and rebuilt with the name and bag the player chose at download, so those choices
always land. Neither byte may be `$00` or `$FF`, what blank SRAM holds, or blank SRAM would
pass the check. The model refuses any other value when a course is built or loaded, so a
manifest that would fail at patch time cannot exist.

### Club rules

| Field | Default | Meaning |
|---|---|---|
| `max` | 14 | The most clubs a bag may hold, putter included, 1-14 |
| `banned` | none | Clubs no bag may hold. Never the putter |
| `required_bag` | `null` | The one bag every player carries |

Clubs are written with the choose-clubs labels `1W`-`4W`, `1I`-`9I`, `PW`, `SW` and `PT`,
in that order. The putter is always allowed: a required bag gains it if missing, must fit
within `max`, and holds no banned club. The ROM does not yet enforce these rules; the club
house's CHOOSE CLUBS menu stays reachable.

### Music slugs

A slug names the course a theme belongs to, with the catalog's lineage prefixes:

| Slug | ROM | Music id |
|---|---|---|
| `nes_japan` | NES Open | $03 |
| `nes_us` | NES Open | $02 |
| `nes_uk` | NES Open | $04 |
| `jp_japan` | Mario Open | $04 |
| `jp_australia` | Mario Open | $03 |
| `jp_france` | Mario Open | $0B |
| `jp_hawaii` | Mario Open | $02 |
| `jp_uk` | Mario Open | $0C |

The ids are those in each ROM's `data/music/` dump, and collide between the two ROMs.
A build plays a NES Open theme by pointing every course at it (`course_theme`), since its
data is already in the ROM, and imports a Mario Open theme from its dump (`music_import`).

### What is not in a manifest

- **The seed's identity.** Its URL id and QR seed ID live on the seed row
  (`docs/randomizer_devplan.md`). Two identical manifests can be two seeds.
- **Download-time choices.** Player names, bags, player IDs and MAC keys belong to the
  player's entry and the finishing stage.
- **Required ROMs.** `required_roms` computes them from the holes and the music: the US
  ROM always, and the Mario Open ROM when any hole, the base of any derived hole or the
  theme comes from it. The site stores them on the seed row when the seed is made.

## Generation

`generate(catalog, curation, settings)`:

1. **PRNG seed.** The settings' seed, or a fresh one.
2. **Pool.** Each lineage's newest version that is not withdrawn, is drawable, is vanilla
   or of an included kind, comes from one of the `sources` and has no excluded tag. A
   derived hole comes from its base's ROM; a community hole comes from none and needs
   none. Holes sharing a curation family form one family; every other hole is a
   family of its own, as is every hole when `allow_family_repeats` is on.
3. **Layout.** A uniform draw from the layouts for the par (`golf/randomizer/layout.py`).
4. **Holes.** Slot by slot: shuffle the unused families that have a member of the slot's
   par, take the first that leaves the remaining slots fillable, then one of its members
   with that par. Drawing a family before a hole keeps a hole with a twin from being twice
   as likely. The fillability check is a bipartite matching of slots to families, which
   finds the fill a backtracking search would, and a pool that no fill exists for raises
   `GenerationError` before anything is drawn. A family holding holes of different pars,
   such as a par 5 and its forward-tee par 3, can fill either kind of slot but only one.
   That is the `uniform` rule's draw (`draw_holes`). Under `expert_cap`
   (`draw_holes_capped`) the slots are filled in a shuffled order, so a nine's expert
   holes are spread over its nine slots and not gathered at its start, and a slot on a
   nine that already has its expert holes takes only a family's other members. A family
   is taken only if the slots still empty could then be filled with no further expert
   hole. So the pool must be able to fill the layout without expert holes whatever the
   cap, and one that cannot raises `GenerationError`.
5. **Music.** A named slug is used as given. `random` draws from the NES Open themes, or
   from all eight when at least one hole comes from Mario Open.
6. **Wind.** `derive_hole_seeds(prng_seed)` gives the 18 wind seeds, which fix each
   hole's pin and jitter. The two wind profiles then give each hole its direction and
   speed anchors ([wind_profiles.md](wind_profiles.md)); a `vanilla` profile keeps what
   the hole's wind seed deals.
7. **Magic words.** Three distinct words from `golf/randomizer/data/word_bank.txt`.
8. **SRAM magic.** Two bytes, each uniform over `$01`-`$FE`, high byte first.

The layout, holes, wind directions, wind speeds, music, magic words and SRAM magic each draw from their own generator,
`random.Random(f"{purpose}\0{prng_seed}")`, and the wind seeds from their own hash, so a
change to one draw leaves the others as they were.

Within one release, the same catalog, curation and settings give the same manifest; the
tests hold generation to that, and `--seed` relies on it. It is not a promise across
releases: nothing regenerates a stored seed, and a seed is what the site stored, not its
inputs (ADR 0024).

### The word bank

One word per line, in any case, uppercased on load. Every word is 4 to 6 characters of
A-Z and 0-9, the characters both the menu header (`menu_trim`) and the scorecard title
(`scorecard_course_name`) can draw, and no word appears twice; a unit test loads the
checked-in bank. Three words joined by spaces are at most 20 characters, within the
scorecard title's 26. The words tell a player at a glance that they have the right ROM;
they do not identify a seed uniquely.

## golf-randomize

```bash
golf-randomize generate --seed demo -o demo.json
golf-randomize generate --par 71 --sources nes_open_us --music nes_uk --mercy-point none
golf-randomize generate --experts-per-nine 0
golf-randomize generate --draw-rule uniform
golf-randomize generate --wind-speed storm_rolling_in --wind-direction out_and_back
golf-randomize build nes_open_us.nes demo.json -o demo.nes
golf-randomize build nes_open_us.nes demo.json --unfinished --ips demo.unfinished.ips
golf-randomize build nes_open_us.nes demo.json --credentials keys.json --name LUIGI --clubs 1W,3W,5I,PW
golf-randomize show demo.json
```

- `generate` has one flag per settings field, each defaulting to the field's default:
  `--seed`, `--par`, `--sources`, `--include`, `--exclude-tags`, `--allow-family-repeats`, `--music`,
  `--mercy-point` (a stroke or `none`), and `--clubs-max`, `--banned` and `--required-bag`
  for the club rules. The draw rule is `--draw-rule` (`uniform` or `expert_cap`) and
  `--experts-per-nine`, which alone means the expert cap. The wind profiles are
  `--wind-speed` and `--wind-direction`. Lists are comma-separated; clubs use the choose-clubs labels. It
  writes the manifest to `-o` (default `manifest.json`) and prints the course.
- `build` runs `build_unfinished` and then `finish` on the unfinished IPS, as the site
  does. With no stage flag the result is a guest ROM. `--unfinished` stops after the first
  stage, which is what the site stores. `--credentials` finishes signed in with a
  `golf-qr-credentials` file.
- Finishing takes the new-save defaults from `--name` (default `MARIO`), `--clubs` (default
  the vanilla bag) and `--no-bgm`, and refuses a bag the manifest's club rules forbid.
- `-o` writes the ROM (default the manifest's path with `.nes`, unless `--ips` is given);
  `--ips` writes the IPS from the vanilla ROM to the stage built.
- `--catalog`, `--curation` and `--holes` point at a catalog index, curation file or hole
  store other than the checked-in ones.
- `show` prints a manifest's holes with distances and wind anchors, totals, draw rule, wind profiles, music, mercy point, club rules,
  magic words and required ROMs.
