"""Unit tests for composing a course from a pool and rules.

Every course mode is drawn and then judged by a check written out here by hand, with
none of the solver's code in it, so a rule that compiles to the wrong thing fails
against what its mode says in plain Python.
"""

import random
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import replace

import pytest

from golf.randomizer.catalog import (
    JP_ROM,
    US_ROM,
    Catalog,
    CatalogEntry,
    HoleId,
    RomSource,
)
from golf.randomizer.compose import GenerationError, Problem, compose
from golf.randomizer.curation import EXPERT_TAG, CurationSnapshot
from golf.randomizer.generate import course_rules
from golf.randomizer.layout import COUNTS, satisfies
from golf.randomizer.manifest import Settings, standard_rules
from golf.randomizer.pool import build_pool
from golf.randomizer.request import settings_from_request

STANDARD = {"rule": "standard"}
FAMILIES = {"rule": "distinct_families"}
COURSES = (
    "nes_us",
    "nes_japan",
    "nes_uk",
    "jp_japan",
    "jp_australia",
    "jp_france",
    "jp_hawaii",
    "jp_uk",
)


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load()


@pytest.fixture(scope="module")
def curation() -> CurationSnapshot:
    return CurationSnapshot.load()


def problem_for(request: dict, catalog, curation) -> Problem:
    settings = settings_from_request(request)
    return Problem(build_pool(catalog, curation, settings), course_rules(settings))


def problem_of(rules: Sequence[Mapping], catalog, curation) -> Problem:
    """Rules a seed's settings may not hold, such as the building blocks the modes are
    written in, over the default pool and with the family rule."""
    return Problem(build_pool(catalog, curation, Settings()), [*rules, FAMILIES])


# -- What each mode promises, in plain Python --------------------------------------------------


class Card:
    """A drawn course, with the handful of facts the checks below read."""

    def __init__(self, holes, curation: CurationSnapshot):
        self.holes = list(holes)
        self.pars = [hole.par for hole in holes]
        self.ids = [str(hole.id) for hole in holes]
        self.expert = [EXPERT_TAG in curation.for_hole(hole.id).tags for hole in holes]

    @property
    def standard(self) -> bool:
        return satisfies(self.pars, COUNTS[72])


def vanilla(course: str):
    return lambda card: card.ids == [f"{course}/{hole:02d}" for hole in range(1, 19)]


def capped(rules: list[dict], cap: int | None) -> list[dict]:
    return rules if cap is None else [*rules, {"rule": "expert_cap", "per_nine": cap}]


#: each mode's request, and what a course of it must be
MODES = {
    **{
        f"standard_cap_{cap}": (
            {"constraints": capped([{"rule": "standard", "par": 72}], cap)},
            Card.standard.fget,
        )
        for cap in (0, 1, 2, None)
    },
    "par_3": (
        {"include": ["derived"], "constraints": capped([{"rule": "par_3"}], 1)},
        lambda c: c.pars == [3] * 18,
    ),
    **{
        f"vanilla_{course}": (
            {
                "sources": [US_ROM if course.startswith("nes_") else JP_ROM],
                "constraints": [{"rule": "vanilla_course", "course": course}],
            },
            vanilla(course),
        )
        for course in COURSES
    },
}


@pytest.mark.parametrize("name", sorted(MODES))
def test_a_mode_draws_the_course_it_describes(name, catalog, curation):
    request, check = MODES[name]
    settings = settings_from_request(request)
    pool = build_pool(catalog, curation, settings)
    problem = Problem(pool, course_rules(settings))
    family_of = {
        member.id: family.key for family in pool.families for member in family.members
    }
    for seed in range(6):
        holes = problem.draw(random.Random(seed), mix=100)
        card = Card(holes, curation)
        assert check(card), f"seed {seed}: {card.ids}"
        assert len(set(card.ids)) == 18
        assert len({family_of[hole.id] for hole in holes}) == 18
        cap = settings.experts_per_nine
        if cap is not None:
            assert sum(card.expert[:9]) <= cap
            assert sum(card.expert[9:]) <= cap
        assert problem.violations(holes) == []
        assert problem.draw(random.Random(seed), mix=100) == holes


# -- Judging and explaining --------------------------------------------------------------------


def test_violations_name_the_rules_a_course_breaks(catalog, curation):
    pool = build_pool(catalog, curation, Settings())
    standard = compose(pool, [STANDARD, FAMILIES], random.Random(1))
    par_threes = Problem(pool, [{"rule": "par_counts", "counts": {"3": 18}}, FAMILIES])
    assert par_threes.violations(standard) == [
        '{"counts":{"3":18},"rule":"par_counts"}'
    ]
    assert par_threes.violations(standard[:17]) == ["a course has 18 holes, got 17"]
    assert Problem(pool, [FAMILIES]).violations([standard[0]] * 18) == [
        '{"rule":"distinct_families"}'
    ]
    assert "a hole is on the course once" in Problem(pool, []).violations(
        [standard[0]] * 18
    )


def explanation(request: dict, catalog, curation) -> str:
    return explained(problem_for(request, catalog, curation))


def explained(problem: Problem) -> str:
    with pytest.raises(GenerationError) as raised:
        problem.draw(random.Random(0))
    return str(raised.value)


def test_a_rule_the_pool_cannot_meet_is_named_with_what_the_pool_has(catalog, curation):
    message = explanation(
        {
            "sources": [US_ROM],
            "constraints": [{"rule": "par_3"}],
        },
        catalog,
        curation,
    )
    assert (
        "the pool (54 holes in 54 families) has no course that keeps this rule"
        in message
    )
    assert '{"rule":"par_3"}' in message
    assert 'the pool has 12 holes matching {"par": 3}, in 12 families' in message
    assert "distinct_families" not in message and "expert_cap" not in message


def test_a_pin_outside_the_pool_is_named(catalog, curation):
    message = explanation(
        {
            "sources": [US_ROM],
            "constraints": [{"rule": "vanilla_course", "course": "jp_uk"}],
        },
        catalog,
        curation,
    )
    assert '{"course":"jp_uk","rule":"vanilla_course"}' in message
    assert "the pool has 0 holes matching" in message


def test_rules_that_only_clash_together_are_named_together(catalog, curation):
    # Mario Open's Hawaii course has more than one expert hole on a nine
    message = explanation(
        {
            "constraints": [
                {"rule": "vanilla_course", "course": "jp_hawaii"},
                {"rule": "expert_cap", "per_nine": 1},
            ]
        },
        catalog,
        curation,
    )
    assert "has no course that keeps these rules together" in message
    assert "vanilla_course" in message and "expert_cap" in message
    assert "distinct_families" not in message

    message = explained(
        problem_of(
            [STANDARD, {"rule": "slot", "hole": [9, 10], "where": {"par": 5}}],
            catalog,
            curation,
        )
    )
    assert '"rule":"standard"' in message and '"rule":"slot"' in message
    assert "distinct_families" not in message


@pytest.mark.parametrize(
    "rule",
    [
        {"rule": "count", "where": {"tag": "expert"}, "min": 17},
        {"rule": "count", "where": {"par": 5}, "scope": "front", "min": 4},
        {"rule": "count", "where": {"par": 4}, "exactly": 11},
    ],
)
def test_a_rule_past_what_a_standard_course_can_give_is_proved_so(
    rule, catalog, curation
):
    message = explained(problem_of([STANDARD, rule], catalog, curation))
    assert "gave up" not in message
    assert f'"rule":"{rule["rule"]}"' in message


def test_an_empty_pool_says_so():
    with pytest.raises(GenerationError, match="the pool is empty"):
        compose(
            build_pool(Catalog(1, {}), CurationSnapshot(), Settings()),
            [],
            random.Random(0),
        )


# -- Synthetic pools ---------------------------------------------------------------------------


def entry(hole_id: str, par: int, distance: int = 400, hole: int = 1) -> CatalogEntry:
    return CatalogEntry(
        HoleId.parse(hole_id),
        RomSource(US_ROM, "course", hole),
        "0" * 64,
        par,
        distance,
        "Test",
    )


def pool_of(entries, families: dict[str, str] | None = None, **settings):
    catalog = Catalog(1, {item.id: item for item in entries})
    curation = CurationSnapshot.from_json(
        {lineage: {"family": family} for lineage, family in (families or {}).items()}
    )
    return build_pool(catalog, curation, Settings(**settings))


def test_a_short_shares_a_family_with_its_base():
    """Twelve par 3s, and eight par 5s that each have a par 3 short in their family."""
    entries = [entry(f"t/three_{n}", 3, 180) for n in range(12)]
    entries += [entry(f"t/four_{n}", 4) for n in range(12)]
    families = {}
    for n in range(8):
        entries += [entry(f"t/five_{n}", 5, 550), entry(f"t/five_{n}_short3", 3, 200)]
        families[f"t/five_{n}"] = families[f"t/five_{n}_short3"] = f"five_{n}"
    pool = pool_of(entries, families)
    par_threes = Problem(pool, [{"rule": "par_counts", "counts": {"3": 18}}, FAMILIES])
    standard = Problem(pool, [STANDARD, FAMILIES])
    for seed in range(20):
        holes = par_threes.draw(random.Random(seed))
        assert [hole.par for hole in holes] == [3] * 18
        assert sum("short3" in hole.id.lineage for hole in holes) >= 6
        ids = {hole.id.lineage for hole in standard.draw(random.Random(seed))}
        for n in range(8):
            assert not {f"t/five_{n}", f"t/five_{n}_short3"} <= ids
    # without the shorts there are twelve par 3 families for eighteen slots
    bare = pool_of([item for item in entries if "short3" not in item.id.lineage])
    with pytest.raises(GenerationError, match="12 holes matching"):
        compose(
            bare,
            [{"rule": "par_counts", "counts": {"3": 18}}, FAMILIES],
            random.Random(0),
        )


# -- Likelihood --------------------------------------------------------------------------------


def shares(problem: Problem, draws: int) -> Counter:
    seen: Counter = Counter()
    for seed in range(draws):
        seen.update(str(hole.id) for hole in problem.draw(random.Random(seed)))
    return Counter({hole: count / draws for hole, count in seen.items()})


def test_holes_no_rule_tells_apart_are_equally_likely():
    """Twelve of each par for a standard course: a par 3 is on a third of the courses."""
    entries = [entry(f"t/par{par}_{n}", par) for par in (3, 4, 5) for n in range(12)]
    seen = shares(Problem(pool_of(entries), [STANDARD, FAMILIES]), 400)
    for par, expected in ((3, 4 / 12), (4, 10 / 12), (5, 4 / 12)):
        for n in range(12):
            assert abs(seen[f"t/par{par}_{n}"] - expected) < 0.13


def test_a_hole_with_a_twin_is_no_likelier_than_one_without():
    entries = [entry(f"t/three_{n}", 3) for n in range(8)]
    entries += [entry(f"t/five_{n}", 5) for n in range(8)]
    entries += [entry(f"t/four_{n}", 4) for n in range(12)]
    entries += [entry("t/twin_a", 4), entry("t/twin_b", 4)]
    pool = pool_of(entries, {"t/twin_a": "twin", "t/twin_b": "twin"})
    seen = shares(Problem(pool, [STANDARD, FAMILIES]), 400)
    expected = 10 / 13  # ten par 4 slots, thirteen par 4 families
    assert abs(seen["t/twin_a"] + seen["t/twin_b"] - expected) < 0.1
    assert abs(seen["t/twin_a"] - expected / 2) < 0.12
    assert abs(seen["t/four_0"] - expected) < 0.1


# -- Mixing ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["standard_cap_0", "standard_cap_1", "par_3"])
def test_a_mixed_course_keeps_every_rule(name, catalog, curation):
    request, check = MODES[name]
    settings = settings_from_request(request)
    pool = build_pool(catalog, curation, settings)
    problem = Problem(pool, course_rules(settings))
    for seed in range(3):
        found = problem.picks(random.Random(seed))
        mixed = problem.mix(found, random.Random(seed), 400)
        assert problem.allows(mixed)
        assert mixed != found
        holes = [problem.candidates[value].entry for value in mixed]
        assert check(Card(holes, curation))
        assert problem.mix(found, random.Random(seed), 400) == mixed
    found = problem.picks(random.Random(9))
    assert problem.mix(found, random.Random(0), 0) == found
    assert problem.draw(random.Random(5), mix=200) == problem.draw(
        random.Random(5), mix=200
    )


def test_mixing_leaves_a_course_with_one_answer_alone(catalog, curation):
    settings = settings_from_request(MODES["vanilla_nes_uk"][0])
    problem = Problem(build_pool(catalog, curation, settings), course_rules(settings))
    found = problem.picks(random.Random(0))
    assert problem.mix(found, random.Random(0), 300) == found


def test_mixing_takes_out_the_lean_toward_a_cap(catalog, curation):
    """Under a cap of one expert hole a nine, the search fills both nines' caps on
    about 73% of courses. Among all the courses that keep the rules it is 51%."""
    problem = problem_for({}, catalog, curation)

    def at_the_cap(mix: int) -> float:
        full = 0
        for seed in range(150):
            holes = problem.draw(random.Random(seed), mix)
            full += sum(hole.id in problem.pool.experts for hole in holes) == 2
        return full / 150

    assert at_the_cap(0) > 0.63
    assert at_the_cap(300) < 0.61


def test_a_mixed_twin_is_no_likelier_than_a_hole_without_one():
    entries = [entry(f"t/three_{n}", 3) for n in range(8)]
    entries += [entry(f"t/five_{n}", 5) for n in range(8)]
    entries += [entry(f"t/four_{n}", 4) for n in range(12)]
    entries += [entry("t/twin_a", 4), entry("t/twin_b", 4)]
    pool = pool_of(entries, {"t/twin_a": "twin", "t/twin_b": "twin"})
    problem = Problem(pool, [STANDARD, FAMILIES])
    seen: Counter = Counter()
    for seed in range(400):
        seen.update(str(hole.id) for hole in problem.draw(random.Random(seed), 200))
    expected = 10 / 13  # ten par 4 slots, thirteen par 4 families
    assert abs((seen["t/twin_a"] + seen["t/twin_b"]) / 400 - expected) < 0.1
    assert abs(seen["t/twin_a"] / 400 - expected / 2) < 0.12
    assert abs(seen["t/four_0"] / 400 - expected) < 0.1


def test_only_the_family_rule_is_added_to_the_rules_settings_hold():
    settings = settings_from_request({"constraints": [{"rule": "par_3"}]})
    assert [rule.name for rule in course_rules(settings)] == [
        "par_3",
        "distinct_families",
    ]
    assert settings.experts_per_nine is None
    uncapped = Settings(constraints=standard_rules(72, None))
    assert [rule.name for rule in course_rules(uncapped)] == [
        "standard",
        "distinct_families",
    ]


def test_the_default_course_is_a_standard_one_with_the_default_expert_cap():
    assert [rule.name for rule in course_rules(Settings(par=71))] == [
        "standard",
        "expert_cap",
        "distinct_families",
    ]
    assert Settings(par=71).rules == standard_rules(71, 1)
    assert Settings().experts_per_nine == 1


# -- Proofs the search does not have to find -----------------------------------------------------


@pytest.mark.parametrize(
    "rules, named",
    [
        # two rules about one count that leave it no value
        (
            [
                {"rule": "count", "where": {"par": 3}, "max": 3},
                {"rule": "count", "where": {"par": 3}, "min": 5},
            ],
            ['"max":3', '"min":5'],
        ),
        # ten par 4s, no two of them in a row: nine is the most eighteen holes take
        (
            [STANDARD, {"rule": "no_consecutive", "pars": [4]}],
            ["standard", "no_consecutive"],
        ),
    ],
)
def test_rules_that_cannot_hold_together_are_refused_at_once(
    rules, named, catalog, curation
):
    problem = problem_of(rules, catalog, curation)
    before = problem.solver.dead_ends
    with pytest.raises(GenerationError) as raised:
        problem.draw(random.Random(0))
    message = str(raised.value)
    assert "has no course that keeps these rules together" in message
    assert all(text in message for text in named)
    assert "expert_cap" not in message and "distinct_families" not in message
    assert problem.solver.dead_ends == before  # refused before any choice was made


def test_giving_up_says_so_and_which_rules_a_course_is_found_without(
    catalog, curation, monkeypatch
):
    """A search cut short before it finds the course there is."""
    monkeypatch.setattr("golf.randomizer.compose.WORK_LIMIT", 1)
    problem = problem_for({}, catalog, curation)
    with pytest.raises(GenerationError) as raised:
        problem.draw(random.Random(0))
    message = str(raised.value)
    assert "the search gave up before proving there is none" in message
    assert "a course is found when any one of these is dropped" in message
    assert '"rule":"standard"' in message.split("is dropped:")[1]


def test_a_par_no_vanilla_hole_has_is_one_more_number(catalog, curation):
    """The six longest holes as par 6s (docs/thoughts_on_par_6.md), which the catalog
    does not have: the rules need nothing new to place them."""
    monsters = {
        "jp_uk/18",
        "jp_uk/14",
        "jp_hawaii/05",
        "jp_uk/09",
        "jp_hawaii/14",
        "jp_hawaii/18",
    }
    reparred = Catalog(
        1,
        {
            item.id: replace(item, par=6) if str(item.id) in monsters else item
            for item in catalog
        },
    )
    long = {"par": [5, 6]}
    rules = [
        {"rule": "par_counts", "counts": {"3": 4, "4": 10}},
        {"rule": "count", "where": long, "exactly": 4},
        {"rule": "count", "where": {"par": 6}, "max": 1},
        {"rule": "no_consecutive", "pars": [3]},
        {"rule": "no_consecutive", "where": long},
        FAMILIES,
    ]
    problem = Problem(build_pool(reparred, curation, Settings()), rules)
    totals = Counter()
    for seed in range(40):
        pars = [hole.par for hole in problem.draw(random.Random(seed))]
        assert pars.count(6) <= 1 and pars.count(5) + pars.count(6) == 4
        totals[sum(pars)] += 1
    assert set(totals) == {72, 73}
