# Hole Transforms

> **Note**: This document was written by Claude, from a proof of concept and its review
> with jdharms.

A hole transform is a deterministic rewrite of a catalog hole, named in a manifest slot's
`transforms` list (`docs/manifest.md`) and applied in order before `CoursePatch` packs
the course (`unfinished_steps` in `golf/randomizer/build.py`). Generation adds none yet:
only hand-written manifests carry them, and `golf-transform` applies them to hole JSON for
inspection and for the editor.

| Name | Effect |
|------|--------|
| `mirror@1` | Flips the hole left to right: terrain, attributes, greens, green box, tee and pins |
| `hazards@1:<seed>` | Each bunker or water group becomes water with p = 0.35, otherwise sand, whatever it was |
| `hazards-weighted@1:<seed>` | Each group flips to the other kind with p = 0.3 (sand) or 0.5 (water), scaled by `1000 / pixels` above 1000 pixels |

## Layout

- `golf/algorithms/mirror.py` and `golf/algorithms/hazards.py` are the operations, plain
  functions from `HoleData` to a new `HoleData`, usable by the editor as well.
- `golf/randomizer/transforms.py` names them: the `TRANSFORMS` registry, parsing and
  `apply_transforms`. A manifest validates its transform names when it loads, so an
  unknown name or a bad seed fails then, not at build time.

## Names and versions

A name is `name@version`, with `:<seed>` after it for a seeded transform. A seed is a
decimal integer from 0 to 2^32 - 1 without leading zeros, so each seed has one spelling.

A golden test (`tests/unit/test_transforms.py`) hashes each transform's output for
every vanilla hole, so a change that alters it is noticed. A change made on purpose
updates the digests; the transform's own version is bumped only if what it means
changed, for example a hazard style's probabilities. Neither bumps `BUILD_VERSION`: a
seed's transformed holes are stored with it when it is built (ADR 0021), so a later
release transforming a hole differently changes no existing seed (ADR 0024). A version
stays in the registry after a newer one ships, because the site still loads manifests
naming it.

Order matters: `mirror@1` then `hazards@1:N` is a different hole from the reverse,
because the hazard rolls follow reading order. The manifest records the order, so builds
reproduce; choosing a canonical order is generation's job.

## Mirror

- **Tile partners.** `data/tables/mirror_tiles.json` maps each terrain and greens tile
  to its left-right partner. It was curated by hand from Claude's suggestions, and
  nothing generates it. Every map is its own inverse. A tile with no partner is an error
  naming its row and column.
- **Partners are for play and looks, not pixels.** Greens partners are chosen for the
  slope they play as (`$31` right slope and `$33` left slope). 48 of the 113 terrain
  partners are not pixel-exact flips, because the tileset has none: half the lips in
  `$40`-`$55`, most out-of-bounds borders `$80`-`$9F`, the tree edges `$BC`-`$BF`, and
  `$3E` and `$3F`, which map to themselves. The lie comes from the drawn tile and the palette, so a mirrored hole
  plays as it looks.
- **Forests are filled again, not mapped.** The forest tiles with trees, `$A0`-`$BB`,
  have no partners: the four fill tiles repeat in a pattern that only runs one way, so
  a reversed row is not a legal tiling. Each becomes the forest placeholder and
  `ForestFiller.fill_all` (`golf/algorithms/forest_fill.py`, `docs/forest_notes.md`)
  fills the forest again. The fill adds bare `$3F` inner-border tiles where it must:
  19-34 per course over the eight dumped courses, more than vanilla has.
- **Widths line up.** Terrain is 22 tiles wide and greens 24, both even, so reversing
  each attribute row keeps every supertile over its own tiles. Reversing a greens row
  moves every tile to the other column parity, so the rough checkerboard tiles `$29` and
  `$2C` are partners, which keeps the pattern in phase.
- **Coordinates**, from the code that reads them:
  - The green box is a left edge, 24 pixels wide: `x' = 176 - 24 - x`.
  - The tee: `InitHole` starts the ball at the tee's x with fraction `$80`, the middle of
    the pixel (bank 13 `$8173`), and draws the tee blocks at x - 11 and x + 4, centered
    on the same point (`$8F18`). So `x' = 175 - x`.
  - A pin offset `o` is eighths of a pixel into the green box, plus a fixed `$28/256`
    (`$DB3F`). `o' = 189 - o` puts every pin in the mirrored pixel column, within 1/16
    pixel of the exact mirror. Vanilla offsets run 36-144.
  - `y` values, distance, scroll limit and the rest of the metadata are unchanged.
- **Not mirrored.** Only what `HoleData` carries is mirrored, which covers every
  per-hole table at `$DD05`-`$E02F`. The rangefinder's renders show the unmirrored
  hole; a seed's yardage book shows the hole as transformed (`docs/yardage_book.md`).

## Hazards

- **Features from color 3.** The four terrain palettes differ only in color 3, and only
  feature tiles and the tee box draw with it, so a feature is a 4-connected area of
  color-3 pixels (`golf/algorithms/features.py`), with the tee box left out. No tile
  holds two features, but two adjacent tiles of the same kind can belong to different
  features, and only the black outlines at pixel level tell them apart.
- **The palette is the lie.** The ROM takes the lie from the supertile palette
  (`LEFA7`): 1 is fairway, 2 bunker, 0 or 3 water. So sand becomes water by changing
  attributes only, and no terrain tile changes. A feature's kind is the palette under
  most of its pixels.
- **Groups.** Features that share a supertile share its palette, so `feature_groups`
  merges them and each group is redrawn as one. A group that changes kind has every
  supertile it covers repainted, 2 for sand and 3 for water; palette 0 is never written,
  since its color 2 is the HUD text (`docs/seasonal_terrain.md`). A group that keeps its
  kind keeps its palettes, so vanilla palette-0 water stays 0. Groups containing
  fairway, or both sand and water, are left alone.
- **A stable roll stream.** `random.Random(seed)` is rolled once for every group in
  order, before deciding whether to skip it, so a skipped group never shifts the rolls
  of the groups after it.
- **Calibration.** 0.35 is vanilla's water share: 195 of the 548 hazard groups across
  the eight dumped courses. The weighted style's 0.3, 0.5 and 1000 pixels were picked by
  hand.
- **Not checked.** Nothing checks playability, such as water across the only route to
  the green or in front of the tee. Par, handicap and the difficulty ratings ignore the
  change.

## Adding a transform, a style or a version

Each hazard style is its own transform rather than an argument to one, so each style's
versions move independently and players can be offered several. `redraw_hazards` holds
what the styles share; a style is a `Draw` function in `golf/algorithms/hazards.py`.

- **A new style:** write its `Draw` beside `uniform` and `weighted`, register it in
  `TRANSFORMS` as `name@1`, and add its golden digests.
- **A new version of a transform:** register `name@2` beside `name@1` and add digests
  for it. `name@1` keeps its own code only while that
  is cheap; it must keep parsing, since stored manifests name it.
- **Any change to shared code** (the forest fill, feature detection, the mirror table)
  that moves a digest: check the new output is what was meant, and update the digests.

## Inspecting

```bash
uv run golf-transform --list
uv run golf-transform courses/us/hole_05.json mirrored.json mirror@1
uv run golf-transform courses/uk/ courses/uk-wet/ hazards@1:42
uv run golf-write nes_open_us.nes courses/uk-wet/ -o uk-wet.nes
```

`golf-editor <hole.json>` opens a written hole directly.
