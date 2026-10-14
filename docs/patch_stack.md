# Patch Stacks

> **Note**: This document was written by Claude based on design and ideas by jdharms.

A finished ROM is a vanilla ROM plus an ordered list of patches: the code a course needs,
the course itself, and the gameplay, quality of life, and "fit and finish"
patches on top. `PatchStack` (`golf/core/patches/stack.py`) is that list,
and builds it in memory.

```python
from golf.core.patches import (
    COURSE_MIRRORS_PATCH, MULTI_BANK_CODE_PATCH, WRAM_EXPANSION_PATCH,
    CoursePatch, PatchStack, seeded_wind_patch,
)

course = CoursePatch(holes)                  # 18 HoleData
stack = PatchStack([
    WRAM_EXPANSION_PATCH,
    MULTI_BANK_CODE_PATCH,
    COURSE_MIRRORS_PATCH,
    course,
    seeded_wind_patch("my seed"),
])

vanilla = Path("nes_open_us.nes").read_bytes()
build = stack.build(vanilla)                 # StackBuild: .rom and .regions
patch = stack.ips(vanilla)                   # the same build, as an IPS patch
```

Steps are built `ROMPatch` objects, and step names must be unique. A stack is assembled in
Python, or from a [recipe](#recipes) with [`golf-patch`](#golf-patch).

## What a build checks

`build(base)` copies the base, applies each step in order, and raises `StackError` (a
`PatchError`) on the first problem:

- **The base ROM.** By default the base must hash to `rom_utils.US_ROM_SHA1`, the vanilla US
  ROM file including its iNES header. `PatchStack(steps, base_sha1=None)` builds on any
  base, such as a ROM that already carries some of the patches. A recipe passes the hash
  of the ROM its patch types are written for; see [Recipes](#recipes).
- **Requirements.** Before a step is applied, every patch in its `requires` must already be
  applied, by an earlier step or in the base. The error says whether a missing requirement
  is `not in the stack` or `listed after it`. The stack never adds or reorders steps.
- **Overlaps.** Every write is attributed to the step that makes it. A step writing a byte
  that an earlier step wrote is an error, even when the value is the same, and the error
  names both steps and the address. A sub-patch shared by two steps is not an overlap:
  `BytePatch` writes nothing when it is already applied.
- **The steps themselves.** A step whose `apply` raises `PatchError` is reported with its
  name.

Overlap tracking matters most for the writes that do not check what they replace: course
data and scorecard totals in `CoursePatch`, and the scorecard QR image in bank 2.

`StackBuild.regions` maps each step name to the `[start, end)` PRG offset ranges it wrote.

## Requirements and `can_apply`

`ROMPatch.requires` lists patches a patch depends on but does not write. It is separate from
`can_apply`, which checks only the bytes a patch replaces. `apply` on `CompositePatch`,
`CoursePatch` and `ScorecardQrPatch` refuses to run while a requirement is missing, so the
rule holds outside a stack too. The [patch types](#patch-types) table lists each patch's
requirements.

## Writes and IPS output

The build runs on `RomWriter.from_bytes`. Every `RomWriter` write method goes through
`write_prg`, which is how the stack sees every write.

`stack.ips(base)` is `golf.core.ips.diff(base, stack.build(base).rom)`. IPS offsets count
from the start of the file, so the diff covers the whole `.nes` file, iNES header included.
`diff` merges changes separated by fewer than 5 unchanged bytes, writes runs of 14 or more
identical bytes as RLE records, splits records at 65,535 bytes, and never starts a record at
offset `0x454F46` (which reads as the `EOF` marker). The same inputs always produce the same
patch. `ips.apply` reads RLE records and the truncation extension.

## Two-stage artifacts and the finish ABI

The randomizer site builds a seed in two stages. Once, it applies the seed's course and
feature stack to the vanilla ROM and stores that **unfinished artifact** as an IPS. For
each download, it reconstructs the unfinished ROM and applies a much smaller finishing
stack: new-save defaults, then either player-specific QR credentials or the guest QR
disable patch.

Two independent versions describe that boundary:

- `build_version` identifies the recipe that creates the unfinished artifact, as a label
  for what players get. A change that makes seeds play or look different bumps it, as
  does any change to what the finisher or the scan route reads from a stored artifact,
  even when the artifact remains compatible with the same finisher. A change that only
  moves bytes need not (ADR 0024).
- `finish_abi_version` identifies the interface the artifact exposes to a finisher: the
  locations, expected preimages, field widths and meanings that finishing consumes.
  Several build versions may produce the same finish ABI.

The promise of a supported finish ABI is **this release can safely personalize this
stored artifact**. It is not a promise that two releases produce byte-identical finished
ROMs. Compatible refactors, validation improvements and behavior fixes may change the
finisher without changing its ABI.

The current ABI, 2, covers:

- the vanilla SRAM-default locations and bytes that `sram_defaults` replaces, apart from
  the BGM loop edit;
- the new-save options table `extended_sram_defaults` installs at bank 9 `$B531`: four bytes,
  BGM, swing, putt and spin, at the vanilla `$FF $FF $FF $FF`;
- the QR seed ID, player ID and MAC key placeholder locations, sizes, fill and encoding;
- the installed QR patch identity that `qr_credentials` requires;
- the splice `qr_disable` restores for a guest ROM; and
- the QR payload protocol consumed by the submission server.

ABI 1, which every artifact built before build version 4 exposes, is the same without the
options table: its finisher writes BGM with `sram_defaults`' loop edit and cannot write
swing, putt or spin defaults.

Moving or resizing a placeholder, changing a credential's representation, changing the
guest-disable mechanism, changing the QR payload's meaning, or having the unfinished
stack consume a location finishing expects to remain vanilla breaks the ABI. A change
elsewhere in the unfinished recipe at most bumps `build_version`.

Building requires the current build and ABI versions: it must not label an artifact with
an ABI it does not produce. Finishing dispatches by `finish_abi_version`, not by
`build_version`. When the ABI eventually changes, the release can retain the small old
finisher without retaining the old course writer, compressor, imported resources or
other unfinished-build machinery. Unsupported ABIs are refused rather than patched at
guessed locations.

Compatibility is not expressed as a minimum or a numeric build-version range. Such a
range assumes compatibility is chronological and contiguous, while a later builder can
reuse an older ABI. The manifest records the ABI directly instead.

### Testing the ABI

An unfinished IPS contains the randomized course, including source-ROM terrain and green
data, so historical IPS artifacts are not checked into this repository as golden files.
Instead, a unit golden records only the non-course ABI metadata derived from the patch
objects: consumed offsets, lengths, expected bytes, QR identity, guest splice and payload
protocol. A change to that test requires an explicit decision that the change is
compatible or that `finish_abi_version` must be bumped.

Integration tests build an unfinished artifact locally when the vanilla ROM and
rehydrated course data are available, then prove both signed-in and guest finishing
against it. A private deployment may additionally exercise retained historical
artifacts, but repository tests do not need to contain their course bytes.

## Recipes

A recipe is a stack written as JSON (`golf/core/patches/recipe.py`):

```json
{
  "steps": [
    {"patch": "wram_expansion"},
    {"patch": "multi_bank_lookup"},
    {"patch": "course_mirrors"},
    {"patch": "course", "course": "courses/jp/jp_uk"},
    {"patch": "menu_trim", "words": "RANDO GOLF 0001"},
    {"patch": "mercy_tap_in", "mercy_point": 9},
    {"patch": "seeded_wind", "seed": "abc123"},
    {"patch": "practice_swing"},
    {"patch": "round_stats"},
    {"patch": "scorecard_qr"}
  ]
}
```

- `patch` names a patch type in the registry (`golf/core/patches/registry.py`). The other
  keys are its parameters, checked against the type's parameter dataclass: unknown or
  missing parameters and values of the wrong type are errors, and integers may also be
  written as strings in any base Python reads (`"0x78"`). A list of strings may also be
  written as one whitespace-separated string (`"clubs": "1W 3W PW"`), which is how `-p`
  passes one.
- Paths are relative to the recipe file.
- `base_sha1` is optional. Omitted means the vanilla ROM the steps are written for;
  `null` means any base. Each patch type is written for one vanilla ROM (`PatchSpec.base_sha1`):
  the US ROM, except `mario_open_free_play`, which is written for Mario Open Golf
  (`jp_rom_utils.JP_ROM_SHA1`). Steps written for different ROMs are an error unless the
  base is `null`.
- Patch types take concrete values and draw nothing at random, so a recipe and a base ROM
  always build the same ROM.
- `qr_credentials` reads its credentials from a separate file written by
  `golf-qr-credentials`, because the MAC keys are secret; a recipe only names the file.

In Python: `Recipe.load(path)`, `Recipe.from_dict(data, base_dir)`, `recipe.stack(base)`,
`recipe.build_steps(base)` (each patch with its parameters and report),
`recipe.expected_base_sha1()` (the base hash the stack will check),
`recipe.to_dict(base_dir)` and `recipe.save(path)`.

## golf-patch

```bash
golf-patch nes_open_us.nes recipe.json -o out.nes
golf-patch nes_open_us.nes recipe.json --ips out.ips
golf-patch nes_open_us.nes -p wram_expansion -p multi_bank_lookup -p course_mirrors \
    -p course:course=courses/japan -p seeded_wind:seed=abc -o out.nes
golf-patch --list
```

- `-p ID[:key=value,...]` adds a step after the recipe's steps, parsed like a recipe step
  with paths relative to the current directory. A value containing a comma needs a recipe.
- `-o` writes the ROM (default `<rom>.patched.nes` unless `--ips` is given); `--ips` writes
  an IPS patch from the base to the build; `--validate-only` builds in memory and writes
  nothing.
- `--save-recipe PATH` writes the combined steps as a recipe.
- `--any-base` builds on a base other than the vanilla ROM the steps are written for, such
  as the output of `golf-write`.
- `-v` adds each patch type's report: bank usage and scorecard totals for `course`, the per-hole pin and wind
  forecast for `seeded_wind`, track and space usage for `music_import`, new tiles and
  import notes for `signpost_random_banner`, the image location for `scorecard_qr`, the
  seed ID and player IDs for `qr_credentials` (never the keys), and the new-save defaults
  for `sram_defaults`.
- `--list` prints every patch type and its parameters.

`golf-write` remains the tool for writing a course from the editor: it applies the course's
three requirements and the `course` step.

## Patch types

| Patch | Parameters | Requires |
|---|---|---|
| `wram_expansion` | | |
| `multi_bank_lookup` | | |
| `course_mirrors` | | |
| `course` | `course` (a directory) or `holes` (18 files); also writes the scorecard totals | `multi_bank_lookup`, `course_mirrors`, `wram_expansion` |
| `menu_trim` | `words` (default `OPEN GOLF RANDO`; three words of 4-6 renderable characters for the header of the main, player count and course select menus), `choose_clubs` (default true; false also drops CHOOSE CLUBS from the club house) | |
| `scorecard_course_name` | `name` (default `RANDOM`; A-Z, 0-9 and space, at most 13), `title` (optional, replaces `18H STROKE PLAY`; at most 26) | `course_mirrors` |
| `remove_course_banner` | | |
| `signpost_random_banner` | `art`, `banner` (default `us`), `hole` (default 1) | |
| `signpost_color` | `color` (one of the curated NES colors in `SIGNPOST_COLOR_FAMILIES`, `golf/core/patches/signpost_color.py`); recolors the banner's brick, except on contest holes, which the game turns blue | |
| `mercy_tap_in` | `mercy_point`, `mercy_result` (default `mercy_point` + 1) | |
| `seeded_wind` | `seed` | `course_mirrors` |
| `wind_anchors` | `anchors`: 18 `direction/speed` pairs, the direction two hex digits and the speed 0-10, as `80/7` (`docs/seeded_wind.md`, **Wind anchors and profiles**) | `course_mirrors` |
| `wind_fix` | none; wraps the wind's cos lookup inside its table, so all 16 wind directions push the way they point (`docs/wind.md`, **The fix**) | |
| `practice_swing` | `hold_frames` (default `0x78`) | |
| `round_stats` | none; counts fairways hit and penalty strokes in SRAM for the QR payload (`docs/scorecard_qr.md`, Round stats) | `course_mirrors` |
| `scorecard_qr` | none; the seed ID, player ID and MAC key placeholders are left at the fill | `course_mirrors`, `round_stats` |
| `qr_credentials` | `credentials` (a `golf-qr-credentials` file); fills the placeholders, expecting the fill | `scorecard_qr` |
| `qr_disable` | none; reverts the round-end splice for a guest ROM, expecting the splice `scorecard_qr` wrote | |
| `course_theme` | `music` (`$02` US, `$03` Japan or `$04` UK); plays that US ROM theme on every course | |
| `music_import` | `dump`, `track` (optional; one dump music ID, imported as `$03` and made every course's theme), `transpose_adjust` (default from the dump) | |
| `sram_defaults` | `player_name` (A-Z, `.` and space, at most 10), `clubs` (up to 14 of `1W`-`4W`, `1I`-`9I`, `PW`, `SW`, `PT`; the putter is added), `bgm` (default true), `sram_magic` (default `0x3553`, "5S"; neither byte `$00` or `$FF`). To use the extended table, supply all three of `swing` and `putt` (`off`, `slow`, `medium`, `fast`) and `spin` (`off`, `top2`, `top1`, `normal`, `back1`, `back2`). Without them, BGM off uses the vanilla loop edit and cannot follow `extended_sram_defaults`. Only a save being initialized gets these values | `extended_sram_defaults` when swing, putt and spin are supplied |
| `extended_sram_defaults` | none; installs the SRAM defaults routine and table at vanilla values in PLAYER STATS' code space | `menu_trim` |
| `peach_dress` | `color` (one of the curated NES colors in `DRESS_COLOR_FAMILIES`, `golf/core/patches/peach_dress.py`); recolors Peach's dress in the putting view | |
| `course_intro_sky` | `image` (a 256-pixel-wide image whose top 64 rows are the sky, in the three colors of background palette 0; default Mario Open's, `golf/core/patches/data/course_intro_sky.png`); draws that sky over the course name on the course intro scene (`docs/course_intro_scene.md`, **The sky patch**) | |
| `putting_practice` | (experimental) | |
| `mario_open_free_play` | none; all six courses and no score limit (`docs/mario_open_free_play.md`) | |

`course_theme` and `music_import` with a `track` both rewrite `CourseBgmTable` at `$DA14`,
so a stack holds one or the other: `course_theme` for a theme already in the ROM,
`music_import` for one from another ROM's dump.

`remove_course_banner` and `signpost_random_banner` both rewrite the banner selection at
bank 12 `$AC5D`, so a stack with both fails: whichever comes second finds the other's bytes
where it expects vanilla ones.

`qr_credentials` and `qr_disable` rewrite bytes `scorecard_qr` wrote, and
`sram_defaults` with extended options rewrites table bytes `extended_sram_defaults`
wrote. They are finishing patches: build the unfinished ROM with
`scorecard_qr` and `extended_sram_defaults`, then run a second stack with `base_sha1=None`
(`--any-base`) on that ROM. See the two-stage build in `randomizer_devplan.md`.

```bash
golf-patch nes_open_us.nes recipe.json -o unfinished.nes
golf-patch unfinished.nes --any-base -p qr_credentials:credentials=keys.json -o finished.nes
golf-patch unfinished.nes --any-base -p qr_disable -p sram_defaults:swing=off,putt=off,spin=back1 -o guest.nes
```

## Testing

```bash
uv run pytest tests/unit/test_patch_stack.py tests/unit/test_patch_recipe.py tests/unit/test_ips.py tests/unit/test_rom_writer.py
uv run pytest tests/integration/test_patch_stack_rom.py tests/integration/test_patch_recipe_rom.py
```

`tests/integration/test_patch_stack_rom.py` builds a stack of every patch that has no art or
file inputs beyond a music dump - WRAM expansion, the course code and a Mario Open course,
menu trim, banner removal, mercy tap-in, seeded wind, practice swing, the scorecard QR,
SRAM defaults and music import - on the vanilla ROM, then finishes that ROM with
`qr_credentials` and, separately, with `qr_disable`.
