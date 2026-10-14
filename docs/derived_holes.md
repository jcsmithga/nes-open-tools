# Derived Holes

> **Note**: This document was written by Claude.

A derived hole is a vanilla hole with a delta applied: a forward tee that makes a par 5 a
par 3, or a variant with a dogleg redrawn. It is a catalog entry like any other, with its
own id, content hash, par and distance. The repository holds only its delta, the cells
and metadata that differ from the vanilla hole, and the hole itself is built from the
vanilla hole each time it is loaded.

Code: `golf/randomizer/delta.py` (the delta format), `golf/randomizer/derive.py`
(publishing and checking) and `DerivedSource` in `golf/randomizer/catalog.py`.
Decisions: ADR 0022 (the entry and its delta) and ADR 0023 (how a seed asks for them).

## The entry

```json
"community/nes_us_12_forward3": {
  "source": {"base": "nes_us/12", "delta": "derived/community/nes_us_12_forward3.json"},
  "content_hash": "0bb70add…",
  "par": 3,
  "distance": 190,
  "author": "jdharms"
}
```

- **`base`** is a hole id, so it names one version of the base forever. A corrected
  `nes_us/12@2` leaves every hole derived from `nes_us/12` as it was. The base is a
  vanilla hole: a derived hole cannot be the base of another.
- **`delta`** is the delta file, relative to the directory the index is in. A delta is
  written once and never edited; a changed hole is a new version with a new delta,
  `nes_us_12_forward3@2.json`.
- **`content_hash`** is the hash of the hole the base and the delta build, the same hash
  every other entry has ([catalog.md](catalog.md)). `HoleStore.load` checks the base
  against its own hash, applies the delta and checks the result, so an edited delta or a
  changed base is refused.
- **The lineage** is `<owner>/<slug>`: the owner is `community` or one author's name, and
  the entry's `author` holds the credit either way. A vanilla course's owner (`nes_us`,
  `jp_uk`) is refused. By convention the slug is the base's lineage, what moved and the
  par: `_forward` for a tee moved up, `_short` for a green moved down or both moved, as
  in `community/nes_us_12_forward3`. Because the slug names the par, every version of a
  lineage has its first version's par, and a hole of another par is a lineage of its
  own.

A derived hole comes from its base's ROM. It is in a pool only when that ROM is among the
seed's `sources`, a seed that draws one requires that ROM, and `golf-rehydrate` and the
site's start-up check build and verify it along with the ROM's vanilla holes. A derived
hole whose base is withdrawn cannot be built and is not drawable.

## The delta

A delta is data, never operations. Carving forest for a tee box, cropping rows and
redrawing the bottom edge are all done in the editor, and the delta records the result:

```json
{
  "format": 1,
  "base": "nes_us/12",
  "metadata": {
    "par": 3,
    "distance": 190,
    "scroll_limit": 1,
    "tee": {"x": 120, "y": 190}
  },
  "terrain": {
    "rows": 30,
    "cells": [
      [23, 14, "35 36"],
      [28, 2, "A3 A0 A1 A2 A3 A0 A1 B2 B7 82"]
    ]
  },
  "attributes": {
    "rows": 15,
    "cells": []
  }
}
```

| Field | Holds |
|---|---|
| `format` | The delta format, 1 |
| `base` | The base's id, which must be the entry's |
| `row_offset` | For a hole cropped or extended at the top: how many terrain rows the base moved up. Left out when 0 |
| `metadata` | The values that differ, each replacing the base's whole: `par`, `distance`, `handicap`, `scroll_limit`, `green`, `tee`, `flag_positions` |
| `terrain`, `attributes`, `greens` | For a grid that differs: the `rows` it ends with, and the `cells` that differ |

A run of cells is `[row, column, values]`: terrain and greens tiles as two-digit hex,
attribute palettes as decimal, separated by single spaces. Runs are in order and never
overlap.

- **Both holes are compared in canonical form**, the fields the content hash covers, with
  terrain cut to its visible height. A delta never mentions hidden rows, the hole number
  or `_debug`.
- **Rows are counted from the top.** A hole cropped at the bottom keeps every remaining
  cell's position and records only its new row count.
- **A hole cropped at the top records a `row_offset`.** The derived hole's terrain row
  `r` is compared with the base's row `r + row_offset`: 10 says the base's top 10 rows
  are gone, and -2 that two rows were added above it. A top crop therefore costs an
  offset and no cells, as a bottom crop costs a row count. The offset is even, since a
  palette covers two terrain rows, and attribute row `r` is compared with the base's
  `r + row_offset / 2`. The greens grid is the green's own and never moves; the green's
  and the tee's new `y` are metadata. `diff` takes the offset that leaves the fewest
  cells to write, and 0 unless another beats it.
- **A row with no base row is written whole**, past either end of the base.
- **Only cells that differ are recorded.** `is_minimal` holds a delta to that, and
  `golf-derive check` and the tests run it, so a delta cannot restate its base.
- **A hole stays in register with its base** apart from that one move by whole rows.
  Moving a hole sideways, or one part of it against another, changes nearly every cell
  it touches, and the delta would then be the base hole displaced. `golf-derive`
  reports a delta that rewrites more than 35% of the terrain the two holes share and
  refuses one over 60% unless given `--allow-large`.

## Publishing one

```bash
cp courses/us/hole_12.json courses/scratch/us_12_forward3.json
golf-editor courses/scratch/us_12_forward3.json
golf-derive new nes_us/12 courses/scratch/us_12_forward3.json community/nes_us_12_forward3 --author jdharms --dry-run
golf-derive new nes_us/12 courses/scratch/us_12_forward3.json community/nes_us_12_forward3 --author jdharms
```

Everything under `courses/` is ignored by git, so an edited copy of a vanilla hole can
live there. `golf-derive new` then:

1. Drops what the editor hid. Removing rows in the editor keeps their terrain and
   attribute rows so they can be restored; terrain past the visible height never reaches
   a ROM, but every attribute row does, so the hidden ones are cut.
2. Refuses a hole no seed could build. Every value `CoursePatch` writes is checked
   against what its table entry holds: a height that is even and 30-60 rows, a
   `scroll_limit` of `(height - 28) / 2`, a par of 3, 4 or 5, a distance of 1-999, a
   handicap of 1-18, 22-wide terrain and 24 x 24 greens of byte tiles, one attribute
   row of palettes 0-3 for every two terrain rows, a tee inside the hole, and four pins.
   The hole is then compressed as a build compresses it.
3. Takes the delta, rebuilds the hole from it and checks the two agree.
4. Refuses a new version whose par is not its lineage's, and a hole identical to its base, to its lineage's newest version or to a hole
   of another lineage. An older version's content can be published again, which is how
   a version is undone ([catalog.md](catalog.md)); a withdrawn version's cannot.
5. Runs every transform over the hole (`mirror@1`, and each seeded one with three seeds)
   and refuses the hole if one raises. A transform's output is not checked or pinned:
   a seed stores its transformed holes as built (ADR 0021).
6. Writes the delta under `derived/<owner>/` beside the index, adds the entry, bumps the
   index version, and writes the curation record.

**Curation.** The first version of a lineage is put in its base's family, which is
created and named after the base (`nes_us_12`) when the base has none, and is tagged
`short` when its par is lower than its base's. `--family` and `--tags` set either
(`--tags ''` for none). A later version keeps the lineage's record unless they are given.

Giving a base its first family is a curation edit like any other: the same holes stay in
every pool, as likely as before, but the base's key in the pool changes, so a PRNG seed
no longer draws the course it drew before.

| Command | Does |
|---|---|
| `new <base> <hole.json> <lineage> --author A` | Publish, as version 1 or the lineage's next version. `--dry-run` reports without writing |
| `export <id> -o <hole.json>` | Write a derived hole out as a hole file, to edit toward its next version |
| `show <id> [--delta]` | Its base, its metadata changes and how many cells each grid changes |
| `check` | Every derived hole builds to its hash, holds only cells that differ, and takes every transform |

`--catalog`, `--curation` and `--holes` point at an index, curation file or hole store
other than the checked-in ones; deltas are read and written beside the index given.

## Drawing them

A derived hole is in no pool by default. A seed's settings name the kinds of hole added
to the vanilla ones in `include` ([manifest.md](manifest.md)), and only a pool that
includes `derived` holds them:

```bash
golf-randomize generate --include derived
golf-randomize generate --include derived --exclude-tags short
```

The site's form does not offer `include`, so no seed made on the site has one.

A derived hole is published into its base's family, so unless the seed allows family
repeats a course holds a hole or a hole derived from it, not both, and a family with a par 5 and its par 3 can fill either kind of slot.
Each derived hole of another par therefore raises how often its design appears; how
families of mixed par should be weighted is open (`docs/thoughts_on_par_6.md`).

## What does not cover them

- **The rangefinder** renders the vanilla courses only. A seed's yardage book shows a
  derived hole as it does any other.
- **Difficulty.** `data/difficulty/holes.json` has no row for a derived hole, and the
  solver is paused (ADR 0012).
- **The transforms' golden hashes** cover the vanilla holes only.

## Tests

- `tests/unit/test_delta.py`: what a delta records, crops and added rows at either end,
  and every malformed delta `apply` refuses.
- `tests/unit/test_derive.py`: publishing, versions, curation, the refusals, the store
  and index resolving a derived entry, and the CLI, over made-up holes.
- `tests/meta/test_derived_holes.py`: every derived entry's delta is checked in where
  `golf-derive` writes it, no delta is left without an entry, and none takes a vanilla
  lineage.
- `tests/integration/test_derived_holes.py`: the checked-in derived holes build and take
  every transform, a forward tee of the US 12th is published, drawn and built into a
  ROM, and the same hole cropped at the top is published as an offset. It needs the vanilla holes.
