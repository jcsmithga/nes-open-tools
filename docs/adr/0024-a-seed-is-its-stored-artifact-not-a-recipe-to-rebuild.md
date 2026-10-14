+++
status = "proposed"
date = 2026-10-14
area = "randomizer"
permanent = false
revisit_when = "the site needs to rebuild a stored seed, such as to repair a bad unfinished IPS in place rather than publish a new seed, or stats need to tell apart holes that share a lineage and transforms"
drafted_by = "Claude"
supersedes = [15]
+++

# A seed is its stored artifact, not a recipe to rebuild

## Context

The randomizer was designed around a seed being a recipe: a manifest of hole ids resolved
through a frozen catalog, which had to stay rebuildable so that a seed downloaded months
later matches what other players got (`docs/randomizer.md`, Appendix C). The site then
started storing each seed's unfinished IPS and finishing it per download, and it never
rebuilds a seed. `build_unfinished` refuses any `build_version` but the current one, so
neither the site nor `golf-randomize build` can.

> I now believe the stored IPS provides that fairness guarantee by itself, and that many
> guarantees upstream of it exist only to keep rebuilding safe, which nothing does any
> more.

An audit of what reads a seed after it is created found:

- **A download** reads the stored IPS and, from the manifest, `finish_abi_version`,
  `sram_magic` and the club rules, with `qr_seed_id` and the entry's keys from their rows.
  `required_roms` also reads each hole's catalog entry.
- **The seed page** reads the catalog entry of each hole id for its distance, author and
  source, and the current curation for its display name.
- **The yardage book** reads a transformed slot's hole from `hole_data` (ADR 0021), and
  any other slot's from the catalog and hole store, checked against its content hash.
- **The round, account and admin pages** parse the whole manifest for its magic words
  and pars.
- **A scan** reads its payload, the seed by `qr_seed_id`, the entry's keys and
  `seed_holes.par`.

Nothing reads a seed's `settings` to generate it again, and nothing rebuilds its
unfinished ROM. Several guarantees exist only for that:

- the same catalog, curation and settings regenerating the same manifest under one
  `generator_version` (`docs/manifest.md`), which also cannot hold in practice, since
  curation is edited freely and a manifest records only its stamp;
- `build_version` bumped for any change that could alter the unfinished ROM's bytes;
- each transform's output pinned by golden hashes, any change to them bumping
  `BUILD_VERSION` (ADR 0015);
- a catalog id resolving to the same hole data forever, and no committed entry ever
  changed (`docs/catalog.md`), which, since vanilla hole data is not committed, also
  freezes the dumpers and the canonical hole form;
- deltas never edited, and the delta format permanent (ADR 0022);
- a strict reader for every manifest schema, which keeps every transform name registered
  and makes each new settings field a schema bump with its own loader branch, as
  `include` (ADR 0023) was.

The catalog machinery has not yet been used: the index is at version 1, with 144
vanilla holes and none withdrawn, derived or community.

## Decision

A seed's identity is what the site stored when it was created: the manifest text as
issued, the unfinished IPS, its rows in `seed_holes` and the built holes in `hole_data`.
Nothing upstream of those has to reproduce them.

- **No regeneration.** Generation is deterministic in its inputs within one release,
  which the tests check and `golf-randomize generate --seed` relies on, but nothing
  promises that a later release generates the same manifest from the same settings.
  `generator_version`, `catalog_version` and `curation_stamp` are provenance: they say
  what made a seed, not how to make it again.
- **`build_version` is a label for what players get.** It is bumped when a change to the
  unfinished recipe makes seeds play or look different enough to be worth telling apart,
  such as a new patch or a fix like `wind_fix`. It must be bumped, and the build 4
  fixture's rule followed (`tests/fixtures/build4/README.md`), when the change touches
  anything the finisher or the scan route reads from a stored artifact. A change that
  only moves bytes, such as better compression, need not bump it.
- **Transform output is not pinned to the build version.** The golden hashes in
  `tests/unit/test_transforms.py` stay as a regression test: when one moves on purpose,
  its digest is updated. A transform's own version (`@N`) still changes only when what
  it means changes.
- **Hole ids stay the primary key for statistics, and transforms the secondary one.**

  > I think that essentially hole id is always a primary differentiator, and the list of
  > transforms applied is a secondary one.

  A lineage is never reused for a different hole design, and a change that matters to
  play is a new version, so stats can group a lineage's versions or split them. A seeded
  transform groups by its name without the seed (`hazards@1`), and `build_version` or a
  stored hole's content hash can split a group when a transform's output changed between
  releases.

The finish ABI, the QR payload protocols, the SRAM magic, seed ids, player ids and MAC
keys, the append-only migrations, and the rule that a seed row is never rewritten are
unchanged: those are owed to the artifacts and ROMs already issued.

## Rejected alternatives

- **Keep every seed rebuildable.** It needs every build version's course writer,
  compressor, transforms and imported resources kept or frozen, which ADR 0015 already
  turned down for transforms, and it buys nothing while the site serves the stored IPS.
  A bad stored artifact is withdrawn and published again as a new seed.
- **Rewrite stored manifests to the current schema.** One migration would retire the
  legacy loaders, but `/h/<id>.json` serves the manifest as issued, and a seed row is
  never rewritten.
- **A stored hole's content hash as the stats key.** It needs no catalog promise at all,
  but a hole with and without transforms would then be two unrelated keys, and comparing
  them is one of the things stats are for.

## Consequences

- Improving the forest fill, feature detection or the mirror table means updating the
  transform digests, not bumping `BUILD_VERSION`.
- `build_version` no longer says that two seeds' unfinished ROMs were built by
  byte-identical recipes, only that nothing players would notice differs between them.
- Changes to generation, including the request resolve of ADR 0016, need no version bump
  to stay correct, only one to stay informative.
- What follows from this, in order, is not done yet:
  1. Store every slot's built hole in `hole_data`, not only the transformed ones, and
     backfill existing seeds from the catalog, so the yardage book reads only the
     database. This extends ADR 0021.
  2. Store on the seed what its pages read from the catalog: the required ROMs and each
     hole's distance, author and source. No existing seed then reads the catalog, and
     `docs/catalog.md`'s guarantees narrow to ids never being removed or reused: an
     entry's metadata or a delta may then be corrected in place.
  3. Read stored manifests tolerantly, taking only the fields a page needs, and load
     strictly only a manifest about to be built. The legacy schema loaders retire, a
     transform name no longer has to stay registered, and a settings field only
     generation reads needs no schema bump.

  Until the first two are done, the catalog's guarantees stand as they are.

## Sources

- `docs/manifest.md`, `docs/catalog.md`, `docs/hole_transforms.md`,
  `docs/patch_stack.md`, `docs/planning/compatibility_breadcrumb.md`
- `golf/randomizer/build.py`, `golf/randomizer/generate.py`,
  `golf/randomizer/transforms.py`, `server/seeds.py`, `server/views.py`,
  `server/yardage_book.py`, `server/routes/seed_pages.py`;
  `tests/unit/test_transforms.py`, `tests/meta/test_catalog_frozen.py`
- ADR 0015, ADR 0016, ADR 0021, ADR 0022, ADR 0023
- Claude Code session 2026-10-13 `018V5k6x`: the audit of every read of a stored seed and
  every compatibility guarantee, and jdharms's choices of hole id over content hash as
  the stats key and of the order of the work.
