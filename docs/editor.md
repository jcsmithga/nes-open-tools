# Using the Course Editor

> **Note**: This document was written by Claude.

`golf-editor` edits one hole file at a time. How the editor is built, and how to add a
tool to it, is in `editor/CLAUDE.md`; the hole file itself is described in
[course_data.md](course_data.md).

## Files

| Toolbar | Key | Does |
|---------|-----|------|
| New | Ctrl+N | Start a blank hole (below), with no file until it is saved |
| Load | Ctrl+O | Open a hole file; dropping one on the window does too |
| Save | Ctrl+S | Save to the hole's file, asking for one if it has none |
| Save As | Ctrl+Shift+S | Save to a new file, which the editor then works on |
| Check | | List everything that keeps the hole out of a ROM |

New, Load and quitting ask first when there are unsaved changes.

## Check

Check runs the checks publishing a hole runs (`golf/formats/hole_check.py`), on the hole
without the rows the editor has hidden: an even height of 30-60 rows and the scroll limit
that goes with it, every tile a real one (no placeholder left), par 3-5, distance 1-999,
handicap 1-18, the tee inside the hole, four pins, and that the hole compresses. Saving
runs them too, and says on the status bar how many problems there are; it saves either
way, so work in progress can always be kept.

## Starting a hole from scratch

New makes a hole that passes Check but has nothing on it: 30 rows of deep rough (`$DF`)
on palette 1, a green that is all rough, the green's box at the top in the middle, the
tee near the bottom, and the four pins near the middle of the green. A hole then goes
roughly:

1. **Length.** `=` and `-` add and remove two rows at the bottom, 30 to 60; the scroll
   limit follows.
2. **Out of bounds.** The Out of Bounds Brush (`O`) paints out-of-bounds ground and
   draws its line, leaving placeholder inside it. Seed clearings if you want them, then
   Forest Fill (`F`) fills the placeholder with forest.
3. **Fairways and hazards.** The Feature Brush (`B`) paints fairway on palette 1, bunker
   on 2 and water on 3, and chooses the border tiles. Built-in stamps (`K`) hold every
   vanilla bunker and water hazard shape, and the tee boxes and single trees.
4. **The green.** In greens mode (Tab), the Green Brush (`N`) paints the putting surface
   and its fringe; Carpet Paint (`V`) paints slopes onto it without touching the fringe.
   Green Fix (`U`) tidies the rough round it.
5. **Positions.** The Position tool (`R`) moves the tee and the green's box in terrain
   mode and the four pins in greens mode: Tab or `[`/`]` chooses one, the arrow keys move
   it. F1-F4 show each pin.
6. **Par, distance and handicap.** The metadata dialog (`D`); the Measure tool (`M`)
   measures yards between points.
7. **Check**, and Save.

[feature_brush.md](feature_brush.md) covers the three brushes, and
[forest_notes.md](forest_notes.md) Forest Fill. A finished hole reaches the randomizer as
a community hole ([community_holes.md](community_holes.md)).
