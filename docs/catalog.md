# The Hole Catalog

> **Note**: This document was written by Claude and edited by jdharms.

The catalog is every hole a randomizer seed can reference. It lives in two files with
different contracts:

| File | Holds | Changes |
|---|---|---|
| `data/catalog/holes.json` | Ids, provenance and a content hash per hole | Ids never removed or given another hole |
| `data/catalog/curation.json` | Tags, drawability, families and display names | Freely edited, with `golf-curate` |

Everything that can reach a ROM is in the index. Everything that only decides which holes
get drawn is curation. A manifest stores resolved hole ids, never filters, and the site
stores every hole a seed was built with, and what its pages show of it, so editing either
file changes no existing seed (ADR 0024).

Code: `golf/randomizer/catalog.py` and `golf/randomizer/curation.py`.

## Ids and versions

An id is a lineage and a version: `nes_uk/01`, `jp_hawaii/07`, `dharms/cliffside@2`.
Version 1 is written without a suffix, and `nes_uk/01@1` parses to the same id.

- **Vanilla lineages** are `nes_<course>/<hole>` for NES Open (`nes_us`, `nes_uk`,
  `nes_japan`) and the Mario Open dump names for Mario Open (`jp_japan`, `jp_australia`,
  `jp_france`, `jp_hawaii`, `jp_uk`).
- **Every other lineage** is `<owner>/<slug>`, where the owner is `community` or one
  author's name. The entry's `author` field holds the credit either way.
- **A change that matters to play is a new version.** This applies to vanilla holes
  too: a corrected extraction of `nes_uk/01` that plays differently is `nes_uk/01@2`.
  Stats group a lineage's versions or split them, so a version is how a change shows in
  them. Anything else about an entry may be corrected in place.
- **Only the highest version of a lineage is drawable.** Supersession never rolls back:
  to undo version 2, publish version 3 with version 1's content.

## The index

Each entry holds its source, content hash, par, distance and author:

```json
"nes_uk/01": {
  "source": {"rom": "nes_open_us", "course": "uk", "hole": 1},
  "content_hash": "1dcf8587…",
  "par": 4,
  "distance": 418,
  "author": "Nintendo"
}
```

A source is one of three things, and it gives the hole its **kind**:

| Kind | Source | The hole's data |
|---|---|---|
| `vanilla` | `{"rom": …, "course": …, "hole": …}` | Dumped from the ROM by `golf-rehydrate` |
| `derived` | `{"base": "<id>", "delta": "<path>"}` | The base, a vanilla hole, with a checked-in delta applied ([derived_holes.md](derived_holes.md)) |
| `community` | `{"file": "<path>"}` | A hole file, relative to the hole store root |

Only vanilla holes are in every pool. A seed's settings ask for the other kinds by name
([manifest.md](manifest.md), `include`), so adding a hole to the index puts it in no pool
that did not ask for its kind (ADR 0023).

Par and distance are copies for the pool builder and the seed page; the hash covers them.

Guarantees, enforced by `tests/meta/test_catalog_ids.py` and the loader:

1. **An id's hole data matches its entry.** `HoleStore.load` recomputes the hash and
   refuses data that does not match, which is what makes a rehydration trustworthy. It
   checks the current data against the current index, not a promise across releases: a
   seed reads the holes stored with it, never the catalog (ADR 0024).
2. **No committed id is ever removed or given another hole**, across the index's whole
   git history: its source stays the same, and the index version never decreases. Stats
   join seeds on hole ids. An entry's hash, par, distance or author may be corrected in
   place; a change that matters to play is a new version instead.
3. **Withdrawal retires an entry for a takedown.** `"withdrawn": true` is never taken
   back. Its id stays reserved, and seeds that used it remain downloadable from their
   stored unfinished IPS. `HoleStore.load` refuses a withdrawn entry unless asked with
   `even_withdrawn`, which is for reading a hole a seed already holds. A lineage whose highest version is withdrawn has nothing
   drawable, and nor has a hole derived from a withdrawn base.

## The content hash

`content_hash` is SHA-256 over the canonical JSON (sorted keys, no whitespace) of the
fields of `HoleData.to_dict()` that reach the ROM: par, distance, handicap, scroll limit,
green, tee, flag positions, attributes, greens, and terrain cut to its visible height.
The hole number, the `_debug` block and hidden terrain rows are excluded. Reformatting a
hole file or re-dumping identical data leaves the hash unchanged.

## Curation

Curation is keyed by lineage, never by versioned id, so a record carries forward when a
new version is published:

```json
{
  "jp_japan/01": {"family": "nes_uk_01"},
  "nes_uk/01": {"family": "nes_uk_01", "tags": ["dogleg"]}
}
```

| Field | Default | Meaning |
|---|---|---|
| `tags` | none | Labels pool filters match on, and `expert`, below |
| `drawable` | true | False retires the lineage from new seeds |
| `family` | none | Holes judged to be the same hole |
| `display_name` | none | Name for the seed page |

An uncurated lineage gets the defaults, so a new catalog entry is drawable with no tags.
Unknown fields and versioned keys are load errors, and a test checks that every curated
lineage exists in the index.

**The `expert` tag** marks an expert hole, which the `expert_cap` rule limits on each
nine ([manifest.md](manifest.md), **The expert cap**). It is on the 21 Mario Open holes that
play worse against par than every NES Open hole ([hole_difficulty.md](hole_difficulty.md),
**Expert holes**), a list jdharms's league-mates reviewed. The tag is the record: it was
set from that list once, and a later re-solve that moves a hole across the line changes
nothing until a person edits the tag. Like any tag it can also go in `exclude_tags`.

**Families** are a person's judgment, never a computed one. Every lineage with the same
label is in the same family, and a lineage is in at most one. Examples are a vanilla hole
and its Mario Open twin, or a par 5 and its forward-tee par 3: `golf-derive` puts a
derived hole in its base's family. The label is a plain
string; by convention a family containing an NES Open hole is named after it, as in
`nes_uk_01`. Whether two holes from one family may share a course is the
`allow_family_repeats` generation setting ([manifest.md](manifest.md)).

`golf-curate suggest` *proposes* families out of the hole data, and records only the ones
a person accepts — see **Editing curation** below.

**Transforms** such as mirroring are applied to a manifest slot after the draw. They
create no catalog entry and have no family of their own.

Generation reads a `CurationSnapshot`: the curation file as loaded, with a `stamp` that
identifies its content and is recorded in the manifest for auditing. The site builds its
snapshot from the same file and will layer player statistics from its database onto the
same record type.

## Editing curation

`golf-curate` reads and edits the curation file. A snapshot is frozen, so every edit
returns a new one and `save` writes it back, one record per line, sorted by lineage. The
commands cover families; tags, drawability and display names are still edited by hand, and
an existing record keeps them untouched.

| Command | Does |
|---|---|
| `list [--course P] [--family L] [--par N] [--unfamilied]` | Every catalog lineage with its family |
| `show <lineage>` | One hole: catalog entry, record, siblings, rangefinder link |
| `family list` | Every family, its members, and how many lineages have none |
| `family set <label> <lineage>... [--move]` | Create or extend a family |
| `family clear <lineage>...` | Drop the family from each hole |
| `family rename <old> <new>` | Rename, or merge into `<new>` when it already exists |
| `suggest [--review] [--limit N] [--min-score F] [--all]` | Candidate families from the hole data |
| `check` | Curated lineages the catalog lacks, and families of one; exit 1 on either |

`--dry-run` on any of the writers reports without writing, and `--curation` points at a
file other than the checked-in one.

**What `suggest` reads.** Two signals in the hole data, neither of which decides anything
(`golf/randomizer/twins.py`):

- **A byte-identical `greens` layout.** Across the vanilla set, 39 greens layouts appear
  exactly twice and none more often, and every one of those pairs is one NES Open hole and
  one Mario Open hole.
- **Terrain agreement**, the share of terrain cells two holes share over their visible
  height. It corroborates 37 of those 39 pairs at better than 80%, and finds three more
  pairs whose greens were redrawn between releases.

Candidates are ranked greens-first, then by terrain, and holes already in a family are
left out unless `--all`. `--review` walks them one at a time with the rangefinder deep link
for each hole, taking `y`/`n`/`s`/`q`, and writes the accepted ones with a label derived
the conventional way — `nes_uk/01` and `jp_japan/01` become `nes_uk_01`.

## Syncing vanilla holes

The hole data is not in the repository. `golf-rehydrate` dumps it from the vanilla ROMs
into `courses/` (Mario Open under `courses/jp/`) and installs it only if every hole matches
its entry here, so the index's content hashes are what make a rehydration trustworthy. The
holes derived from a ROM's holes are built and checked with them. The
site runs the same check before it starts. See `golf/randomizer/rehydrate.py`.

`golf-catalog-sync` walks the dumped course directories and, for each hole:

- **Adds** a version 1 entry when the index lacks the id, and bumps the index version.
- **Verifies** an existing entry against the data, and reports a mismatch without writing
  anything.
- **Skips** entries whose data is not present, such as Mario Open holes on a checkout
  without the JP dump.

It never removes or rewrites an entry, and never mints a version above 1. `--check`
reports without writing and exits 1 if anything would be added or does not match.
