# Community Holes

> **Note**: This document was written by Claude based on design and ideas by jdharms.

A community hole is a hole a contributor made, not one dumped from a ROM or derived from
one. It has two copies with different jobs (ADR 0027):

| Copy | Where | Changes |
|---|---|---|
| Working copy | `courses/community/` | Edited freely in the editor, and committed |
| Published copy | `data/catalog/community/`, beside the index | Never edited; a change is a new version |

The randomizer reads only the published copy, through its catalog entry
([catalog.md](catalog.md)). Editing a working copy changes no seed and breaks no entry
until it is published again.

Code: `golf/randomizer/community.py`, and the CLI `golf-community`
(`tools/data/community.py`).

## Courses and loose holes

**A course** is a folder of `hole_01.json`-`hole_18.json`, with the `golf-patch` recipe
that builds it, in `courses/community/<course>/`. Its holes publish as
`<course>/01`-`<course>/18`, as a vanilla course's are, and the index lists the course
with its display name and author. Georgia Pines is `courses/community/georgia_pines/`.

**A loose hole**, one sent on its own or with a few others, goes in
`courses/community/<author>/` and publishes under its author: `<author>/<slug>`.

An owner is either a listed course or an author. Publishing refuses a loose hole under a
course, a new course named like an existing author, and the vanilla owners.

## Credit

A hole file may name its `author`:

```json
{
  "hole": 7,
  "author": "<handle>",
  "par": 4,
  ...
}
```

It never reaches the ROM, so it changes no content hash. A course's hole is credited to
its own `author`, or to the course's when it names none; a loose hole must name one.
Publishing copies the credit into the catalog entry, which is what a seed copies and the
site shows. A credit changed in a working copy whose content is otherwise the same
corrects the entry in place, with no new version.

## Publishing

```bash
golf-community course courses/community/georgia_pines georgia_pines --name "Georgia Pines" --author <handle> --dry-run
golf-community course courses/community/georgia_pines georgia_pines
golf-community hole courses/community/<handle>/island_green.json <handle>/island_green
golf-community check
```

Publishing a hole:

1. Drops the attribute rows the editor hid below the visible terrain, as `golf-derive`
   does; they would otherwise reach the ROM.
2. Refuses a hole no seed could be built with (`golf/formats/hole_check.py`, as
   `golf-derive` does) or that a transform cannot run on.
3. Skips a hole whose content matches its lineage's newest version, and refuses one
   identical to another lineage's hole.
4. Otherwise makes the lineage's next version: `community/<owner>/<slug>.json`, or
   `<slug>@<version>.json` after the first, next to the index. A file there is never
   overwritten.
5. Writes the entries and bumps the index version.

`course` lists the course the first time, which needs `--name` and `--author`; later
they correct its listing in place. It republishes only the holes that changed. Curation
records (tags, families) are not written; `golf-curate` sets them.

`check` loads every live community hole from where publishing put it, against its hash,
and runs the same checks again. `tests/unit/test_catalog.py` runs it on the checked-in
index.

## Start to finish

Publishing is permanent. Once committed, a hole's id, its file's path and a course's
listing never change ([catalog.md](catalog.md), guarantee 2). A mistake is mended with a
new version or a withdrawal, never by editing or removing what was published.

### A course

1. **Commit the working copy** in `courses/community/<course>/`: `hole_01.json`-
   `hole_18.json`, with its `golf-patch` recipe if it has a build of its own.
2. **Credit it.** The editor cannot set `author`, so either let `--author` credit every
   hole, or add `"author"` by hand to the hole files that need another credit.
3. **Dry-run** and read the report: the 18 ids, their par and distance and credit, and
   the notes, such as attribute rows dropped below the visible terrain. Those rows are
   never seen, but a build made from the working copy still writes them, so the published
   holes and the course's own build differ there.

   ```bash
   golf-community course courses/community/<course> <course> --name "<Name>" --author <handle> --dry-run
   ```

4. **Publish**: the same command without `--dry-run`. It writes
   `data/catalog/community/<course>/01.json`-`18.json` and the index.
5. **Check and commit together.** `golf-community check`, then `uv run pytest`; commit the
   18 frozen files and `data/catalog/holes.json` in one commit, since an index naming
   files that are not there fails the tests.
6. **Release the site.** It reads the catalog from its checkout, so the holes reach it
   with the next deploy.

To change a hole later, edit its working copy and run `course` again without `--name` or
`--author`: only the holes that changed get a new version, `<course>/07@2`. Passing
`--name` or `--author` corrects the listing in place.

### A single hole

1. **Commit the working copy** in `courses/community/<author>/<slug>.json`. The slug is
   lowercase letters, digits and underscores, and names the hole: `island_green`.
2. **Credit it.** `hole` takes no `--author`; the file must name its own, added by hand
   until the editor can set it:

   ```json
   "author": "<handle>",
   ```

3. **Dry-run** and read the report:

   ```bash
   golf-community hole courses/community/<author>/<slug>.json <author>/<slug> --dry-run
   ```

4. **Publish**: the same command without `--dry-run`. It writes
   `data/catalog/community/<author>/<slug>.json` and the index.
5. **Check and commit together**, as for a course: the frozen file and
   `data/catalog/holes.json` in one commit.
6. **Release the site.**

To change it later, edit the working copy and run `hole` again: a changed hole is
`<author>/<slug>@2`, and a changed credit alone corrects the entry in place.

### Reaching players

A published hole is drawn only by a seed whose settings `include` `community` (below).
The site's generate form never asks for it; it includes only `derived`, and only for
the par 3 mode (`request_from_state` in `server/forms.py`). Until it does, on the site only a
request file uploaded to the mystery page, where that page is enabled
(`Config.mystery_seeds`), can ask for community holes.

## Pools

Like derived holes, community holes are in a seed's pool only when its settings
`include` them ([manifest.md](manifest.md), ADR 0023). They need no ROM, so every
checkout and the site can load them without rehydrating anything.
