+++
status = "accepted"
date = 2026-11-15
area = "randomizer"
permanent = false
revisit_when = "a hole worth publishing needs a green or pin closer to an edge than any vanilla one, or a patch changes how the putting view treats the screen's edges or a ball just off the putting surface"
drafted_by = "Claude"
supersedes = []
+++

# Publishing refuses a green outside the vanilla greens' safe zone, or a pin without putting surface all round it

## Context

The vanilla course data is no longer published, so contributors start holes from
scratch in the editor (New, `HoleData.blank()`), not from a vanilla hole that already
works. The checks a published hole had to pass (`check_hole`, now `hole_problems` in
`golf/formats/hole_check.py`) asked only whether a ROM can hold the hole: table ranges,
grid sizes, tile values, four pins with byte offsets, and that the hole compresses. A
hole could pass all of them and still play badly or wrongly:

- **A putting surface at the edge of the green's 24 x 24 grid.** The top and right
  edges of the putting view are the screen's, so surface there can sit in a CRT's
  overscan, where the game may ask the player to putt from somewhere they cannot see.
  The left and bottom edges meet the HUD. On every side, a ball carried a pixel off a
  surface with no fringe round it is sent back to the chipping setup.
- **A pin off the putting surface**, or right at its edge. Nothing checked where a pin
  is in relation to the green it is on.

A scan of all 144 vanilla greens (NES Open's 54 and Mario Open's 90) on 2026-11-15
found:

- **Margins.** Measured to the putting surface (any tile that is neither rough nor
  fringe), every green keeps at least 3 tiles from the top edge and 2 from the right,
  bottom and left. Measured to the fringe, the minimums are 2, 1, 0 and 0. Where the
  fringe reaches an edge, the tile there is one of its rounded outer corners (`$4C`,
  `$4F`, `$50`, `$56`), mostly rough pixels.
- **Pins.** All 576 pins sit on a putting surface tile whose eight neighbors are putting
  surface too; the nearest fringe or rough tile is never closer than 2 tiles.

## Decision

The checks a published hole must pass include the green's playability, not only what a
ROM can hold, and violations block publishing.

- **Safe zone.** At least 3 tiles of fringe or rough between the putting surface and the
  top of the green's grid, and 2 at the right, bottom and left (`GREEN_SAFE_ZONE`).
- **Pins.** Each pin's tile, the greens pixel (`x_offset`, `y_offset`) divided by 8, is
  putting surface, and so is every tile within `PIN_CLEARANCE` (1) of it, diagonals
  included.
- **No putting surface** is one problem, "the green has no putting surface", rather than
  one per pin.
- **Thresholds are the vanilla minimums**: the most liberal values every vanilla green
  passes. A test (`tests/integration/test_hole_check_vanilla.py`) runs every check on
  every vanilla hole, so a rule no vanilla hole could pass fails there.
- The rules are part of `hole_problems`, so the editor's Check, its warning on save,
  `golf-derive` and `golf-community` all apply them. Saving in the editor warns but
  never refuses.

## Rejected alternatives

- **Warn, but publish anyway.** Keeps Check about what a ROM can hold, but a hole that
  plays badly would reach seeds. Refusing is the safer default while the catalog has
  few community holes; it can be loosened if it turns away holes worth having.
- **Measure the safe zone to the fringe.** The vanilla minimums would be 0 at the
  bottom and left, which constrains nothing there. The putting surface is what the
  player putts on, and its minimums hold on every side with no exceptions.
- **Tighter thresholds**, such as the usual margins (top 4, right 3, bottom 2, left 2 to
  the surface). Several vanilla greens would fail, and the aim is to refuse only what no
  vanilla green does.
- **Pins need only be on a putting surface tile.** More liberal, but it allows a pin
  against the fringe, which no vanilla green has; the ring matches how the safe zone's
  thresholds were set.
- **A blank hole that passes Check**, with a starter putting surface. It would have no
  fringe until painted over with the Green Brush. A new hole fails Check with the one
  problem instead, which is true of it.

## Consequences

- A contributor learns about a bad green from the editor's Check before a hole is sent,
  and publishing cannot let one into the catalog.
- Every vanilla hole passes, so derived holes, which keep their base's green, pass unless
  their edits move a pin or the surface.
- Test holes need a putting surface round their pins: `synthetic_hole` has a flat one,
  and tests that paint greens start from `HoleData.blank().greens`.
- A green as tight as vanilla allows, but on a side vanilla never combined with another,
  still passes: the rules are per side and per pin, not a whole-green shape.

## Sources

- `golf/formats/hole_check.py` (`green_problems`, `GREEN_SAFE_ZONE`, `PIN_CLEARANCE`)
- `docs/editor.md` (Check), `docs/derived_holes.md`, `docs/putting_view.md` (the cup's
  position)
- `tests/unit/test_hole_check.py`, `tests/integration/test_hole_check_vanilla.py`
- Claude Code session 8871441a, 2026-11-15: the scan of the vanilla greens and pins, and
  jdharms's call to block publishing at the most liberal thresholds every vanilla green
  passes
