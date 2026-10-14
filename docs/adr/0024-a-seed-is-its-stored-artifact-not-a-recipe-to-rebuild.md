+++
status = "accepted"
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

An audit of what read a seed after it was created found:

- **A download** read the stored IPS and, from the manifest, `finish_abi_version`,
  `sram_magic` and the club rules, with `qr_seed_id` and the entry's keys from their rows.
  `required_roms` also read each hole's catalog entry.
- **The seed page** read the catalog entry of each hole id for its distance, author and
  source, and the current curation for its display name.
- **The yardage book** read a transformed slot's hole from `hole_data` (ADR 0021), and
  any other slot's from the catalog and the hole store, checked against its content hash.
- **The round, account and admin pages** parsed the whole manifest for its magic words
  and pars.
- **A scan** read its payload, the seed by `qr_seed_id`, the entry's keys and
  `seed_holes.par`.

Nothing read a seed's `settings` to generate it again, and nothing rebuilt its unfinished
ROM. Several guarantees existed only for that, or only because a stored seed still read
the catalog:

- the same catalog, curation and settings regenerating the same manifest under one
  `generator_version` (`docs/manifest.md`), which could not hold in practice anyway,
  since curation is edited freely and a manifest records only its stamp;
- `build_version` bumped for any change that could alter the unfinished ROM's bytes;
- each transform's output pinned by golden hashes, any change to them bumping
  `BUILD_VERSION` (ADR 0015);
- a catalog id resolving to the same hole data forever, and no committed entry ever
  changed (`docs/catalog.md`), which, since vanilla hole data is not committed, also froze
  the dumpers and the canonical hole form;
- deltas never edited, and the delta format permanent (ADR 0022);
- a strict reader for every manifest schema, which keeps every transform name registered
  and makes each new settings field a schema bump with its own loader branch, as
  `include` (ADR 0023) was, though nothing reads a stored seed's `include`.

The catalog machinery had not yet been used: the index was at version 1, with 144
vanilla holes and none withdrawn, derived or community.

## Decision

A seed's identity is what the site stored when it was created: the manifest text as
issued, the unfinished IPS, its rows in `seed_holes` and its built holes in `hole_data`.
Nothing upstream of those has to reproduce them, and a seed's pages read nothing a later
release could change.

- **Every hole as built is stored.** `insert_seed` takes the build's 18 holes and stores
  each in `hole_data`, not only the transformed ones. This reverses the alternative ADR
  0021 rejected, "storing every slot's hole", whose reason was that an untransformed slot
  resolves through the catalog in any release; that is no longer promised. A hole two
  seeds share is stored once, so the vanilla holes add at most one row each.
- **What a seed's pages read from the catalog is kept on the seed** (migration 7): the
  required ROMs on `seeds`, and each hole's distance, author and source on `seed_holes`.
  The seed page, the download's ROM check and the yardage book read these rows. A hole's
  display name still comes from the current curation, and the admin pages still show
  whether the catalog has withdrawn a hole since.
- **Seeds stored before migration 7 are filled in once.** `golf-site-backfill`, run by
  hand at the deploy that brings the migration, works every seed out from the manifest,
  the catalog and the hole store before writing anything, and writes nothing unless all
  of them can be filled. The site refuses to start while any seed is unfilled.
- **The catalog promises what stats need, and no more.** No committed id is ever removed,
  given another source or un-withdrawn (`tests/meta/test_catalog_ids.py`), and a change to
  a hole that matters to play is a new version. An entry's hash, par, distance or author,
  and a derived hole's delta, may be corrected in place. `HoleStore.load` still checks
  hole data against its entry, as a check that a rehydration matches the index it was
  made for.
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

  Stats can group a lineage's versions or split them. A seeded transform groups by its
  name without the seed (`hazards@1`), and `build_version` or a stored hole's content
  hash can split a group when a transform's output changed between releases.
- **A stored manifest is read for what its pages need.** Strict loading, with every field
  checked, is for a manifest about to be built. Reading a stored seed takes only the
  fields its pages and its finisher use, so a schema needs no loader branch to stay
  readable, and a transform name no longer has to stay registered for its manifests to
  load. The finish inputs (`finish_abi_version`, `sram_magic`, the club rules) stay
  checked. This follows migration 7 as its own release, because the backfill reads old
  manifests through the strict loader.

The finish ABI, the QR payload protocols, the SRAM magic, seed ids, player ids and MAC
keys, the append-only migrations, and the rule that a seed row's manifest and IPS are
never rewritten are unchanged: those are owed to the artifacts and ROMs already issued.

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
- **Backfill at startup.** The site would fill old seeds itself and need no deploy step,
  but every start would be a data migration, run where a failure takes the site down.
- **Read a hole's distance from `hole_data` instead of keeping a column.** It would store
  one value fewer, but the seed page would decompress all 18 holes on every view.

## Consequences

- No stored seed reads the catalog or the hole store. The hole store serves only new
  builds and the backfill.
- A stored hole takes about 1.1 KB under zlib (ADR 0021). Vanilla holes are shared, as
  is a hole with only unseeded transforms such as `mirror@1`, so only a seeded transform
  adds rows of its seed's own.
- Improving the forest fill, feature detection or the mirror table means updating the
  transform digests, not bumping `BUILD_VERSION`.
- `build_version` no longer says that two seeds' unfinished ROMs were built by
  byte-identical recipes, only that nothing players would notice differs between them.
- Changes to generation, including the request resolve of ADR 0016, need no version bump
  to stay correct, only one to stay informative.
- This softens one consequence of ADR 0022, that "the delta format is as permanent as
  the entries that name it." A delta can be rewritten in a new format in place, so
  `apply` needs to read only the formats the checked-in deltas use.
- A database from before migration 7, such as an old backup, needs `golf-site-backfill`
  from a release that still has the strict legacy loaders. Once the tolerant reader
  ships and production has run the backfill, the command can retire.
- A seed stored by an unreleased development build may not load at all: one in a
  development database had a schema 3 manifest from before schema 3 gained its wind
  profiles. The backfill names such a seed and writes nothing, and it has to be deleted.

## Sources

- `docs/manifest.md`, `docs/catalog.md`, `docs/hole_transforms.md`,
  `docs/patch_stack.md`, `docs/yardage_book.md`, `docs/deployment.md`,
  `docs/planning/compatibility_breadcrumb.md`
- `golf/randomizer/build.py`, `golf/randomizer/generate.py`,
  `golf/randomizer/transforms.py`, `server/seeds.py`, `server/migrations.py`,
  `server/views.py`, `server/yardage_book.py`, `server/routes/seed_pages.py`,
  `tools/site_backfill.py`; `tests/unit/test_transforms.py`,
  `tests/meta/test_catalog_ids.py`
- ADR 0015, ADR 0016, ADR 0021, ADR 0022, ADR 0023
- Claude Code session 2026-10-13 `018V5k6x`: the audit of every read of a stored seed and
  every compatibility guarantee, and jdharms's choices of hole id over content hash as
  the stats key, of the order of the work, of a one-time backfill run at deploy, with the
  stored holes and catalog facts in one migration, and of the tolerant reader following
  in its own release. A dry run of the backfill on a restored production backup found
  every seed fillable.
