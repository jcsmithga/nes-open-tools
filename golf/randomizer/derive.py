"""
Publishing derived holes: an edited copy of a vanilla hole, turned into a delta, a
catalog entry and a curation record.

`derive` takes the hole a person edited and the vanilla hole it started from. It never
edits a hole itself: the tee box, the carved forest and the new bottom edge are all the
editor's work, and what comes out is the cells and metadata that differ (ADR 0022).
`golf-derive` (tools/data/derive.py) is the CLI on top. See docs/derived_holes.md.
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path

from golf.core.palettes import GREENS_WIDTH, TERRAIN_WIDTH
from golf.core.patches.course import compress_holes
from golf.formats.hole_data import MAX_TERRAIN_ROWS, MIN_TERRAIN_ROWS, HoleData

from . import delta as hole_delta
from .catalog import (
    COMMUNITY,
    DERIVED,
    VANILLA,
    VANILLA_OWNERS,
    Catalog,
    CatalogEntry,
    CatalogError,
    DerivedSource,
    HoleId,
    HoleStore,
    canonical_dict,
    content_hash,
)
from .curation import CurationSnapshot, HoleCuration
from .manifest import HOLE_PARS
from .transforms import TRANSFORMS, TransformError, parse_transform
from .twins import suggested_label

#: where an index keeps its deltas, relative to its own directory
DELTA_DIR = "derived"
#: the tag on a derived hole whose par is lower than its base's
SHORT_TAG = "short"
#: seeds each seeded transform is tried with when a derived hole is checked
TRANSFORM_SEEDS = (0, 1, 2)
#: the share of the shared terrain a delta may rewrite before it is reported, and before
#: it is refused: past these the hole has probably moved within its grid, and the delta
#: is then mostly the base hole displaced
WARN_SHARE = 0.35
REFUSE_SHARE = 0.60

MIN_ROWS = MIN_TERRAIN_ROWS
MAX_ROWS = MAX_TERRAIN_ROWS
ATTRIBUTES_WIDTH = 11
GREENS_HEIGHT = 24
ATTRIBUTE_PALETTES = range(4)
TILES = range(0x100)
PINS = 4


class DeriveError(CatalogError):
    """An edited hole that cannot be published as a derived hole."""


@dataclass(frozen=True)
class Derivation:
    """A derived hole ready to be written: nothing is on disk until `write`."""

    entry: CatalogEntry
    delta: dict
    catalog: Catalog
    curation: CurationSnapshot
    #: the share of the terrain cells both holes have that the delta rewrites
    terrain_share: float
    #: what was done or noticed along the way, for the person publishing
    notes: tuple[str, ...]

    @property
    def delta_path(self) -> Path:
        assert isinstance(self.entry.source, DerivedSource)
        return self.entry.source.path

    def write(self, index: Path, curation: Path) -> None:
        """Write the delta, then the index and the curation file that name it."""
        path = self.delta_path
        if path.exists():
            raise DeriveError(f"{path} already exists; deltas are never overwritten")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(format_delta(self.delta))
        self.catalog.save(index)
        self.curation.save(curation)


def format_delta(delta: dict) -> str:
    """A delta as its file holds it: one run to a line, so a hole reads as its edits."""
    lines = [
        f'  "format": {_json(delta["format"])}',
        f'  "base": {_json(delta["base"])}',
    ]
    if "row_offset" in delta:
        lines.append(f'  "row_offset": {_json(delta["row_offset"])}')
    if "metadata" in delta:
        fields = ",\n".join(
            f"    {_json(key)}: {_json(value)}"
            for key, value in delta["metadata"].items()
        )
        lines.append('  "metadata": {\n' + fields + "\n  }")
    for name in hole_delta.GRIDS:
        if name not in delta:
            continue
        grid = delta[name]
        runs = ",\n".join(f"      {_json(run)}" for run in grid["cells"])
        cells = "[\n" + runs + "\n    ]" if runs else "[]"
        lines.append(
            f'  "{name}": {{\n    "rows": {grid["rows"]},\n    "cells": {cells}\n  }}'
        )
    text = "{\n" + ",\n".join(lines) + "\n}\n"
    if json.loads(text) != delta:  # pragma: no cover - a delta field this cannot write
        raise DeriveError("the delta cannot be written as a delta file")
    return text


def _json(value: object) -> str:
    return json.dumps(value)


def delta_file(hole_id: HoleId) -> str:
    """`derived/community/nes_us_12_forward3.json`, or `...@2.json` for a later version."""
    owner, slug = hole_id.lineage.split("/")
    suffix = "" if hole_id.version == 1 else f"@{hole_id.version}"
    return f"{DELTA_DIR}/{owner}/{slug}{suffix}.json"


def visible(hole: HoleData) -> tuple[HoleData, tuple[str, ...]]:
    """`hole` as it is published: with the rows the editor hid, and their palettes, gone.

    The editor removes rows softly, keeping the terrain and attribute rows below the
    visible height so they can be restored. Terrain past the height never reaches a ROM,
    but every attribute row does, so the hidden ones are dropped here.
    """
    canonical = canonical_dict(hole)
    notes = []
    wanted = (hole.terrain_height + 1) // 2
    rows = canonical["attributes"]["rows"]
    if len(rows) > wanted:
        notes.append(
            f"dropped {len(rows) - wanted} attribute rows below the visible "
            f"{hole.terrain_height} terrain rows"
        )
        canonical["attributes"] = {
            **canonical["attributes"],
            "height": wanted,
            "rows": rows[:wanted],
        }
    return HoleData.from_dict(canonical), tuple(notes)


def _is_int(value: object, low: int, high: int) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and low <= value <= high
    )


def _grid_problems(
    name: str, grid: list, width: int, height: int, values: range
) -> list[str]:
    if len(grid) != height or any(len(row) != width for row in grid):
        return [f"{name} must be {width} cells wide and {height} rows tall"]
    bad = sorted({cell for row in grid for cell in row if cell not in values})
    if bad:
        return [
            f"{name} cells must be {values.start}-{values.stop - 1}, got "
            + ", ".join(
                f"${cell:X}" if isinstance(cell, int) else repr(cell) for cell in bad
            )
        ]
    return []


def check_hole(hole: HoleData) -> None:
    """Refuse a hole no seed could be built with. Raises DeriveError.

    Every value `CoursePatch` writes is checked against what its table entry holds, and
    the hole is then compressed, as a build would compress it.
    """
    problems = []
    metadata = hole.metadata
    height = hole.terrain_height
    if not isinstance(height, int) or height % 2 or not MIN_ROWS <= height <= MAX_ROWS:
        raise DeriveError(
            f"terrain height must be even and {MIN_ROWS}-{MAX_ROWS} rows, got {height}"
        )
    limit = metadata.get("scroll_limit")
    if not _is_int(limit, (height - 28) // 2, (height - 28) // 2):
        problems.append(
            f"scroll_limit must be {(height - 28) // 2} for {height} rows, got {limit}"
        )
    if len(hole.attributes) != (height + 1) // 2:
        problems.append(
            f"{height} terrain rows need {(height + 1) // 2} attribute rows, got "
            f"{len(hole.attributes)}"
        )
    else:
        problems += _grid_problems(
            "attributes",
            hole.attributes,
            ATTRIBUTES_WIDTH,
            (height + 1) // 2,
            ATTRIBUTE_PALETTES,
        )
    problems += _grid_problems(
        "terrain", hole.terrain[:height], TERRAIN_WIDTH, height, TILES
    )
    problems += _grid_problems(
        "greens", hole.greens, GREENS_WIDTH, GREENS_HEIGHT, TILES
    )
    if not _is_int(metadata.get("par"), min(HOLE_PARS), max(HOLE_PARS)):
        problems.append(
            f"par must be one of {list(HOLE_PARS)}, got {metadata.get('par')!r}"
        )
    for name, low, high in (("distance", 1, 999), ("handicap", 1, 18)):
        if not _is_int(metadata.get(name), low, high):
            problems.append(f"{name} must be {low}-{high}, got {metadata.get(name)!r}")
    if not (_is_int(hole.green_x, 0, 0xFF) and _is_int(hole.green_y, 0, 0xFF)):
        problems.append(
            f"the green's position must be bytes, got {hole.green_x!r}, {hole.green_y!r}"
        )
    tee = metadata.get("tee")
    if not (
        isinstance(tee, dict)
        and set(tee) == {"x", "y"}
        and _is_int(tee["x"], 0, TERRAIN_WIDTH * 8 - 1)
        and _is_int(tee["y"], 0, height * 8 - 1)
    ):
        problems.append(
            f"the tee must be an x and y inside the hole's {TERRAIN_WIDTH * 8} x "
            f"{height * 8} pixels, got {tee!r}"
        )
    flags = metadata.get("flag_positions")
    if not (
        isinstance(flags, list)
        and len(flags) == PINS
        and all(
            isinstance(flag, dict)
            and set(flag) == {"x_offset", "y_offset"}
            and all(_is_int(value, 0, 0xFF) for value in flag.values())
            for flag in flags
        )
    ):
        problems.append(
            f"flag_positions must be {PINS} pins with byte x_offset and y_offset"
        )
    if problems:
        raise DeriveError("; ".join(problems))
    try:
        compress_holes([hole])
    except Exception as problem:
        raise DeriveError(f"the hole does not compress: {problem}") from None


def transform_problems(hole: HoleData) -> list[str]:
    """What each known transform raises on `hole`, which a seed's build would hit.

    Nothing about a transform's output is checked: a seed stores its transformed holes
    as built (ADR 0021), so only a transform that cannot run is a problem.
    """
    problems = []
    for name, transform in TRANSFORMS.items():
        texts = (
            [f"{name}:{seed}" for seed in TRANSFORM_SEEDS]
            if transform.seeded
            else [name]
        )
        for text in texts:
            try:
                parse_transform(text)(hole)
            except TransformError as problem:
                problems.append(str(problem))
                break
    return problems


def _next_version(catalog: Catalog, lineage: str, base: CatalogEntry) -> int:
    versions = [entry for entry in catalog if entry.id.lineage == lineage]
    for entry in versions:
        source = entry.source
        if (
            not isinstance(source, DerivedSource)
            or source.base.id.lineage != base.id.lineage
        ):
            raise DeriveError(
                f"{lineage} is already {entry.id}, which is not a hole derived from "
                f"{base.id.lineage}"
            )
    return max((entry.id.version for entry in versions), default=0) + 1


def _check_par(catalog: Catalog, hole_id: HoleId, par: int) -> None:
    """A lineage's slug names its par (`nes_us_12_forward3`), so its versions keep it."""
    first = catalog.entries.get(HoleId(hole_id.lineage))
    if first is not None and first.par != par:
        raise DeriveError(
            f"{hole_id.lineage} is a par {first.par}, and a version of it cannot be a "
            f"par {par}; a hole of another par is a lineage of its own"
        )


def derive(
    catalog: Catalog,
    curation: CurationSnapshot,
    store: HoleStore,
    base_id: HoleId | str,
    edited: HoleData,
    lineage: str,
    author: str,
    *,
    root: Path,
    tags: frozenset[str] | None = None,
    family: str | None = None,
    allow_large: bool = False,
) -> Derivation:
    """The derived hole `edited` makes of `base_id`, as a new version of `lineage`.

    `root` is the directory of the index the entry will be written to. `tags` and
    `family` default to `short` for a hole of lower par than its base and to the base's
    family, which is created, named after the base, when the base has none. A new
    version of an existing lineage keeps the lineage's curation unless they are given.
    Raises DeriveError.
    """
    base = catalog[base_id]
    if base.kind != VANILLA:
        raise DeriveError(f"{base.id} is not a vanilla hole; a base must be one")
    if base.withdrawn:
        raise DeriveError(f"{base.id} is withdrawn")
    owner = HoleId(lineage).lineage.split("/")[0]
    if owner in VANILLA_OWNERS:
        raise DeriveError(
            f"{lineage}: {owner} names a vanilla course; publish under an author, "
            f"or under {COMMUNITY}"
        )
    if not author:
        raise DeriveError("a derived hole needs an author")
    hole_id = HoleId(lineage, _next_version(catalog, lineage, base))

    published, notes = visible(edited)
    check_hole(published)
    _check_par(catalog, hole_id, published.metadata["par"])
    base_canonical = canonical_dict(store.load(base))
    delta = hole_delta.diff(base_canonical, canonical_dict(published), str(base.id))
    if hole_delta.is_empty(delta):
        raise DeriveError(f"the edited hole is identical to {base.id}")
    rebuilt = HoleData.from_dict(hole_delta.apply(base_canonical, delta, str(base.id)))
    digest = content_hash(published)
    if content_hash(rebuilt) != digest:  # pragma: no cover - diff and apply disagree
        raise DeriveError("the delta does not rebuild the edited hole")
    for other in catalog:
        # an older version's content may come back as a new version, which is how a
        # version is undone (docs/catalog.md); a withdrawn version's may not
        restores = (
            other.id.lineage == lineage
            and other.id.version < hole_id.version - 1
            and not other.withdrawn
        )
        if other.content_hash == digest and not restores:
            raise DeriveError(f"the edited hole is identical to {other.id}")

    share = hole_delta.changed_share(base_canonical, delta, "terrain")
    if share > REFUSE_SHARE and not allow_large:
        raise DeriveError(
            f"the delta rewrites {share:.0%} of the terrain the two holes share, so it "
            f"is mostly {base.id} moved within its grid; a derived hole stays in "
            f"register with its base"
        )
    notes = list(notes)
    if share > WARN_SHARE:
        notes.append(
            f"the delta rewrites {share:.0%} of the terrain the two holes share"
        )
    problems = transform_problems(rebuilt)
    if problems:
        raise DeriveError("a transform cannot run on the hole: " + "; ".join(problems))

    entry = CatalogEntry(
        id=hole_id,
        source=DerivedSource(base, delta_file(hole_id), root),
        content_hash=digest,
        par=published.metadata["par"],
        distance=published.metadata["distance"],
        author=author,
    )
    new_catalog = replace(
        catalog,
        version=catalog.version + 1,
        entries={**catalog.entries, hole_id: entry},
    )

    first = hole_id.version == 1
    record = curation.holes.get(lineage, HoleCuration())
    if tags is None and first and entry.par < base.par:
        tags = frozenset({SHORT_TAG})
    if tags is not None:
        record = replace(record, tags=tags)
    new_curation = curation.with_record(lineage, record)
    if family is None and first:
        family = curation.for_hole(base.id).family or suggested_label([base.id.lineage])
    if family is not None:
        members = [lineage]
        if curation.for_hole(base.id).family is None:
            members.append(base.id.lineage)
            notes.append(f"put {base.id.lineage} in the new family {family}")
        new_curation = new_curation.set_family(members, family, move=not first)

    return Derivation(entry, delta, new_catalog, new_curation, share, tuple(notes))


def derived_entries(catalog: Catalog) -> list[CatalogEntry]:
    return [entry for entry in catalog if entry.kind == DERIVED]


def check_derived(catalog: Catalog, store: HoleStore) -> list[str]:
    """Every problem with the catalog's live derived holes whose bases the store holds.

    Each one must build to its content hash, hold only cells that differ from its base,
    and run through every transform. A hole whose base is not in the store, as Mario Open's
    are without its ROM, is left out.
    """
    problems = []
    for entry in derived_entries(catalog):
        source = entry.source
        assert isinstance(source, DerivedSource)
        if not entry.live or not store.has(source.base):
            continue
        try:
            hole = store.load(entry)
            base = canonical_dict(store.load(source.base))
        except CatalogError as problem:
            problems.append(str(problem))
            continue
        if not hole_delta.is_minimal(base, json.loads(source.path.read_text())):
            problems.append(
                f"{entry.id}: {source.path} restates cells of its base, or is not "
                "written as golf-derive writes it"
            )
        problems += [f"{entry.id}: {problem}" for problem in transform_problems(hole)]
    return problems
