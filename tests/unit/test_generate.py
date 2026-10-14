"""Unit tests for generating a manifest from a catalog, curation and settings."""

import json
import re
from itertools import pairwise

import pytest

from golf.core.patches.seeded_wind import derive_hole_seeds
from golf.core.patches.sram_defaults import Club, magic_bytes
from golf.randomizer.build import BUILD_VERSION, FINISH_ABI_VERSION
from golf.randomizer.catalog import (
    JP_ROM,
    US_ROM,
    Catalog,
    CatalogEntry,
    HoleId,
    RomSource,
)
from golf.randomizer.curation import EXPERT_TAG, CurationSnapshot
from golf.randomizer.generate import GENERATOR_VERSION, GenerationError, generate
from golf.randomizer.manifest import (
    SCHEMA,
    ClubRules,
    Manifest,
    Settings,
    mode_rules,
    required_roms,
    standard_rules,
)
from golf.randomizer.pool import build_pool
from golf.randomizer.wind import BANDS, DIRECTION_PROFILES, SPEED_PROFILES
from tests.standard_course import is_standard


@pytest.fixture(scope="module")
def real_catalog() -> Catalog:
    return Catalog.load()


@pytest.fixture(scope="module")
def real_curation() -> CurationSnapshot:
    return CurationSnapshot.load()


def entry(hole_id: str, par: int, rom: str = US_ROM) -> CatalogEntry:
    return CatalogEntry(
        HoleId.parse(hole_id), RomSource(rom, "course", 1), "0" * 64, par, 400, "Test"
    )


def minimal_catalog(par3: int = 4, par4: int = 10, par5: int = 4, extra=()) -> Catalog:
    entries = [
        *(entry(f"t/three_{n}", 3) for n in range(par3)),
        *(entry(f"t/four_{n}", 4) for n in range(par4)),
        *(entry(f"t/five_{n}", 5) for n in range(par5)),
        *extra,
    ]
    return Catalog(1, {item.id: item for item in entries})


def test_same_inputs_give_the_same_manifest(real_catalog, real_curation):
    settings = Settings(prng_seed="league-week-1")
    first = generate(real_catalog, real_curation, settings)
    assert generate(real_catalog, real_curation, settings) == first
    assert (
        generate(real_catalog, real_curation, Settings(prng_seed="league-week-2"))
        != first
    )


def test_draws_a_prng_seed_when_the_settings_have_none(real_catalog, real_curation):
    drawn = generate(real_catalog, real_curation, Settings())
    assert drawn.settings.prng_seed is not None
    assert re.fullmatch(r"[0-9a-f]{16}", drawn.settings.prng_seed)
    assert generate(real_catalog, real_curation, drawn.settings) == drawn


def test_records_versions_and_round_trips(real_catalog, real_curation):
    manifest = generate(real_catalog, real_curation, Settings(prng_seed="abc"))
    assert manifest.schema == SCHEMA == 5
    assert manifest.generator_version == GENERATOR_VERSION
    assert manifest.build_version == BUILD_VERSION == 6
    assert manifest.finish_abi_version == FINISH_ABI_VERSION == 2
    assert manifest.catalog_version == real_catalog.version
    assert manifest.curation_stamp == real_curation.stamp
    assert Manifest.from_json(json.loads(json.dumps(manifest.to_json()))) == manifest


#: the course pars each mode's seeds may have
MODE_PARS = {
    "standard": {72},
    "natural_par": set(range(68, 77)),
    "executive": set(range(56, 61)),
    "par_3": {54},
}


def no_two_in_a_row(layout, par: int) -> bool:
    return not any(one == other == par for one, other in pairwise(layout))


@pytest.mark.parametrize("mode", sorted(MODE_PARS))
def test_each_mode_gives_courses_of_its_shape(real_catalog, real_curation, mode):
    pars = set()
    for seed in range(12):
        settings = Settings(
            prng_seed=f"{mode}-{seed}",
            include=frozenset({"derived"}),
            constraints=mode_rules(mode, 1),
        )
        course = generate(real_catalog, real_curation, settings).course
        assert course.par in MODE_PARS[mode]
        assert all(real_catalog[slot.id].par == slot.par for slot in course.holes)
        layout = course.layout
        if mode == "standard":
            assert is_standard(layout)
        if mode == "natural_par":
            assert no_two_in_a_row(layout, 3) and no_two_in_a_row(layout, 5)
            assert layout[-1] != 3
        if mode == "executive":
            assert 5 not in layout
            assert abs(layout[:9].count(4) - layout[9:].count(4)) <= 1
        pars.add(course.par)
    if mode in ("natural_par", "executive"):
        assert len(pars) > 1, "a rolled mode draws its mix per seed"


def test_transform_chances_roll_each_hole_on_its_own(real_catalog, real_curation):
    def holes(**chances):
        settings = Settings(prng_seed="transforms", **chances)
        return generate(real_catalog, real_curation, settings).course.holes

    plain = holes()
    assert all(slot.transforms == () for slot in plain)
    every = holes(mirror_chance=100, hazard_chance=100)
    assert [slot.id for slot in every] == [slot.id for slot in plain]
    for slot in every:
        assert slot.transforms[0] == "mirror@1"
        assert slot.transforms[1].startswith("hazards@1:")
    seeds = {slot.transforms[1] for slot in every}
    assert len(seeds) == 18

    def mirrored(slots) -> list[bool]:
        return ["mirror@1" in slot.transforms for slot in slots]

    half = holes(mirror_chance=50)
    assert 0 < sum(mirrored(half)) < 18
    assert mirrored(holes(mirror_chance=50, hazard_chance=100)) == mirrored(half)
    assert all(
        "mirror@1" in many.transforms
        for few, many in zip(half, holes(mirror_chance=75), strict=True)
        if "mirror@1" in few.transforms
    )


def test_copies_settings_into_the_course(real_catalog, real_curation):
    clubs = ClubRules(max=12, banned=frozenset({Club.W1}))
    settings = Settings(prng_seed="abc", mercy_point=None, clubs=clubs, music="jp_uk")
    course = generate(real_catalog, real_curation, settings).course
    assert (course.mercy_point, course.clubs, course.music) == (None, clubs, "jp_uk")


def test_wind_seeds_come_from_the_prng_seed(real_catalog, real_curation):
    course = generate(real_catalog, real_curation, Settings(prng_seed="abc")).course
    assert [slot.wind_seed for slot in course.holes] == derive_hole_seeds("abc")


def test_each_draw_has_its_own_stream(real_catalog, real_curation):
    both = generate(real_catalog, real_curation, Settings(prng_seed="abc")).course
    us_only = generate(
        real_catalog,
        real_curation,
        Settings(prng_seed="abc", sources=frozenset({US_ROM})),
    ).course
    assert both.magic_words == us_only.magic_words
    assert both.sram_magic == us_only.sram_magic
    assert [s.wind_seed for s in both.holes] == [s.wind_seed for s in us_only.holes]


def test_sram_magic_is_drawn_per_seed_and_never_holds_a_blank_sram_byte(
    real_catalog, real_curation
):
    magics = [
        generate(
            real_catalog, real_curation, Settings(prng_seed=str(seed))
        ).course.sram_magic
        for seed in range(40)
    ]
    for magic in magics:
        assert all(byte not in (0x00, 0xFF) for byte in magic_bytes(magic))
    assert len(set(magics)) > 1


def test_nes_open_seeds_use_nes_open_music(real_catalog, real_curation):
    for seed in range(40):
        manifest = generate(
            real_catalog,
            real_curation,
            Settings(prng_seed=str(seed), sources=frozenset({US_ROM})),
        )
        assert manifest.course.music.startswith("nes_")
        assert required_roms(manifest, real_catalog) == (US_ROM,)


def test_mario_open_seeds_can_use_mario_open_music(real_catalog, real_curation):
    music = {
        generate(
            real_catalog, real_curation, Settings(prng_seed=str(seed))
        ).course.music
        for seed in range(60)
    }
    assert any(slug.startswith("jp_") for slug in music)


def test_never_repeats_a_family():
    twins = [entry("t/twin_a", 4), entry("t/twin_b", 4, JP_ROM)]
    holes = minimal_catalog(par4=9, extra=twins)
    curation = CurationSnapshot.from_json(
        {"t/twin_a": {"family": "twin"}, "t/twin_b": {"family": "twin"}}
    )
    for seed in range(30):
        ids = {
            str(slot.id)
            for slot in generate(
                holes, curation, Settings(prng_seed=str(seed))
            ).course.holes
        }
        assert len(ids & {"t/twin_a", "t/twin_b"}) == 1


def test_a_real_seed_never_repeats_a_curated_family(real_catalog, real_curation):
    """The invariant over the live curation, whose families span both vanilla ROMs.

    The synthetic cases above pin the rule; this pins that the rule still bites on the
    data the CLI and the site actually generate from.
    """
    pool = build_pool(real_catalog, real_curation, Settings())
    shared = [family for family in pool.families if len(family.members) > 1]
    assert shared, (
        "no family has two drawable members, so this test would prove nothing"
    )

    label_of = {
        lineage: label
        for label, members in real_curation.families().items()
        for lineage in members
    }
    for seed in range(40):
        holes = generate(
            real_catalog, real_curation, Settings(prng_seed=str(seed))
        ).course.holes
        drawn = [
            label_of[slot.id.lineage] for slot in holes if slot.id.lineage in label_of
        ]
        assert len(drawn) == len(set(drawn)), f"seed {seed} repeated a family: {drawn}"


def test_allowing_repeats_can_use_both_twins():
    twins = [entry("t/twin_a", 4), entry("t/twin_b", 4, JP_ROM)]
    holes = minimal_catalog(par4=8, extra=twins)
    curation = CurationSnapshot.from_json(
        {"t/twin_a": {"family": "twin"}, "t/twin_b": {"family": "twin"}}
    )
    course = generate(
        holes, curation, Settings(prng_seed="abc", allow_family_repeats=True)
    ).course
    assert {"t/twin_a", "t/twin_b"} <= {str(slot.id) for slot in course.holes}


def test_spends_a_mixed_family_where_it_is_the_only_fit():
    # Three par 3 families and four par 5 families, plus one family with a par 3 and a
    # par 5 member: the course is only fillable if that family takes a par 3 slot.
    mixed = [entry("t/mixed_short", 3), entry("t/mixed_long", 5)]
    holes = minimal_catalog(par3=3, extra=mixed)
    curation = CurationSnapshot.from_json(
        {"t/mixed_short": {"family": "m"}, "t/mixed_long": {"family": "m"}}
    )
    for seed in range(30):
        ids = {
            str(slot.id)
            for slot in generate(
                holes, curation, Settings(prng_seed=str(seed))
            ).course.holes
        }
        assert "t/mixed_short" in ids and "t/mixed_long" not in ids


def test_reports_a_pool_the_family_rule_leaves_too_small():
    twins = [entry("t/twin_a", 3), entry("t/twin_b", 3)]
    holes = minimal_catalog(par3=2, extra=twins)
    curation = CurationSnapshot.from_json(
        {"t/twin_a": {"family": "twin"}, "t/twin_b": {"family": "twin"}}
    )
    with pytest.raises(
        GenerationError, match=r"(?s)18 holes in 17 families.*distinct_families"
    ):
        generate(holes, curation, Settings(prng_seed="abc"))


def test_reports_a_par_the_pool_is_short_of():
    with pytest.raises(GenerationError) as raised:
        generate(
            minimal_catalog(par3=3, par4=14),
            CurationSnapshot.from_json({}),
            Settings(prng_seed="abc"),
        )
    message = str(raised.value)
    assert '"rule":"standard"' in message
    assert 'the pool has 3 holes matching {"par": 3}, in 3 families' in message
    assert "distinct_families" not in message


def test_reports_filters_that_empty_the_pool(real_catalog, real_curation):
    with pytest.raises(GenerationError, match="14 holes.*cannot fill 18 slots"):
        generate(minimal_catalog(par5=0), real_curation, Settings(prng_seed="abc"))
    with pytest.raises(GenerationError, match="the pool is empty"):
        generate(Catalog(1, {}), real_curation, Settings(prng_seed="abc"))


# -- Draw rules -------------------------------------------------------------------------------


def experts_by_nine(manifest: Manifest, curation: CurationSnapshot) -> tuple[int, int]:
    expert = [
        EXPERT_TAG in curation.for_hole(slot.id).tags for slot in manifest.course.holes
    ]
    return sum(expert[:9]), sum(expert[9:])


def expert_catalog(experts: int = 6) -> tuple[Catalog, CurationSnapshot]:
    """Enough ordinary holes for any layout, plus `experts` par 4 expert holes."""
    extra = [entry(f"t/expert_{n}", 4, JP_ROM) for n in range(experts)]
    catalog = minimal_catalog(par3=5, par4=12, par5=5, extra=extra)
    curation = CurationSnapshot.from_json(
        {str(item.id.lineage): {"tags": [EXPERT_TAG]} for item in extra}
    )
    return catalog, curation


def test_the_real_curation_tags_expert_holes(real_catalog, real_curation):
    pool = build_pool(real_catalog, real_curation, Settings())
    assert len(pool.experts) == 21
    assert all(hole_id.lineage.startswith("jp_") for hole_id in pool.experts)


@pytest.mark.parametrize("per_nine", [0, 1, 2])
def test_an_expert_cap_holds_on_each_nine_of_real_seeds(
    real_catalog, real_curation, per_nine
):
    most = 0
    for seed in range(60):
        manifest = generate(
            real_catalog,
            real_curation,
            Settings(prng_seed=str(seed), constraints=standard_rules(per_nine)),
        )
        nines = experts_by_nine(manifest, real_curation)
        assert max(nines) <= per_nine, f"seed {seed}: {nines}"
        assert is_standard(manifest.course.layout)
        assert len({slot.id for slot in manifest.course.holes}) == 18
        most = max(most, *nines)
    assert most == per_nine, "the cap was never reached, so it was never tested"


def test_a_uniform_draw_goes_over_the_cap_on_real_seeds(real_catalog, real_curation):
    """What the cap is for: without it, real seeds put several expert holes on a nine."""
    worst = max(
        max(
            experts_by_nine(
                generate(
                    real_catalog,
                    real_curation,
                    Settings(prng_seed=str(seed), constraints=standard_rules(None)),
                ),
                real_curation,
            )
        )
        for seed in range(40)
    )
    assert worst > 1


def test_a_capped_seed_never_repeats_a_family(real_catalog, real_curation):
    label_of = {
        lineage: label
        for label, members in real_curation.families().items()
        for lineage in members
    }
    for seed in range(40):
        holes = generate(
            real_catalog,
            real_curation,
            Settings(prng_seed=str(seed), constraints=standard_rules(1)),
        ).course.holes
        drawn = [
            label_of[slot.id.lineage] for slot in holes if slot.id.lineage in label_of
        ]
        assert len(drawn) == len(set(drawn)), f"seed {seed} repeated a family: {drawn}"


def test_a_capped_draw_is_the_same_for_the_same_seed():
    catalog, curation = expert_catalog()
    settings = Settings(prng_seed="abc", constraints=standard_rules(1))
    assert generate(catalog, curation, settings) == generate(
        catalog, curation, settings
    )


def test_expert_holes_land_on_every_slot_of_a_nine():
    """Filling the slots in play order would gather a nine's expert holes at its start."""
    catalog, curation = expert_catalog()
    seen = set()
    for seed in range(300):
        manifest = generate(
            catalog,
            curation,
            Settings(prng_seed=str(seed), constraints=standard_rules(1)),
        )
        seen |= {
            number
            for number, slot in enumerate(manifest.course.holes)
            if EXPERT_TAG in curation.for_hole(slot.id).tags
        }
    par_fours = set()
    for seed in range(300):
        layout = generate(
            catalog, curation, Settings(prng_seed=str(seed))
        ).course.layout
        par_fours |= {number for number, par in enumerate(layout) if par == 4}
    assert seen == par_fours
    assert {number % 9 for number in seen} == set(range(9))


def test_a_cap_of_zero_leaves_a_mixed_family_its_other_member():
    """An expert hole's twin can still be drawn from the family they share."""
    twins = [entry("t/twin_expert", 4, JP_ROM), entry("t/twin_plain", 4)]
    catalog = minimal_catalog(par4=9, extra=twins)
    curation = CurationSnapshot.from_json(
        {
            "t/twin_expert": {"family": "twin", "tags": [EXPERT_TAG]},
            "t/twin_plain": {"family": "twin"},
        }
    )
    drawn = set()
    for seed in range(30):
        course = generate(
            catalog,
            curation,
            Settings(prng_seed=str(seed), constraints=standard_rules(0)),
        ).course
        drawn |= {str(slot.id) for slot in course.holes}
    assert "t/twin_plain" in drawn and "t/twin_expert" not in drawn


def test_a_capped_draw_uses_the_expert_holes_the_pool_needs():
    """Five ordinary par 4s and six expert ones: a course needs five of the experts."""
    extra = [entry(f"t/expert_{n}", 4, JP_ROM) for n in range(6)]
    catalog = minimal_catalog(par4=5, extra=extra)
    curation = CurationSnapshot.from_json(
        {str(item.id.lineage): {"tags": [EXPERT_TAG]} for item in extra}
    )
    for cap in (None, 9, 3):
        manifest = generate(
            catalog,
            curation,
            Settings(prng_seed="abc", constraints=standard_rules(cap)),
        )
        assert sum(experts_by_nine(manifest, curation)) >= 5
    # four expert holes at most, and thirteen others: seventeen holes for eighteen slots
    with pytest.raises(GenerationError, match=r"(?s)expert_cap.*13 holes other than"):
        generate(
            catalog,
            curation,
            Settings(prng_seed="abc", constraints=standard_rules(2)),
        )


# -- Wind profiles ----------------------------------------------------------------------------


def test_vanilla_profiles_give_each_hole_the_wind_its_seed_deals(
    real_catalog, real_curation
):
    course = generate(real_catalog, real_curation, Settings(prng_seed="abc")).course
    assert all(slot.wind == slot.dealt_wind for slot in course.holes)


def test_wind_profiles_set_the_anchors_and_nothing_else(real_catalog, real_curation):
    plain = generate(real_catalog, real_curation, Settings(prng_seed="abc"))
    windy = generate(
        real_catalog,
        real_curation,
        Settings(
            prng_seed="abc",
            wind_speed_profile="back_nine_pressure",
            wind_direction_profile="headwind_out",
        ),
    )
    assert [slot.wind for slot in windy.course.holes] != [
        slot.wind for slot in plain.course.holes
    ]
    for number, (slot, before) in enumerate(
        zip(windy.course.holes, plain.course.holes, strict=True)
    ):
        assert (slot.id, slot.par, slot.wind_seed) == (
            before.id,
            before.par,
            before.wind_seed,
        )
        direction, speed = slot.wind
        assert speed in (BANDS["gentle"] if number < 9 else BANDS["strong"])
        center = 0x80 if number < 9 else 0x00
        assert ((direction - center) // 0x10 + 8) % 16 - 8 in range(-2, 3)
    assert (windy.course.music, windy.course.magic_words) == (
        plain.course.music,
        plain.course.magic_words,
    )
    assert Manifest.from_json(json.loads(json.dumps(windy.to_json()))) == windy


def test_the_two_wind_profiles_draw_from_their_own_streams(real_catalog, real_curation):
    def holes(**profiles):
        settings = Settings(prng_seed="abc", **profiles)
        return generate(real_catalog, real_curation, settings).course.holes

    both = holes(wind_speed_profile="strong", wind_direction_profile="prevailing")
    speed_only = holes(wind_speed_profile="strong")
    direction_only = holes(wind_direction_profile="prevailing")
    assert [slot.wind_speed for slot in both] == [
        slot.wind_speed for slot in speed_only
    ]
    assert [slot.wind_direction for slot in both] == [
        slot.wind_direction for slot in direction_only
    ]
    # a vanilla part keeps the seed's own anchor beside a profiled one
    assert all(slot.wind_direction == slot.dealt_wind[0] for slot in speed_only)
    assert all(slot.wind_speed == slot.dealt_wind[1] for slot in direction_only)


@pytest.mark.parametrize("speed", SPEED_PROFILES)
@pytest.mark.parametrize("direction", DIRECTION_PROFILES)
def test_every_pair_of_wind_profiles_generates(
    real_catalog, real_curation, speed, direction
):
    settings = Settings(
        prng_seed="abc", wind_speed_profile=speed, wind_direction_profile=direction
    )
    manifest = generate(real_catalog, real_curation, settings)
    assert manifest.settings.wind_speed_profile == speed
    assert manifest.settings.wind_direction_profile == direction
