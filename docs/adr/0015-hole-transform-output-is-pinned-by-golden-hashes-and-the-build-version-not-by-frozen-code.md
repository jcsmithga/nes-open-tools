+++
status = "superseded"
date = 2026-10-14
area = "randomizer"
permanent = false
revisit_when = "the site rebuilds a stored seed from its manifest instead of serving its stored unfinished IPS, or one release must build more than one unfinished build version"
drafted_by = "Claude"
supersedes = []
superseded_by = 24
+++

# Hole transform output is pinned by golden hashes and the build version, not by frozen code

## Context

A manifest slot names hole transforms (`mirror@1`, `hazards@1:<seed>`), and the site
stores those names in `seed_holes`. A seed's course must not change after it is
generated. A transform's output depends on more than its own module: `mirror@1` runs the
forest fill, which the editor shares and will keep improving, and reads
`data/tables/mirror_tiles.json`. The hazard transforms depend on feature detection,
`data/chr-ram.bin` and `random.Random`. The proof of concept on the `hazard-shuffle`
branch promised that a shipped version's output never changes, enforced nothing, and
suggested `mirror@1` might need its own frozen copy of the fill.

`build_version` already names the recipe from a manifest to the unfinished ROM, and
must be bumped whenever an imported build resource could change the bytes
(`docs/manifest.md`). The site serves each seed's stored unfinished IPS and never
rebuilds a seed from an older build version.

## Decision

A transform's output only has to hold steady within one `BUILD_VERSION`. A test pins
it: `tests/unit/test_transforms.py` hashes every vanilla hole after each registered
transform (two seeds for the seeded ones) and compares against checked-in digests.
When a digest changes, `BUILD_VERSION` is bumped. The transform's own version (`@N`) is
bumped only when what the transform means changes, such as a hazard style's
probabilities, not for incidental changes like a better forest fill.

The transform code is shared with the editor and not copied. A version stays in the
registry after a newer one ships, because the site still loads manifests that name it.

## Rejected alternatives

- **A frozen copy of each dependency per transform version.** `mirror@1` would carry
  its own forest fill, and every later fix would need a new copy. It duplicates code
  to guarantee rebuilds the site never does.
- **No pinning.** Nothing would catch an editor change that silently alters the course
  a manifest builds.
- **Bumping the transform version for every output change.** That spreads
  `build_version`'s job across every transform name, and the old version would have to
  keep its old code to mean anything.

## Consequences

- An improvement to the forest fill, feature detection or the mirror table fails the
  golden test, and is shipped by bumping `BUILD_VERSION` and updating the digests.
- Within one release only the current version of each transform's code exists, so
  rebuilding a manifest from an older build version is not possible, as it already is
  not for every other build input.
- The golden test needs both vanilla ROMs dumped (`vanilla_courses`,
  `vanilla_jp_courses`); it skips without them, like every other vanilla-data test.

## Sources

- `docs/hole_transforms.md`, `docs/manifest.md`
- `golf/randomizer/transforms.py`, `tests/unit/test_transforms.py`
- Session with jdharms, 2026-10-03 (session 019pV1kQ): reviewing the proof of concept,
  and jdharms's choice of golden hashes as the tripwire.
