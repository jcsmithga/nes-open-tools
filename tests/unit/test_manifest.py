"""Unit tests for the manifest model and its JSON form."""

import json
from dataclasses import replace
from typing import Any

import pytest

from golf.core.patches.sram_defaults import Club
from golf.core.rng import predict_hole
from golf.randomizer.build import BUILD_VERSION, FINISH_ABI_VERSION
from golf.randomizer.catalog import (
    JP_ROM,
    US_ROM,
    Catalog,
    CatalogEntry,
    DerivedSource,
    FileSource,
    HoleId,
    RomSource,
)
from golf.randomizer.manifest import (
    DEFAULT_DRAW_RULE,
    LEGACY_BUILD_VERSION,
    LEGACY_FINISH_ABI_VERSION,
    SCHEMA,
    ClubRules,
    Course,
    DrawRule,
    Manifest,
    ManifestError,
    Settings,
    Slot,
    StoredDrawRule,
    StoredManifest,
    required_roms,
)
from tests.legacy_manifest import as_schema

PARS = (4, 5, 4, 3, 4, 3, 4, 5, 4, 4, 4, 3, 5, 4, 5, 3, 4, 4)


def slots(prefix: str = "nes_us") -> tuple[Slot, ...]:
    return tuple(
        Slot(HoleId(f"{prefix}/{number:02d}"), par, number)
        for number, par in enumerate(PARS, start=1)
    )


def course(**overrides) -> Course:
    fields: dict[str, Any] = dict(
        holes=slots(),
        music="nes_uk",
        mercy_point=9,
        clubs=ClubRules(),
        magic_words=("DIVOT", "CADDY", "BOGEY"),
        sram_magic=0x5247,
    )
    return Course(**(fields | overrides))


def manifest(**overrides) -> Manifest:
    fields: dict[str, Any] = dict(
        schema=SCHEMA,
        generator_version=1,
        build_version=BUILD_VERSION,
        finish_abi_version=FINISH_ABI_VERSION,
        catalog_version=1,
        curation_stamp="stamp",
        settings=Settings(prng_seed="abc"),
        course=course(),
    )
    return Manifest(**(fields | overrides))


def round_trip(value: Manifest) -> Manifest:
    return Manifest.from_json(json.loads(json.dumps(value.to_json())))


def test_round_trips_through_json():
    value = manifest(
        settings=Settings(
            prng_seed="abc",
            par=70,
            sources=frozenset({JP_ROM}),
            exclude_tags=frozenset({"expert"}),
            allow_family_repeats=True,
            music="jp_hawaii",
            mercy_point=None,
            clubs=ClubRules(
                max=10,
                banned=frozenset({Club.W1}),
                required_bag=frozenset({Club.I7, Club.W3}),
            ),
        ),
        course=course(
            mercy_point=None,
            clubs=ClubRules(max=12, banned=frozenset({Club.SW, Club.W2})),
        ),
    )
    assert round_trip(value) == value


def test_json_shape():
    data = manifest().to_json()
    assert list(data) == [
        "schema",
        "generator_version",
        "build_version",
        "finish_abi_version",
        "catalog_version",
        "curation_stamp",
        "settings",
        "course",
    ]
    assert data["course"]["holes"][0] == {
        "id": "nes_us/01",
        "par": 4,
        "transforms": [],
        "wind_seed": 1,
        "wind_direction": 0xF0,
        "wind_speed": 3,
    }
    assert data["settings"]["clubs"] == {"max": 14, "banned": [], "required_bag": None}
    assert data["settings"]["sources"] == [US_ROM, JP_ROM]


def test_course_json_carries_the_sram_magic():
    assert manifest().to_json()["course"]["sram_magic"] == 0x5247
    data = manifest().to_json()
    del data["course"]["sram_magic"]
    with pytest.raises(ManifestError, match="missing fields \\['sram_magic'\\]"):
        Manifest.from_json(data)


@pytest.mark.parametrize(
    "sram_magic", [0x0047, 0xFF47, 0x5200, 0x52FF, -1, 0x10000, "0x5247", True, None]
)
def test_rejects_a_bad_sram_magic(sram_magic):
    with pytest.raises(ManifestError, match="sram_magic"):
        course(sram_magic=sram_magic)
    data = manifest().to_json()
    data["course"]["sram_magic"] = sram_magic
    with pytest.raises(ManifestError, match="sram_magic"):
        Manifest.from_json(data)


def test_course_par_and_layout():
    assert course().layout == PARS
    assert course().par == 72


def test_club_labels_are_written_in_club_order():
    rules = ClubRules(banned=frozenset({Club.SW, Club.W1, Club.I5}))
    assert rules.to_json()["banned"] == ["1W", "5I", "SW"]


def test_required_bag_gains_the_putter():
    assert ClubRules(required_bag=frozenset({Club.W1})).required_bag == {
        Club.W1,
        Club.PT,
    }


@pytest.mark.parametrize(
    "rules, message",
    [
        (dict(max=0), "max must be"),
        (dict(max=15), "max must be"),
        (dict(banned={Club.PT}), "putter cannot be banned"),
        (dict(max=2, required_bag={Club.W1, Club.W2}), "over the max"),
        (dict(banned={Club.W1}, required_bag={Club.W1}), "includes banned"),
    ],
)
def test_rejects_bad_club_rules(rules, message):
    with pytest.raises(ManifestError, match=message):
        ClubRules(**rules)


@pytest.mark.parametrize(
    "data",
    [
        {"max": 14, "banned": ["1W", "1W"], "required_bag": None},
        {"max": 14, "banned": ["5W"], "required_bag": None},
        {"max": True, "banned": [], "required_bag": None},
        {"max": 14, "banned": []},
    ],
)
def test_rejects_bad_club_json(data):
    with pytest.raises(ManifestError):
        ClubRules.from_json(data)


@pytest.mark.parametrize(
    "settings",
    [
        dict(par=73),
        dict(sources=set()),
        dict(sources={"nes_open_eu"}),
        dict(music="nes_mars"),
        dict(mercy_point=0),
        dict(mercy_point=256),
        dict(prng_seed=""),
        dict(allow_family_repeats="yes"),
    ],
)
def test_rejects_bad_settings(settings):
    with pytest.raises(ManifestError):
        Settings(**settings)


def test_rejects_bad_courses():
    with pytest.raises(ManifestError, match="18 holes"):
        course(holes=slots()[:17])
    with pytest.raises(ManifestError, match="more than once"):
        course(holes=slots()[:17] + slots()[:1])
    with pytest.raises(ManifestError, match="course music"):
        course(music="random")
    with pytest.raises(ManifestError, match="uppercase"):
        course(magic_words=("divot", "CADDY", "BOGEY"))
    with pytest.raises(ManifestError, match="magic_words"):
        course(magic_words=("DIVOT", "DIVOT", "BOGEY"))


def test_rejects_bad_slots():
    with pytest.raises(ManifestError, match="wind_seed"):
        Slot(HoleId("nes_us/01"), 4, 0x10000)
    with pytest.raises(ManifestError, match="par"):
        Slot(HoleId("nes_us/01"), 6, 0)
    with pytest.raises(ManifestError, match="unknown transform 'mirror'"):
        Slot(HoleId("nes_us/01"), 4, 0, ("mirror",))
    with pytest.raises(ManifestError, match="transform must be a string"):
        Slot.from_json(
            {
                "id": "nes_us/01",
                "par": 4,
                "transforms": [1],
                "wind_seed": 0,
                "wind_direction": 0,
                "wind_speed": 0,
            }
        )
    with pytest.raises(ManifestError, match="not canonical"):
        Slot.from_json(
            {
                "id": "nes_us/01@1",
                "par": 4,
                "transforms": [],
                "wind_seed": 0,
                "wind_direction": 0,
                "wind_speed": 0,
            }
        )


def test_a_slot_takes_the_wind_its_seed_deals_unless_given_anchors():
    forecast = predict_hole(0x1234, swings=0)
    dealt = (forecast.direction_anchor, forecast.speed_anchor)
    assert Slot(HoleId("nes_us/01"), 4, 0x1234).wind == dealt
    given = Slot(HoleId("nes_us/01"), 4, 0x1234, wind_direction=0x80, wind_speed=10)
    assert given.wind == (0x80, 10) and given.dealt_wind == dealt
    assert Slot.from_json(given.to_json()) == given


@pytest.mark.parametrize(
    "anchors",
    [
        {"wind_direction": 0x08},
        {"wind_direction": 0x100},
        {"wind_direction": -16},
        {"wind_direction": "80"},
        {"wind_direction": True},
        {"wind_speed": 11},
        {"wind_speed": -1},
        {"wind_speed": 2.0},
    ],
)
def test_a_slot_refuses_anchors_the_rom_table_cannot_hold(anchors):
    with pytest.raises(ManifestError, match="wind_"):
        Slot(HoleId("nes_us/01"), 4, 0, **anchors)


@pytest.mark.parametrize("field", ["wind_direction", "wind_speed"])
def test_the_current_schema_requires_each_holes_anchors(field):
    data = manifest().to_json()
    del data["course"]["holes"][3][field]
    with pytest.raises(ManifestError, match=f"missing fields \\['{field}'\\]"):
        Manifest.from_json(data)


@pytest.mark.parametrize("field", ["wind_speed_profile", "wind_direction_profile"])
def test_settings_refuse_an_unknown_wind_profile(field):
    with pytest.raises(ManifestError, match=field):
        replace(Settings(), **{field: "hurricane"})
    data = manifest().to_json()
    del data["settings"][field]
    with pytest.raises(ManifestError, match=f"missing fields \\['{field}'\\]"):
        Manifest.from_json(data)


def test_the_default_wind_profiles_are_vanilla():
    settings = Settings()
    assert settings.wind_speed_profile == settings.wind_direction_profile == "vanilla"


def test_rejects_missing_and_unknown_fields():
    data = manifest().to_json()
    del data["course"]["music"]
    with pytest.raises(ManifestError, match="missing fields \\['music'\\]"):
        Manifest.from_json(data)
    data = manifest().to_json()
    data["settings"]["practice_swing"] = True
    with pytest.raises(ManifestError, match="unknown \\['practice_swing'\\]"):
        Manifest.from_json(data)


def test_the_current_schema_requires_a_draw_rule():
    data = manifest().to_json()
    del data["settings"]["draw_rule"]
    with pytest.raises(ManifestError, match="missing fields \\['draw_rule'\\]"):
        Manifest.from_json(data)


def test_the_default_draw_rule_caps_expert_holes_at_one_a_nine():
    assert Settings().draw_rule == DEFAULT_DRAW_RULE == DrawRule.expert_cap(1)
    assert Settings().to_json()["draw_rule"] == {"rule": "expert_cap", "per_nine": 1}


@pytest.mark.parametrize(
    "rule", [DrawRule(), DrawRule.expert_cap(0), DrawRule.expert_cap(9)]
)
def test_a_draw_rule_round_trips(rule):
    assert DrawRule.from_json(json.loads(json.dumps(rule.to_json()))) == rule
    assert round_trip(manifest(settings=Settings(prng_seed="abc", draw_rule=rule)))


@pytest.mark.parametrize(
    "data",
    [
        None,
        "uniform",
        {},
        {"rule": "ceiling"},
        {"rule": "uniform", "per_nine": 1},
        {"rule": "expert_cap"},
        {"rule": "expert_cap", "per_nine": -1},
        {"rule": "expert_cap", "per_nine": 10},
        {"rule": "expert_cap", "per_nine": True},
        {"rule": "expert_cap", "per_nine": "1"},
        {"rule": "expert_cap", "per_nine": 1, "extra": 0},
    ],
)
def test_a_malformed_draw_rule_is_refused(data):
    with pytest.raises(ManifestError):
        DrawRule.from_json(data)


def test_settings_refuse_a_draw_rule_that_is_not_one():
    with pytest.raises(ManifestError, match="draw_rule"):
        Settings(draw_rule="uniform")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, 0, -1, True, "2"])
def test_a_manifest_requires_a_positive_integer_build_version(value):
    data = manifest().to_json()
    data["build_version"] = value
    with pytest.raises(ManifestError, match="build_version"):
        Manifest.from_json(data)


def test_a_manifest_requires_the_build_version_field():
    data = manifest().to_json()
    del data["build_version"]
    with pytest.raises(ManifestError, match="missing fields.*build_version"):
        Manifest.from_json(data)


@pytest.mark.parametrize("value", [None, 0, -1, True, "1"])
def test_a_manifest_requires_a_positive_integer_finish_abi_version(value):
    data = manifest().to_json()
    data["finish_abi_version"] = value
    with pytest.raises(ManifestError, match="finish_abi_version"):
        Manifest.from_json(data)


def test_a_manifest_requires_the_finish_abi_version_field():
    data = manifest().to_json()
    del data["finish_abi_version"]
    with pytest.raises(ManifestError, match="missing fields.*finish_abi_version"):
        Manifest.from_json(data)


@pytest.mark.parametrize("schema", [1, 2, 3, SCHEMA + 1])
def test_the_strict_loader_reads_only_this_releases_schema(schema):
    data = manifest().to_json() | {"schema": schema, "something_new": 1}
    with pytest.raises(ManifestError, match=f"schema {schema} .*StoredManifest"):
        Manifest.from_json(data)
    with pytest.raises(ManifestError, match="StoredManifest"):
        manifest(schema=schema)


def test_a_manifest_records_its_prng_seed():
    with pytest.raises(ManifestError, match="prng_seed"):
        manifest(settings=Settings())


def test_include_round_trips_and_is_written_in_a_fixed_order():
    value = manifest(
        settings=Settings(prng_seed="abc", include=frozenset({"community", "derived"}))
    )
    assert value.to_json()["settings"]["include"] == ["derived", "community"]
    assert round_trip(value) == value
    assert manifest().to_json()["settings"]["include"] == []


@pytest.mark.parametrize("include", [{"vanilla"}, {"derived", "other"}, {""}])
def test_settings_refuse_a_kind_that_cannot_be_included(include):
    with pytest.raises(ManifestError, match="include"):
        Settings(include=frozenset(include))


def test_the_current_schema_requires_include():
    data = manifest().to_json()
    del data["settings"]["include"]
    with pytest.raises(ManifestError, match="include"):
        Manifest.from_json(data)


def test_required_roms_follow_a_derived_hole_to_its_base():
    entries = {
        slot.id: CatalogEntry(
            slot.id, RomSource(US_ROM, "us", number), "0" * 64, slot.par, 400, "N"
        )
        for number, slot in enumerate(slots(), start=1)
    }
    base = CatalogEntry(
        HoleId("jp_uk/03"), RomSource(JP_ROM, "jp_uk", 3), "0" * 64, 5, 500, "N"
    )
    first = slots()[0]
    short = HoleId("jdharms/jp_uk_03_short")
    entries[short] = CatalogEntry(
        short, DerivedSource(base, "derived/a.json"), "1" * 64, first.par, 190, "j"
    )
    entries[HoleId("dharms/cliffside")] = CatalogEntry(
        HoleId("dharms/cliffside"), FileSource("a.json"), "2" * 64, 4, 400, "d"
    )
    holes = Catalog(1, entries)
    with_short = course(holes=(Slot(short, first.par, 1), *slots()[1:]))
    assert required_roms(manifest(course=with_short), holes) == (US_ROM, JP_ROM)
    second = slots()[1]
    with_file = course(
        holes=(
            slots()[0],
            Slot(HoleId("dharms/cliffside"), second.par, 2),
            *slots()[2:],
        )
    )
    assert required_roms(manifest(course=with_file), holes) == (US_ROM,)


def test_required_roms_count_holes_and_music():
    entries = [
        CatalogEntry(
            slot.id,
            RomSource(US_ROM, "us", number),
            "0" * 64,
            slot.par,
            400,
            "Nintendo",
        )
        for number, slot in enumerate(slots(), start=1)
    ]
    holes = Catalog(1, {item.id: item for item in entries})
    assert required_roms(manifest(), holes) == (US_ROM,)
    assert required_roms(manifest(course=course(music="jp_france")), holes) == (
        US_ROM,
        JP_ROM,
    )


# -- Stored manifests -----------------------------------------------------------------------

UNIFORM_SETTINGS = Settings(prng_seed="abc", draw_rule=DrawRule())


def issued(schema: int, **overrides) -> dict:
    """A manifest's JSON as a release of `schema` issued it, its seed made as that
    release's were: a uniform draw, and the wind its seeds deal."""
    value = manifest(**({"settings": UNIFORM_SETTINGS} | overrides))
    return value.to_json() if schema == SCHEMA else as_schema(value.to_json(), schema)


@pytest.mark.parametrize("schema", [1, 2, 3, SCHEMA])
def test_a_stored_manifest_of_any_schema_reads_as_its_seed_was_made(schema):
    stored = StoredManifest.from_json(issued(schema))
    made = manifest(settings=UNIFORM_SETTINGS)
    assert stored.schema == schema
    if schema == 1:
        assert (stored.build_version, stored.finish_abi_version) == (
            LEGACY_BUILD_VERSION,
            LEGACY_FINISH_ABI_VERSION,
        )
    else:
        assert (stored.build_version, stored.finish_abi_version) == (
            made.build_version,
            made.finish_abi_version,
        )
    settings = stored.settings
    assert (settings.prng_seed, settings.par) == ("abc", made.settings.par)
    assert settings.sources == made.settings.sources
    assert settings.include == frozenset()
    assert settings.draw_rule == StoredDrawRule("uniform")
    assert (settings.wind_speed_profile, settings.wind_direction_profile) == (
        "vanilla",
        "vanilla",
    )
    course = stored.course
    assert (course.music, course.mercy_point, course.clubs) == (
        made.course.music,
        made.course.mercy_point,
        made.course.clubs,
    )
    assert (course.magic_words, course.sram_magic) == (
        made.course.magic_words,
        made.course.sram_magic,
    )
    assert (course.par, course.layout) == (made.course.par, made.course.layout)
    for slot, made_slot in zip(course.holes, made.course.holes, strict=True):
        assert (slot.id, slot.par, slot.wind_seed) == (
            made_slot.id,
            made_slot.par,
            made_slot.wind_seed,
        )
        assert slot.wind == made_slot.dealt_wind == made_slot.wind


def test_a_stored_manifest_reads_every_field_the_current_schema_has():
    settings = Settings(
        prng_seed="abc",
        include=frozenset({"derived"}),
        exclude_tags=frozenset({"short"}),
        allow_family_repeats=True,
        draw_rule=DrawRule.expert_cap(0),
        wind_speed_profile="strong",
        wind_direction_profile="out_and_back",
    )
    holes = slots()
    moved = replace(
        holes[0], wind_direction=0x80, wind_speed=10, transforms=("mirror@1",)
    )
    value = manifest(settings=settings, course=course(holes=(moved, *holes[1:])))
    stored = StoredManifest.from_json(value.to_json())
    assert stored.settings.include == frozenset({"derived"})
    assert stored.settings.exclude_tags == frozenset({"short"})
    assert stored.settings.allow_family_repeats
    assert stored.settings.draw_rule == StoredDrawRule("expert_cap", 0)
    assert stored.settings.wind_speed_profile == "strong"
    assert stored.settings.wind_direction_profile == "out_and_back"
    assert stored.course.holes[0].wind == (0x80, 10)
    assert stored.course.holes[0].transforms == ("mirror@1",)


def test_a_stored_manifest_ignores_fields_this_release_does_not_read():
    data = issued(SCHEMA)
    data["request"] = {"sets": []}
    data["settings"]["practice_swing"] = True
    data["course"]["holes"][0]["lie"] = "fairway"
    data["course"]["theme"] = {"palette": 3}
    assert StoredManifest.from_json(data).course.par == manifest().course.par


def test_a_stored_manifest_keeps_names_this_release_no_longer_has():
    data = issued(SCHEMA)
    data["settings"]["draw_rule"] = {"rule": "ceiling", "per_nine": 2}
    data["settings"]["wind_speed_profile"] = "hurricane"
    data["settings"]["wind_direction_profile"] = "swirling"
    data["course"]["music"] = "jp_mars"
    stored = StoredManifest.from_json(data)
    assert stored.settings.draw_rule == StoredDrawRule("ceiling", 2)
    assert stored.settings.draw_rule.to_json() == {"rule": "ceiling", "per_nine": 2}
    assert (
        stored.settings.wind_speed_profile,
        stored.settings.wind_direction_profile,
    ) == ("hurricane", "swirling")
    assert stored.course.music == "jp_mars"


def test_a_stored_manifest_keeps_transform_names_the_registry_lacks():
    data = issued(SCHEMA)
    data["course"]["holes"][0]["transforms"] = ["spin@9:1", "mirror@1"]
    stored = StoredManifest.from_json(data)
    assert stored.course.holes[0].transforms == ("spin@9:1", "mirror@1")
    with pytest.raises(ManifestError, match="unknown transform 'spin@9:1'"):
        Manifest.from_json(data)


@pytest.mark.parametrize(
    "path",
    [
        ("schema",),
        ("generator_version",),
        ("settings", "par"),
        ("settings", "sources"),
        ("course", "sram_magic"),
        ("course", "clubs"),
        ("course", "magic_words"),
    ],
)
def test_a_stored_manifest_needs_the_fields_every_schema_has(path):
    data = issued(1)
    target = data
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    with pytest.raises(ManifestError, match=f"missing field '{path[-1]}'"):
        StoredManifest.from_json(data)


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda data: data.update(finish_abi_version=0), "finish_abi_version"),
        (lambda data: data.update(build_version="6"), "build_version"),
        (lambda data: data["course"].update(sram_magic=0x00FF), "sram"),
        (lambda data: data["course"].update(sram_magic=True), "sram_magic"),
        (lambda data: data["course"]["clubs"].update(max=0), "clubs max"),
        (lambda data: data["course"]["clubs"].update(banned=["PT"]), "putter"),
        (lambda data: data["settings"].update(draw_rule={"rule": 3}), "draw rule"),
        (lambda data: data["settings"].update(draw_rule="uniform"), "draw_rule"),
        (lambda data: data["course"]["holes"][0].update(wind_speed=11), "wind_speed"),
        (lambda data: data["course"]["holes"][0].update(par=6), "par"),
        (lambda data: data["course"]["holes"][0].update(id="Not An Id"), "hole"),
    ],
)
def test_a_stored_manifest_refuses_values_no_release_issued(change, message):
    data = issued(SCHEMA)
    change(data)
    with pytest.raises(ManifestError, match=message):
        StoredManifest.from_json(data)
