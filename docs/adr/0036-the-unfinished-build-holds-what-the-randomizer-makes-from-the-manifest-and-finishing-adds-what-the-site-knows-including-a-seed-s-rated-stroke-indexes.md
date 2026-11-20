+++
status = "accepted"
date = 2026-11-20
area = "randomizer"
permanent = false
revisit_when = "a seed's ratings can change after it is stored, or finishing needs a seed fact the site could change between downloads"
drafted_by = "Claude"
supersedes = []
+++

# The unfinished build holds what the randomizer makes from the manifest, and finishing adds what the site knows, including a seed's rated stroke indexes

## Context

The site rates every seed when it stores it (ADR 0034), and the seed page, the net card
and match play give strokes by the stroke indexes it rated. The ROM did not show them.
`CoursePatch` writes each hole's own `handicap` into the handicap table at `$DDDD`, the
scorecard's handicap column, so a seed's ROM showed the stroke indexes its holes had on
their home courses: rarely a ranking of 1 to 18, since holes from different courses can
share a value, and never the seed's.

The rating happens after the build. `SeedBuilder.build` makes the unfinished IPS, then
`insert_seed` rates the seed in the transaction that stores it. Writing the stroke
indexes in the unfinished build was worked through first: the rating reads each built
hole's content hash, which exists only once the transforms have run, so the build would
have to apply the transforms, call back into the site to rate the holes, write the
result into the manifest, and only then run the patches. The manifest would gain a field
that generation never decides, and the build an input from the site's database.

The rule that had seemed to apply, that everything the same across downloads belongs in
the unfinished IPS, was not the rule the code followed. Finishing already writes the
seed's `qr_seed_id`, which the site assigns when it stores the seed, which never changes,
and which is in neither the manifest nor the IPS.

> the unfinished patch is **only** ROM information that is consistent across downloads,
> but finishing may also contain some information that is not download-specific.

Every finishing write until now was a `BytePatch` whose preimage is known bytes: vanilla
save defaults, the options table's `$FF` fill, the QR placeholders' `$00` fill. The
handicap table's preimage is whatever the seed's holes carried, different on every seed.

## Decision

- **The boundary.** The unfinished build holds what the randomizer makes from the
  manifest, the catalog and the vanilla ROM, with no site. Finishing adds what the site
  knows: the player's options and credentials, and facts the site stores about a seed
  once and never changes. Those are the seed's `qr_seed_id` and its rated stroke indexes.
- **Stroke indexes are not in the manifest.** They could be, but the generator would
  then depend on the ratings, which it has no need for, as it has none for the QR seed
  id.
- **Finishing writes a rated seed's stroke indexes.** The download routes read them from
  `seed_hole_ratings` (`seed_stroke_indexes` in `server/ratings.py`) and pass them to
  `finish`, which adds the `stroke_indexes` step
  (`golf/core/patches/stroke_indexes.py`) after the new-save defaults, for guests and
  signed-in players alike. An unrated seed, or a finish with no site, passes none, and
  its ROM keeps the holes' own handicaps.
- **The step checks that the bytes look like handicaps.** It writes course 1's 18
  entries of the table, which every course slot plays under `course_mirrors`. It refuses
  unless each byte there is 1 to 18, rather than checking particular values, and writes
  only a ranking of the 18 holes. The rest of the finishing stack already refuses a ROM
  that is not an unfinished image: `qr_credentials` needs its placeholder fill and the QR
  patch's identity, and `qr_disable` its splice. Overwriting stroke indexes is harmless,
  so the step does not refuse a ROM already finished with others.
- **No finish ABI bump.** Every artifact ever stored, under ABI 1 or 2, leaves bytes that
  look like stroke indexes in those 18 bytes: `CoursePatch` has written the handicap
  table since 1.0. The interface is unchanged; the finisher uses more of it.

  > before today we expected the unfinished stack to leave "stroke index looking bytes"
  > in 18 specific bytes, and after today we expect the unfinished stack to leave "stroke
  > index looking bytes" in the same 18 specific bytes. The finishing stack doing *more*
  > isn't in itself an ABI bump

  Both ABI contract tests in `tests/unit/test_build.py` pin the table's location, width
  and the values accepted there, as literals, as ADR 0009 asks of everything finishing
  relies on; `tests/integration/test_build_rom.py` checks that the unfinished build
  leaves each hole's own handicap there.
- **A seed's ratings are never rewritten.** A download's ROM depends on it. `rate_on`
  skips a rated seed, and `tests/unit/test_server_ratings.py` checks that a later refit
  leaves a seed's rating as it was.

This revises ADR 0024's account of what a download reads: from the database, the seed's
stored stroke indexes as well as its `qr_seed_id` and the entry's keys.

## Rejected alternatives

- **Write them in the unfinished build.** It keeps the IPS the one place a seed's shared
  ROM bytes live, fixed by construction. But it needs the build to rate the seed partway
  through, a manifest field generation does not decide, a schema and build version bump,
  and a change to every caller that creates a seed. A seed made with no ratings run, and
  every seed stored before, would carry the holes' own handicaps for good.
- **Bump the finish ABI.** New seeds would get a new number for an artifact with the same
  interface, which marks when a seed was made rather than what it exposes, and a rolled
  back release would refuse to finish every seed made since. Without a bump, an older
  release finishes them with the holes' own handicaps.
- **Check the preimage against the holes' own handicaps.** Exact, but finishing would
  load the seed's 18 holes from `hole_data` on every download, to guard bytes whose
  overwriting does no harm.
- **Have the unfinished build leave a known fill there.** It would give the step a known
  preimage, but finishing would then always have to write something, the holes' own
  handicaps for an unrated seed included, and an unfinished ROM would show nonsense.
- **Rebuild stored seeds' IPS.** Not needed: finishing gives every seed, old or new, its
  stroke indexes on its next download.

## Consequences

- A rated seed's ROM shows the same stroke indexes as its page, whenever it was made. A
  seed made with no ratings run shows its holes' own until the next refit rates it, and
  its rated ones from then on: the one way a seed's finished ROM changes between
  downloads other than by player.
- What a player's ROM held is the stored IPS, the finish inputs and the seed's stored
  ratings, not the IPS alone.
- A tool that re-rates seeds, which nothing does, would change every later download of
  them. It would need its own decision.
- `golf-randomize` finishes with the holes' own handicaps, as it has no ratings.
- A new seed fact for the ROM goes in finishing only if the site stores it once and never
  changes it; anything generation decides goes in the manifest and the unfinished build.

## Sources

- `docs/patch_stack.md` (Two-stage artifacts and the finish ABI), ADR 0024 and ADR 0034.
- The code: `golf/core/patches/stroke_indexes.py`, `finishing_steps` and `finish` in
  `golf/randomizer/build.py`, `seed_stroke_indexes` in `server/ratings.py`.
- Design session with jdharms, 2026-11-19 to 2026-11-20 (session
  01Rn2bVwqEjFFxVCsBBDdYDd), from a report that a daily's ROM showed vanilla handicaps.
