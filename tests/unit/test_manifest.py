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
    DEFAULT_EXPERTS_PER_NINE,
    LEGACY_BUILD_VERSION,
    LEGACY_FINISH_ABI_VERSION,
    LEGACY_SCHEMA,
    LEGACY_SCHEMAS,
    SCHEMA,
    ClubRules,
    Course,
    Manifest,
    ManifestError,
    Settings,
    Slot,
    required_roms,
    standard_rules,
)
from golf.randomizer.rules import Rule, parse_rules
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
            {"id": "nes_us/01", "par": 4, "transforms": [1], "wind_seed": 0},
            legacy=True,
        )
    with pytest.raises(ManifestError, match="not canonical"):
        Slot.from_json(
            {"id": "nes_us/01@1", "par": 4, "transforms": [], "wind_seed": 0},
            legacy=True,
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


UNIFORM_SETTINGS = Settings(prng_seed="abc", constraints=standard_rules(72, None))


def test_schema_one_loads_with_its_implicit_versions_and_keeps_its_shape():
    data = as_schema(manifest(settings=UNIFORM_SETTINGS).to_json(), LEGACY_SCHEMA)
    loaded = Manifest.from_json(data)
    assert loaded.build_version == LEGACY_BUILD_VERSION
    assert loaded.finish_abi_version == LEGACY_FINISH_ABI_VERSION
    assert loaded.settings == UNIFORM_SETTINGS
    assert loaded.to_json() == data


def test_schema_one_cannot_claim_another_build_version():
    with pytest.raises(ManifestError, match="implies build_version 1"):
        manifest(schema=LEGACY_SCHEMA, build_version=2, settings=UNIFORM_SETTINGS)


def test_schema_one_cannot_claim_another_finish_abi():
    with pytest.raises(ManifestError, match="implies finish_abi_version 1"):
        manifest(
            schema=LEGACY_SCHEMA,
            build_version=LEGACY_BUILD_VERSION,
            finish_abi_version=2,
            settings=UNIFORM_SETTINGS,
        )


def test_schema_two_loads_with_no_expert_cap_and_keeps_its_shape():
    data = as_schema(manifest(settings=UNIFORM_SETTINGS).to_json(), 2)
    loaded = Manifest.from_json(data)
    assert loaded.settings.experts_per_nine is None
    assert loaded.settings.rules == standard_rules(72, None)
    assert loaded.to_json() == data


@pytest.mark.parametrize("schema", LEGACY_SCHEMAS)
def test_a_legacy_schema_loads_with_the_wind_its_seeds_deal(schema):
    data = as_schema(manifest(settings=UNIFORM_SETTINGS).to_json(), schema)
    assert "wind_direction" not in data["course"]["holes"][0]
    loaded = Manifest.from_json(data)
    assert loaded.settings.wind_speed_profile == "vanilla"
    assert loaded.settings.wind_direction_profile == "vanilla"
    for slot in loaded.course.holes:
        forecast = predict_hole(slot.wind_seed, swings=0)
        assert slot.wind == (forecast.direction_anchor, forecast.speed_anchor)
    assert loaded.to_json() == data


@pytest.mark.parametrize("schema", LEGACY_SCHEMAS)
def test_a_legacy_schema_refuses_wind_profiles_and_anchors(schema):
    legacy = dict(
        schema=schema,
        build_version=LEGACY_BUILD_VERSION,
        finish_abi_version=LEGACY_FINISH_ABI_VERSION,
    )
    with pytest.raises(ManifestError, match="no wind profiles or anchors"):
        manifest(
            **legacy, settings=replace(UNIFORM_SETTINGS, wind_speed_profile="strong")
        )
    holes = slots()
    dealt_direction, _ = holes[0].dealt_wind
    moved = replace(holes[0], wind_direction=dealt_direction ^ 0x80)
    with pytest.raises(ManifestError, match="no wind profiles or anchors"):
        manifest(
            **legacy,
            settings=UNIFORM_SETTINGS,
            course=course(holes=(moved, *holes[1:])),
        )
    data = as_schema(manifest(settings=UNIFORM_SETTINGS).to_json(), schema)
    data["course"]["holes"][0]["wind_speed"] = 3
    with pytest.raises(ManifestError, match="unknown \\['wind_speed'\\]"):
        Manifest.from_json(data)


@pytest.mark.parametrize("schema", LEGACY_SCHEMAS)
def test_a_legacy_schema_refuses_an_expert_cap(schema):
    data = as_schema(manifest(settings=UNIFORM_SETTINGS).to_json(), schema)
    data["settings"]["draw_rule"] = {"rule": "uniform"}
    with pytest.raises(ManifestError, match="unknown \\['draw_rule'\\]"):
        Manifest.from_json(data)
    with pytest.raises(ManifestError, match="drawn without one"):
        manifest(
            schema=schema,
            build_version=LEGACY_BUILD_VERSION,
            finish_abi_version=LEGACY_FINISH_ABI_VERSION,
        )


def test_the_current_schema_has_no_draw_rule():
    data = manifest().to_json()
    assert "draw_rule" not in data["settings"]
    data["settings"]["draw_rule"] = {"rule": "uniform"}
    with pytest.raises(ManifestError, match="unknown \\['draw_rule'\\]"):
        Manifest.from_json(data)


def test_the_default_course_caps_expert_holes_at_one_a_nine():
    assert Settings().constraints is None
    assert Settings().experts_per_nine == DEFAULT_EXPERTS_PER_NINE == 1
    assert Settings(par=70).rules == standard_rules(70, 1)
    assert [rule.to_json() for rule in standard_rules(70, 1)] == [
        {"rule": "standard", "par": 70},
        {"rule": "expert_cap", "per_nine": 1},
    ]
    assert [rule.name for rule in standard_rules(72, None)] == ["standard"]


def test_the_expert_cap_is_read_from_the_rules():
    def cap(*rules: dict) -> int | None:
        return Settings(constraints=parse_rules(list(rules))).experts_per_nine

    assert cap({"rule": "total_par", "max": 70}) is None
    assert cap({"rule": "expert_cap", "per_nine": 3}) == 3
    assert (
        cap(
            {"rule": "expert_cap", "per_nine": 3}, {"rule": "expert_cap", "per_nine": 2}
        )
        == 2
    )


@pytest.mark.parametrize("schema", [3, 4])
@pytest.mark.parametrize(
    "cap, stored",
    [
        (None, {"rule": "uniform"}),
        (0, {"rule": "expert_cap", "per_nine": 0}),
        (1, {"rule": "expert_cap", "per_nine": 1}),
        (9, {"rule": "expert_cap", "per_nine": 9}),
    ],
)
def test_a_schema_with_a_draw_rule_loads_it_as_rules_and_keeps_its_shape(
    schema, cap, stored
):
    settings = Settings(prng_seed="abc", par=71, constraints=standard_rules(71, cap))
    data = as_schema(manifest(settings=settings).to_json(), schema)
    assert data["settings"]["draw_rule"] == stored
    assert "constraints" not in data["settings"]
    loaded = Manifest.from_json(data)
    assert loaded.settings.experts_per_nine == cap
    assert loaded.settings.rules == standard_rules(71, cap)
    assert loaded.to_json() == data
    assert list(loaded.to_json()["settings"]) == list(data["settings"])


def test_a_stored_cap_of_one_loads_as_the_default_course():
    data = as_schema(manifest().to_json(), 4)
    assert data["settings"]["draw_rule"] == {"rule": "expert_cap", "per_nine": 1}
    assert Manifest.from_json(data).settings == manifest().settings


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
    stored = as_schema(manifest().to_json(), 4)
    stored["settings"]["draw_rule"] = data
    with pytest.raises(ManifestError):
        Manifest.from_json(stored)


def test_standard_rules_refuse_a_par_or_a_cap_no_course_has():
    with pytest.raises(ManifestError, match="par must be one of"):
        standard_rules(73)
    with pytest.raises(ManifestError, match="per_nine"):
        standard_rules(72, 10)


@pytest.mark.parametrize("value", [None, 0, -1, True, "2"])
def test_schema_two_requires_a_positive_integer_build_version(value):
    data = manifest().to_json()
    data["build_version"] = value
    with pytest.raises(ManifestError, match="build_version"):
        Manifest.from_json(data)


def test_schema_two_requires_the_build_version_field():
    data = manifest().to_json()
    del data["build_version"]
    with pytest.raises(ManifestError, match="missing fields.*build_version"):
        Manifest.from_json(data)


@pytest.mark.parametrize("value", [None, 0, -1, True, "1"])
def test_schema_two_requires_a_positive_integer_finish_abi_version(value):
    data = manifest().to_json()
    data["finish_abi_version"] = value
    with pytest.raises(ManifestError, match="finish_abi_version"):
        Manifest.from_json(data)


def test_schema_two_requires_the_finish_abi_version_field():
    data = manifest().to_json()
    del data["finish_abi_version"]
    with pytest.raises(ManifestError, match="missing fields.*finish_abi_version"):
        Manifest.from_json(data)


def test_rejects_other_schemas_before_reading_fields():
    data = manifest().to_json() | {"schema": SCHEMA + 1, "something_new": 1}
    with pytest.raises(ManifestError, match=f"schema {SCHEMA + 1}"):
        Manifest.from_json(data)


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


def test_schema_three_loads_with_a_vanilla_pool_and_keeps_its_shape():
    data = as_schema(manifest().to_json(), 3)
    assert "include" not in data["settings"]
    loaded = Manifest.from_json(data)
    assert loaded.schema == 3
    assert loaded.settings.include == frozenset()
    assert loaded.settings == manifest().settings
    assert loaded.to_json() == data


@pytest.mark.parametrize("schema", [1, 2, 3])
def test_a_schema_before_include_refuses_one(schema):
    settings = Settings(
        prng_seed="abc",
        constraints=standard_rules(72, None),
        include=frozenset({"derived"}),
    )
    with pytest.raises(ManifestError, match="has no include"):
        manifest(schema=schema, build_version=1, settings=settings)
    data = as_schema(manifest(settings=UNIFORM_SETTINGS).to_json(), schema)
    data["settings"]["include"] = []
    with pytest.raises(ManifestError, match="unknown"):
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


# -- Constraints ------------------------------------------------------------------------------


def test_constraints_are_checked_rules_and_round_trip():
    rules = [
        {"rule": "par_counts", "counts": {"3": 18}},
        {"rule": "expert_cap", "per_nine": 0},
    ]
    value = manifest(settings=Settings(prng_seed="abc", constraints=parse_rules(rules)))
    assert value.settings.constraints == tuple(Rule.parse(rule) for rule in rules)
    data = json.loads(json.dumps(value.to_json()))
    assert data["settings"]["constraints"] == rules
    assert Manifest.from_json(data) == value
    assert hash(value.settings) == hash(Manifest.from_json(data).settings)
    assert manifest().to_json()["settings"]["constraints"] is None


@pytest.mark.parametrize(
    "constraints", [[{"rule": "shuffle"}], [{"par": 3}], ["standard"], "standard"]
)
def test_a_rule_the_vocabulary_does_not_have_is_refused(constraints):
    with pytest.raises(ManifestError, match="constraints"):
        Settings(prng_seed="abc", constraints=constraints)


def test_schema_four_loads_as_a_standard_course_and_keeps_its_shape():
    data = as_schema(manifest().to_json(), 4)
    assert "constraints" not in data["settings"]
    loaded = Manifest.from_json(data)
    assert loaded.schema == 4 and loaded.settings.constraints is None
    assert loaded.to_json() == data


def test_a_schema_before_constraints_refuses_them():
    data = as_schema(manifest().to_json(), 4)
    data["settings"]["constraints"] = None
    with pytest.raises(ManifestError, match="unknown"):
        Manifest.from_json(data)
    with pytest.raises(ManifestError, match="has no constraints"):
        manifest(
            schema=4,
            settings=Settings(
                prng_seed="abc", constraints=parse_rules([{"rule": "standard"}])
            ),
        )
