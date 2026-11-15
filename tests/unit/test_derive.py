"""Unit tests for publishing derived holes, and for the catalog and store resolving them."""

import json
import subprocess
import sys

import pytest

from golf.formats.hole_data import HoleData
from golf.randomizer import delta as hole_delta
from golf.randomizer import derive as derive_module
from golf.randomizer.catalog import (
    DERIVED,
    REPO_ROOT,
    US_ROM,
    VANILLA,
    Catalog,
    CatalogError,
    DerivedSource,
    HoleId,
    HoleStore,
    canonical_dict,
    content_hash,
    sync_vanilla,
)
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.derive import (
    DeriveError,
    check_derived,
    delta_file,
    derive,
    format_delta,
    transform_problems,
    visible,
)
from golf.randomizer.rehydrate import RehydrateError, verify_holes
from golf.randomizer.transforms import TransformError
from golf.randomizer.twins import signatures
from tests.synthetic_holes import synthetic_hole, write_courses

LINEAGE = "jdharms/nes_us_02_short"


@pytest.fixture(autouse=True)
def transforms_run(monkeypatch):
    """Made-up holes are not holes a transform can read, so the transforms are stood in for."""
    monkeypatch.setattr(derive_module, "transform_problems", lambda hole: [])


@pytest.fixture
def workspace(tmp_path):
    """A hole store of synthetic holes, with the index and curation file that list them."""
    store = HoleStore(write_courses(tmp_path / "courses", "us"))
    catalog = sync_vanilla(Catalog(version=0), store).catalog
    index = tmp_path / "catalog" / "holes.json"
    index.parent.mkdir()
    catalog.save(index)
    curation = tmp_path / "catalog" / "curation.json"
    CurationSnapshot().save(curation)
    return store, index, curation


def short_hole(number: int = 2) -> HoleData:
    """Hole `number` as a forward tee: a par lower, a tee moved and a tee box drawn."""
    hole = synthetic_hole(number)
    hole.metadata["par"] -= 1
    hole.metadata["distance"] = 190
    hole.metadata["tee"] = {"x": 90, "y": 200}
    hole.terrain[25][10] = 0x35
    hole.terrain[25][11] = 0x36
    hole.metadata["scroll_limit"] = 1
    return hole


def base_hole(number: int = 2) -> HoleData:
    hole = synthetic_hole(number)
    hole.metadata["scroll_limit"] = 1
    hole.metadata["tee"] = {"x": 88, "y": 220}
    return hole


@pytest.fixture
def published(workspace):
    """The workspace with hole 2's forward tee published; returns its derivation too."""
    store, index, curation = workspace
    # the synthetic holes carry a scroll limit and a tee no real hole has
    base_hole().save(str(store.root / "us" / "hole_02.json"))
    catalog = sync_vanilla(Catalog(version=0), store).catalog
    catalog.save(index)
    derivation = derive(
        catalog,
        CurationSnapshot.load(curation),
        store,
        "nes_us/02",
        short_hole(),
        LINEAGE,
        "jdharms",
        root=index.parent,
    )
    derivation.write(index, curation)
    return store, index, curation, derivation


def derive_again(published, hole: HoleData, lineage: str = LINEAGE, **options):
    store, index, curation, _ = published
    return derive(
        Catalog.load(index),
        CurationSnapshot.load(curation),
        store,
        options.pop("base", "nes_us/02"),
        hole,
        lineage,
        options.pop("author", "jdharms"),
        root=index.parent,
        **options,
    )


def test_publishing_writes_a_delta_an_entry_and_a_curation_record(published):
    store, index, curation, derivation = published
    entry = Catalog.load(index)[LINEAGE]
    assert entry == derivation.entry
    assert entry.kind == DERIVED
    assert entry.rom == US_ROM
    assert (entry.par, entry.distance, entry.author) == (4, 190, "jdharms")
    assert json.loads(index.read_text())["holes"][LINEAGE]["source"] == {
        "base": "nes_us/02",
        "delta": "derived/jdharms/nes_us_02_short.json",
    }
    delta = json.loads(
        (index.parent / "derived/jdharms/nes_us_02_short.json").read_text()
    )
    assert delta == derivation.delta
    assert delta["metadata"] == {
        "par": 4,
        "distance": 190,
        "tee": {"x": 90, "y": 200},
    }
    assert delta["terrain"] == {"rows": 30, "cells": [[25, 10, "35 36"]]}
    records = CurationSnapshot.load(curation)
    assert records.for_hole(LINEAGE).family == "nes_us_02"
    assert records.for_hole(LINEAGE).tags == {"short"}
    assert records.for_hole("nes_us/02").family == "nes_us_02"


def test_the_index_version_goes_up(published):
    _, index, _, _ = published
    assert Catalog.load(index).version == 2


def test_the_store_builds_a_derived_hole_from_its_base(published):
    store, index, _, _ = published
    entry = Catalog.load(index)[LINEAGE]
    hole = store.load(entry)
    assert content_hash(hole) == entry.content_hash == content_hash(short_hole())
    assert hole.terrain[25][10:12] == [0x35, 0x36]
    assert hole.metadata["tee"] == {"x": 90, "y": 200}
    assert (
        store.path_for(entry) == index.parent / "derived/jdharms/nes_us_02_short.json"
    )


def test_a_delta_file_reads_as_one_run_a_line(published):
    _, _, _, derivation = published
    text = format_delta(derivation.delta)
    assert json.loads(text) == derivation.delta
    assert '      [25, 10, "35 36"]\n' in text
    assert derivation.delta_path.read_text() == text


def test_a_delta_file_holds_its_row_offset():
    delta = {
        "format": 1,
        "base": "nes_us/12",
        "row_offset": 10,
        "metadata": {"green": {"x": 128, "y": 8}},
        "terrain": {"rows": 30, "cells": [[2, 3, "35 36"]]},
        "attributes": {"rows": 15, "cells": []},
    }
    text = format_delta(delta)
    assert json.loads(text) == delta
    assert '  "row_offset": 10,\n' in text


def test_a_version_keeps_its_lineages_par(published):
    hole = short_hole()
    hole.metadata["par"] = 3
    with pytest.raises(DeriveError, match="is a par 4, and a version of it cannot"):
        derive_again(published, hole)
    assert derive_again(published, hole, "community/nes_us_02_short3").entry.par == 3


def test_a_changed_base_fails_the_derived_hole(published):
    store, index, _, _ = published
    hole = base_hole()
    hole.terrain[0][5] = 0x77
    hole.save(str(store.root / "us" / "hole_02.json"))
    with pytest.raises(CatalogError, match=rf"{LINEAGE}: its base, nes_us/02"):
        store.load(Catalog.load(index)[LINEAGE])


def test_an_edited_delta_fails_its_content_hash(published):
    store, index, _, derivation = published
    delta = json.loads(derivation.delta_path.read_text())
    delta["metadata"]["distance"] = 191
    derivation.delta_path.write_text(json.dumps(delta))
    with pytest.raises(CatalogError, match="content hash"):
        store.load(Catalog.load(index)[LINEAGE])


def test_a_malformed_or_missing_delta_is_a_catalog_error(published):
    store, index, _, derivation = published
    entry = Catalog.load(index)[LINEAGE]
    derivation.delta_path.write_text('{"format": 9, "base": "nes_us/02"}')
    with pytest.raises(CatalogError, match="unsupported delta format"):
        store.load(entry)
    derivation.delta_path.write_text("not json")
    with pytest.raises(CatalogError, match=LINEAGE):
        store.load(entry)
    derivation.delta_path.unlink()
    with pytest.raises(CatalogError, match="not found"):
        store.load(entry)


def test_rehydration_checks_derived_holes_with_their_roms(published):
    store, index, _, derivation = published
    catalog = Catalog.load(index)
    assert verify_holes(catalog, store, [US_ROM]) == 19
    assert verify_holes(catalog, store, ["mario_open_jp"]) == 0
    derivation.delta_path.write_text(
        derivation.delta_path.read_text().replace("190", "191")
    )
    with pytest.raises(RehydrateError, match=LINEAGE):
        verify_holes(catalog, store, [US_ROM])


def test_a_later_version_keeps_the_lineages_curation(published):
    _, index, curation, _ = published
    hole = short_hole()
    hole.metadata["distance"] = 200
    derivation = derive_again(published, hole)
    assert derivation.entry.id == HoleId(LINEAGE, 2)
    assert derivation.curation == CurationSnapshot.load(curation)
    assert delta_file(derivation.entry.id) == "derived/jdharms/nes_us_02_short@2.json"
    derivation.write(index, curation)
    catalog = Catalog.load(index)
    assert catalog.newest()[LINEAGE].id.version == 2
    assert catalog.version == 3
    # the first version still builds
    HoleStore(published[0].root).load(catalog[LINEAGE])


def test_an_older_versions_content_can_come_back_as_a_new_version(published):
    _, index, curation, first = published
    hole = short_hole()
    hole.metadata["distance"] = 200
    derive_again(published, hole).write(index, curation)
    # the newest version cannot be published again
    with pytest.raises(DeriveError, match=rf"identical to {LINEAGE}@2"):
        derive_again(published, hole)
    # nor can another lineage take a version's content
    with pytest.raises(DeriveError, match=f"identical to {LINEAGE}"):
        derive_again(published, short_hole(), "jdharms/copy")
    restored = derive_again(published, short_hole())
    assert restored.entry.id == HoleId(LINEAGE, 3)
    assert restored.entry.content_hash == first.entry.content_hash
    assert restored.delta == first.delta


def test_a_withdrawn_versions_content_cannot_come_back(published):
    store, index, curation, _ = published
    data = json.loads(index.read_text())
    data["holes"][LINEAGE]["withdrawn"] = True
    index.write_text(json.dumps(data))
    with pytest.raises(DeriveError, match=f"identical to {LINEAGE}"):
        derive_again(published, short_hole())
    hole = short_hole()
    hole.metadata["distance"] = 200
    derive_again(published, hole).write(index, curation)
    hole.metadata["distance"] = 210
    derive_again(published, hole).write(index, curation)
    with pytest.raises(DeriveError, match=f"identical to {LINEAGE}"):
        derive_again(published, short_hole())


def test_the_checks_a_build_makes_refuse_the_hole(published, monkeypatch):
    monkeypatch.setattr(
        derive_module, "compression_problem", lambda hole: "no pattern for this row"
    )
    hole = short_hole()
    hole.metadata["distance"] = 200
    with pytest.raises(DeriveError, match="does not compress: no pattern"):
        derive_again(published, hole, "jdharms/other")


def test_the_store_has_a_derived_hole_only_with_its_base(published):
    store, index, curation, _ = published
    catalog = Catalog.load(index)
    assert store.has(catalog[LINEAGE])
    assert [one.lineage for one in signatures(catalog, store)].count(LINEAGE) == 1
    (store.root / "us" / "hole_02.json").unlink()
    assert not store.has(catalog[LINEAGE])
    assert store.has(catalog["nes_us/03"])
    # what reads every hole present skips it, as it does its base
    lineages = [one.lineage for one in signatures(catalog, store)]
    assert LINEAGE not in lineages and "nes_us/02" not in lineages
    assert len(lineages) == 17


def test_tags_and_family_can_be_given(published):
    hole = short_hole()
    hole.metadata["distance"] = 200
    derivation = derive_again(
        published,
        hole,
        "community/nes_us_02_variant",
        tags=frozenset({"variant", "scenic"}),
        family="other_family",
    )
    record = derivation.curation.for_hole("community/nes_us_02_variant")
    assert record.tags == {"variant", "scenic"}
    assert record.family == "other_family"
    assert derivation.curation.for_hole("nes_us/02").family == "nes_us_02"


def test_a_hole_of_the_same_par_gets_no_tag(published):
    hole = short_hole()
    hole.metadata["par"] += 1
    derivation = derive_again(published, hole, "jdharms/nes_us_02_left")
    assert derivation.curation.for_hole("jdharms/nes_us_02_left").tags == frozenset()
    assert derivation.curation.for_hole("jdharms/nes_us_02_left").family == "nes_us_02"


def test_hidden_rows_and_their_palettes_are_not_published(workspace):
    hole = synthetic_hole(2)
    hole.terrain += [[0x99] * 22, [0x99] * 22]
    hole.attributes.append([2] * 11)
    seen, notes = visible(hole)
    assert len(seen.terrain) == 30
    assert len(seen.attributes) == 15
    assert notes == ("dropped 1 attribute rows below the visible 30 terrain rows",)
    again, notes = visible(seen)
    assert notes == ()
    assert content_hash(again) == content_hash(seen)


def test_a_hole_no_seed_could_build_is_refused(published):
    """Every problem is named; the rules themselves are tests/unit/test_hole_check.py's."""
    hole = short_hole()
    hole.metadata["distance"] = 200
    hole.metadata["par"] = 6
    hole.metadata["handicap"] = 0
    with pytest.raises(DeriveError, match="par must be one of .*; handicap must be"):
        derive_again(published, hole, "jdharms/other")


def test_refusals(published):
    hole = short_hole()
    hole.metadata["distance"] = 200
    with pytest.raises(DeriveError, match="identical to nes_us/02"):
        derive_again(published, base_hole(), "jdharms/same")
    with pytest.raises(DeriveError, match=f"identical to {LINEAGE}"):
        derive_again(published, short_hole(), "jdharms/same")
    with pytest.raises(DeriveError, match="names a vanilla course"):
        derive_again(published, hole, "nes_us/02_short")
    with pytest.raises(DeriveError, match="not a vanilla hole"):
        derive_again(published, hole, "jdharms/chained", base=LINEAGE)
    with pytest.raises(DeriveError, match="needs an author"):
        derive_again(published, hole, "jdharms/other", author="")
    with pytest.raises(DeriveError, match="not a hole derived from nes_us/03"):
        derive_again(published, hole, LINEAGE, base="nes_us/03")
    with pytest.raises(CatalogError, match="not in the catalog"):
        derive_again(published, hole, "jdharms/other", base="nes_us/99")
    with pytest.raises(CatalogError, match="bad hole lineage"):
        derive_again(published, hole, "jdharms/us-02-short")


def test_a_delta_is_never_overwritten(published):
    _, index, curation, derivation = published
    with pytest.raises(DeriveError, match="already exists"):
        derivation.write(index, curation)


def test_a_hole_moved_within_its_grid_is_refused(published):
    hole = short_hole()
    hole.terrain = [row[1:] + row[:1] for row in hole.terrain]
    with pytest.raises(DeriveError, match="moved within its grid"):
        derive_again(published, hole, "jdharms/shifted")
    derivation = derive_again(published, hole, "jdharms/shifted", allow_large=True)
    assert derivation.terrain_share > 0.6
    assert any("rewrites" in note for note in derivation.notes)


def test_a_transform_that_cannot_run_refuses_the_hole(published, monkeypatch):
    monkeypatch.setattr(
        derive_module, "transform_problems", lambda hole: ["mirror@1: no partner"]
    )
    hole = short_hole()
    hole.metadata["distance"] = 200
    with pytest.raises(DeriveError, match="mirror@1: no partner"):
        derive_again(published, hole, "jdharms/other")


def test_transform_problems_names_each_transform_that_raises(monkeypatch):
    def refuse(text):
        def apply(hole):
            raise TransformError(f"{text}: cannot")

        return apply

    monkeypatch.setattr(derive_module, "parse_transform", refuse)
    problems = transform_problems(synthetic_hole(1))
    assert "mirror@1: cannot" in problems
    assert "hazards@1:0: cannot" in problems
    assert len(problems) == len(derive_module.TRANSFORMS)


def test_check_derived_passes_a_published_hole_and_names_a_restated_delta(published):
    store, index, _, derivation = published
    assert check_derived(Catalog.load(index), store) == []
    delta = json.loads(derivation.delta_path.read_text())
    base = canonical_dict(store.load(Catalog.load(index)["nes_us/02"]))
    row = base["terrain"]["rows"][25].split()
    delta["terrain"]["cells"] = [[25, 9, f"{row[9]} 35 36"]]
    derivation.delta_path.write_text(json.dumps(delta))
    assert hole_delta.apply(base, delta) == canonical_dict(short_hole())
    problems = check_derived(Catalog.load(index), store)
    assert len(problems) == 1 and "restates cells of its base" in problems[0]


def test_check_derived_skips_a_hole_whose_base_is_not_dumped(published):
    store, index, _, _ = published
    (store.root / "us" / "hole_02.json").unlink()
    assert check_derived(Catalog.load(index), store) == []


def test_check_derived_reports_a_missing_delta(published):
    store, index, _, derivation = published
    derivation.delta_path.unlink()
    problems = check_derived(Catalog.load(index), store)
    assert len(problems) == 1 and "not found" in problems[0]


class TestIndex:
    def data(self, published) -> dict:
        return json.loads(published[1].read_text())

    def test_round_trips(self, published):
        _, index, _, _ = published
        catalog = Catalog.load(index)
        assert Catalog.from_json(catalog.to_json(), index.parent) == catalog
        source = catalog[LINEAGE].source
        assert isinstance(source, DerivedSource)
        assert source.base == catalog["nes_us/02"]
        assert source.base.kind == VANILLA

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ({"base": "nes_us/99", "delta": "derived/a.json"}, "is not a vanilla hole"),
            ({"base": LINEAGE, "delta": "derived/a.json"}, "is not a vanilla hole"),
            ({"base": "nes_us/02@1", "delta": "derived/a.json"}, "not canonical"),
            ({"base": 2, "delta": "derived/a.json"}, "must be a hole id"),
            ({"base": "nes_us/02", "delta": "../a.json"}, "under the catalog"),
            ({"base": "nes_us/02", "delta": "/tmp/a.json"}, "under the catalog"),
            ({"base": "nes_us/02", "delta": ""}, "under the catalog"),
            ({"base": "nes_us/02", "delta": 3}, "under the catalog"),
            ({"base": "nes_us/02"}, "unrecognized source"),
        ],
    )
    def test_refuses_a_bad_derived_source(self, published, source, message):
        data = self.data(published)
        data["holes"]["jdharms/other"] = data["holes"][LINEAGE] | {"source": source}
        with pytest.raises(CatalogError, match=message):
            Catalog.from_json(data)

    def test_a_derived_hole_whose_base_is_withdrawn_is_not_drawable(self, published):
        store, index, _, _ = published
        data = self.data(published)
        data["holes"]["nes_us/02"]["withdrawn"] = True
        catalog = Catalog.from_json(data, index.parent)
        entry = catalog[LINEAGE]
        assert not entry.live
        assert LINEAGE not in catalog.newest()
        with pytest.raises(CatalogError, match="its base, nes_us/02 is withdrawn"):
            store.load(entry)
        assert (
            content_hash(store.load(entry, even_withdrawn=True)) == entry.content_hash
        )
        assert verify_holes(catalog, store, [US_ROM]) == 17


def run_cli(*args: str, index, curation, holes):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.data.derive",
            "--catalog",
            str(index),
            "--curation",
            str(curation),
            "--holes",
            str(holes),
            *args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_cli_shows_exports_and_checks(published, tmp_path):
    store, index, curation, _ = published
    paths = dict(index=index, curation=curation, holes=store.root)

    shown = run_cli("show", LINEAGE, "--delta", **paths)
    assert shown.returncode == 0, shown.stderr
    assert "from nes_us/02, by jdharms" in shown.stdout
    assert "par 5 -> 4" in shown.stdout
    assert '[25, 10, "35 36"]' in shown.stdout

    exported = tmp_path / "exported.json"
    result = run_cli("export", LINEAGE, "-o", str(exported), **paths)
    assert result.returncode == 0, result.stderr
    hole = HoleData()
    hole.load(exported)
    assert content_hash(hole) == content_hash(short_hole())
    assert run_cli("export", LINEAGE, "-o", str(exported), **paths).returncode == 1

    assert run_cli("show", "nes_us/02", **paths).returncode == 1
