# Architecture Decision Records

> Intro, "When to write one" written by jdharms.
> Remainder written by Claude.
> The index is primarily maintained by AI agents.

Each record here captures one decision: what forced it, what was chosen, what was
turned down and why, and what should make us reopen it.

## When to write one

Write a record when a choice has a real alternative that someone could reasonably pick
later without knowing why it was turned down: a trade-off, an interim answer ("for
now"), or anything with a condition that should reopen it.

The `rejected` status is only for a proposed record that "lost"; it keeps
the history of that proposal rather than deleting it.  Generally this
should only be used if a `proposed` record gets *committed*.  If
we change our minds on a proposed record mid-session, we should rewrite
the record in the "positive" form of the chosen alternative.

## Format

A trimmed-down [MADR](https://adr.github.io/madr/) record, one file per decision:
`NNNN-slug.md`. The number and slug come from the filename and the title from the `#`
heading. The frontmatter is TOML between `+++` lines:

| Field | Meaning |
|-------|---------|
| `status` | `proposed`, `accepted`, `rejected`, `deprecated` or `superseded` |
| `date` | when the status last changed |
| `area` | `rom`, `patches`, `qr`, `randomizer`, `site`, `deploy` or `tooling` |
| `permanent` | `true` when the effects can't be undone, e.g. something baked into released ROMs |
| `revisit_when` | the observable condition that should reopen the decision |
| `drafted_by` | who wrote the draft |
| `supersedes` | numbers of the records this one replaces |
| `superseded_by` | set on a replaced record once its successor is accepted |

The body has exactly these sections, in order: Context, Decision, Rejected
alternatives, Consequences, Sources. Sources cite the docs and code involved and, for
decisions made in a Claude Code session, the date and session ID prefix.

A proposed record may leave sections empty. Any other status needs every section
filled in and `revisit_when`; a rejected record is only useful for saying why. Once a record is accepted, only its status changes. Changing the
decision means writing a new record that supersedes it.

Code and docs cite a record as "ADR" followed by its four-digit number, for example
in a comment beside the constant the decision governs.

## Commands

```bash
uv run golf-adr new "Title" --area patches [--supersedes N] [--revisit-when "..."]
uv run golf-adr status N accepted       # also marks what N supersedes as superseded
uv run golf-adr index [--check]         # regenerate the table below
uv run golf-adr check                   # numbering and supersession links
```

`tests/meta/test_adrs.py` checks every record, the links between them, that this
index is current, and that every cited record exists.

## Index

<!-- adr-index:start -->
| ADR | Title | Area | Status | Permanent | Revisit when |
|-----|-------|------|--------|-----------|--------------|
| [0001](0001-one-course-per-rom.md) | One course per ROM | patches | accepted |  | Multi-course generation (devplan item 16) is scheduled, or a patch is wanted on a ROM that keeps its vanilla courses |
| [0002](0002-any-byte-written-by-two-patchstack-steps-is-an-error.md) | Any byte written by two PatchStack steps is an error | patches | accepted |  | Overlap errors start firing on deliberate, identical writes often enough to be a nuisance |
| [0003](0003-sign-in-not-required-to-generate-or-download-seeds.md) | Sign-in not required to generate or download seeds | site | accepted |  | League rounds regularly go unrecorded because players downloaded guest ROMs, and the league wants sign-in enforced |
| [0004](0004-four-bits-each-for-strokes-and-putts-in-the-qr-hole-record.md) | Four bits each for strokes and putts in the QR hole record | qr | accepted | yes | A site seed can be built without the mercy tap-in, or the QR payload protocol changes version for another reason |
| [0005](0005-the-site-reads-its-release-from-the-git-checkout-it-runs-from.md) | The site reads its release from the git checkout it runs from | site | accepted |  | The site is deployed some other way than a git checkout of its release tag, such as a container image or a built package |
| [0006](0006-a-collection-page-is-a-directory-with-one-card-per-file.md) | A collection page is a directory with one card per file | site | accepted |  | A collection needs an order other than newest date first, or entries need structure beyond a title, a date and a Markdown body |
| [0007](0007-finish-abi-2-writes-new-save-options-into-a-table-in-player-stats-space.md) | Finish ABI 2 writes new-save options into a table in PLAYER STATS' space | randomizer | accepted | yes | A new-save option needs a value the four-byte table does not hold |
| [0008](0008-saved-download-settings-live-in-a-cookie-and-on-the-account-not-in-entries.md) | Saved download settings live in a cookie and on the account, not in entries | site | accepted |  | Players need saved settings that follow them across browsers while signed out, or entries start recording the bag a round was played with |
| [0009](0009-finishing-relies-only-on-pinned-contract-bytes-not-on-the-current-port.md) | Finishing relies only on pinned contract bytes, not on the current port | randomizer | accepted |  | The next finish ABI bump, which is also when to replace the zero placeholder fill with a recognizable magic value |
| [0010](0010-scorecard-qr-protocol-version-2-adds-fairways-hit-and-penalty-strokes.md) | Scorecard QR protocol version 2 adds fairways hit and penalty strokes | qr | accepted | yes | A stat the league wants does not fit the payload's reserved flag bits, or the server needs per-hole penalties |
| [0011](0011-the-shot-physics-model-reproduces-the-rom-exactly-checked-against-its-own-code.md) | The shot physics model reproduces the ROM exactly, checked against its own code | rom | accepted |  | Course-wide difficulty maps need more shots per second than the exact model gives, or a patch changes bank 13's physics code rather than its tables |
| [0012](0012-hole-difficulty-solving-is-paused-its-results-kept-at-skill-3-pin-0-no-wind.md) | Hole difficulty solving is paused, its results kept at skill 3, pin 0, no wind | tooling | accepted |  | jdharms takes the solver up again, or something the results rest on changes: the physics model or the solver, the randomizer wanting the other pins or wind, or the randomizer depending on exact expected scores rather than rough rankings |
| [0013](0013-feature-borders-are-fitted-to-a-painted-shape-with-the-vanilla-holes-as-a-preference-rather-than-a-rule.md) | Feature borders are fitted to a painted shape, with the vanilla holes as a preference rather than a rule | tooling | accepted |  | the brush's fits need hand correction often enough that the cycle tool is still the main way borders get made, or holes drawn in a deliberately different style are wanted |
| [0014](0014-the-out-of-bounds-brush-draws-only-the-line-and-leaves-the-forest-inside-it-to-forest-fill.md) | The out-of-bounds brush draws only the line, and leaves the forest inside it to Forest Fill | tooling | accepted |  | filling the out-of-bounds interior by hand after every stroke proves to be busywork, with the seeding rarely used |
| [0015](0015-hole-transform-output-is-pinned-by-golden-hashes-and-the-build-version-not-by-frozen-code.md) | Hole transform output is pinned by golden hashes and the build version, not by frozen code | randomizer | superseded by [0024](0024-a-seed-is-its-stored-artifact-not-a-recipe-to-rebuild.md) |  | the site rebuilds a stored seed from its manifest instead of serving its stored unfinished IPS, or one release must build more than one unfinished build version |
| [0016](0016-seeds-are-generated-from-a-request-that-resolve-turns-into-concrete-settings.md) | Seeds are generated from a Request that resolve turns into concrete Settings | randomizer | proposed |  | players want a seed's rolled outcome kept out of its public manifest, or a request needs a dependency between fields that weighted sets of weighted fields cannot express |
| [0017](0017-each-hole-s-wind-anchors-come-from-the-manifest-through-a-table-the-rom-reads.md) | Each hole's wind anchors come from the manifest, through a table the ROM reads | randomizer | accepted | yes | a wind speed above the vanilla anchors is wanted, the pin should be chosen per hole as the anchors are, or a patch needs the course-3 flag block the table sits in |
| [0018](0018-a-seed-has-two-wind-profiles-speed-and-direction-with-speed-drawn-in-bands-by-intensity.md) | A seed has two wind profiles, speed and direction, with speed drawn in bands by intensity | randomizer | accepted |  | players report a band or profile plays unlike its name, a profile is wanted that an intensity per hole or a cone cannot express, or the speed wrap at 10 is patched and changes what anchors 9 and 10 play like |
| [0019](0019-mario-open-s-intro-sky-is-checked-in-as-an-image-and-ships-in-every-seed.md) | Mario Open's intro sky is checked in as an image and ships in every seed | randomizer | accepted | yes | an original sky is drawn to replace it, or the Mario Open ROM stops being something the site may assume players can lawfully obtain |
| [0020](0020-a-green-is-painted-as-its-putting-surface-and-its-fringe-and-rough-follow-from-that.md) | A green is painted as its putting surface, and its fringe and rough follow from that | tooling | accepted |  | greens drawn with the brush need their fringe corrected by hand often enough that tile-by-tile editing is still the main way a green gets made, or a fringe of another width is wanted |
| [0021](0021-a-seed-s-transformed-holes-are-stored-with-it-as-built.md) | A seed's transformed holes are stored with it, as built | site | accepted |  | the stored holes grow large enough to matter to the database or its replication, or the site starts rebuilding seeds from their manifests, which would give every release each build version's transforms |
| [0022](0022-a-derived-hole-is-a-catalog-entry-holding-a-base-hole-and-a-delta-of-data.md) | A derived hole is a catalog entry holding a base hole and a delta of data | randomizer | accepted |  | a variant needs to move a hole's terrain sideways or one part of it against another, which a cell delta can only record by copying the base, or a derived hole needs another derived hole as its base |
| [0023](0023-holes-that-are-not-vanilla-enter-the-pool-only-when-a-seed-s-settings-include-their-kind.md) | Holes that are not vanilla enter the pool only when a seed's settings include their kind | randomizer | accepted |  | players need to choose among derived or community holes more finely than by kind, such as short holes without other variants |
| [0024](0024-a-seed-is-its-stored-artifact-not-a-recipe-to-rebuild.md) | A seed is its stored artifact, not a recipe to rebuild | randomizer | accepted |  | the site needs to rebuild a stored seed, such as to repair a bad unfinished IPS in place rather than publish a new seed, or stats need to tell apart holes that share a lineage and transforms |
<!-- adr-index:end -->
