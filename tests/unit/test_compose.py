"""Unit tests for composing a course from a pool and rules.

Every checked-in preset is drawn and then judged by a check written out here by hand,
with none of the solver's code in it, so a rule that compiles to the wrong thing fails
against what its preset says in plain Python.
"""

import random
from collections import Counter
from dataclasses import replace
from itertools import pairwise

import pytest

from golf.randomizer.catalog import (
    JP_ROM,
    US_ROM,
    Catalog,
    CatalogEntry,
    HoleId,
    RomSource,
)
from golf.randomizer.compose import GenerationError, Problem, compose, load_over_par
from golf.randomizer.curation import EXPERT_TAG, CurationSnapshot
from golf.randomizer.generate import course_rules
from golf.randomizer.layout import COUNTS, satisfies
from golf.randomizer.manifest import Settings, standard_rules
from golf.randomizer.pool import build_pool
from golf.randomizer.request import load_request, presets, settings_from_request

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


# -- What each preset promises, in plain Python ------------------------------------------------


class Card:
    """A drawn course, with the handful of facts the checks below read."""

    def __init__(self, holes, curation: CurationSnapshot):
        self.holes = list(holes)
        self.pars = [hole.par for hole in holes]
        self.yards = [hole.distance for hole in holes]
        self.ids = [str(hole.id) for hole in holes]
        self.roms = [hole.rom for hole in holes]
        self.courses = [hole.id.lineage.split("/")[0] for hole in holes]
        self.numbers = [hole.source.hole for hole in holes]
        self.expert = [EXPERT_TAG in curation.for_hole(hole.id).tags for hole in holes]
        scores = load_over_par()
        self.over = [scores[hole.id.lineage] for hole in holes]

    @property
    def standard(self) -> bool:
        return satisfies(self.pars, COUNTS[72])

    def run(self, matches) -> int:
        """The longest run of holes in a row that match."""
        longest = current = 0
        for hole in range(18):
            current = current + 1 if matches(hole) else 0
            longest = max(longest, current)
        return longest


def vanilla(course: str):
    return lambda card: card.ids == [f"{course}/{hole:02d}" for hole in range(1, 19)]


CHECKS = {
    **{f"vanilla_{course}": vanilla(course) for course in COURSES},
    "par_3_course": lambda c: c.pars == [3] * 18,
    "executive": lambda c: (
        Counter(c.pars) == {3: 14, 4: 4}
        and c.pars[:9].count(4) == 2
        and c.run(lambda hole: c.pars[hole] == 4) == 1
    ),
    "executive_mixed": lambda c: (
        5 not in c.pars
        and 8 <= c.pars.count(3) <= 12
        and 60 <= sum(c.pars) <= 64
        and abs(c.pars[:9].count(3) - c.pars[9:].count(3)) <= 1
        and c.run(lambda hole: c.pars[hole] == 3) <= 3
    ),
    "finishing_fives": lambda c: (
        c.standard and c.pars[8] == c.pars[17] == 5 and c.pars[0] != 5
    ),
    "world_tour": lambda c: (
        c.standard
        and set(Counter(c.courses)) == set(COURSES)
        and set(Counter(c.courses).values()) <= {2, 3}
        and all(one != other for one, other in pairwise(c.courses))
    ),
    "in_position": lambda c: c.standard and c.numbers == list(range(1, 19)),
    "compensating_nines": lambda c: (
        c.standard and sum(c.over[:9]) <= 3.0 + 1e-6 and sum(c.over[9:]) <= 3.0 + 1e-6
    ),
    "tips": lambda c: c.standard and sum(c.yards) >= 7900,
    "forward_tees": lambda c: c.standard and sum(c.yards) <= 6950,
    "ladder": lambda c: (
        Counter(c.pars) == {3: 4, 4: 10, 5: 4}
        and all(one < other for one, other in pairwise(c.yards))
    ),
    "change_of_pace": lambda c: (
        c.standard and all(abs(one - other) >= 60 for one, other in pairwise(c.yards))
    ),
    "nes_out_mario_in": lambda c: (
        c.standard and set(c.roms[:9]) == {US_ROM} and set(c.roms[9:]) == {JP_ROM}
    ),
    "alternating_roms": lambda c: (
        c.standard and all(one != other for one, other in pairwise(c.roms))
    ),
    "vanilla_front_nine": lambda c: (
        c.standard and c.ids[:9] == [f"nes_us/{hole:02d}" for hole in range(1, 10)]
    ),
    "expert_gauntlet": lambda c: c.standard and sum(c.expert) >= 12,
    "shape_by_distance": lambda c: (
        sum(yards <= 260 for yards in c.yards) == 4
        and sum(yards >= 481 for yards in c.yards) == 4
        and c.run(lambda hole: c.yards[hole] <= 260) == 1
        and c.run(lambda hole: c.yards[hole] >= 481) == 1
        and sum(yards >= 750 for yards in c.yards) <= 1
    ),
    "grand_finale": lambda c: (
        c.standard
        and c.pars[17] == 5
        and c.over[17] == max(c.over)
        and c.over[0] == min(c.over)
    ),
    "mario_heavy": lambda c: c.standard,
    "short_front": lambda c: (
        c.pars[:9] == [3] * 9
        and Counter(c.pars[9:]) == {3: 2, 4: 5, 5: 2}
        and c.run(lambda hole: c.pars[hole] == 5) == 1
    ),
    "palindrome": lambda c: c.standard and c.pars == c.pars[::-1],
    "even_nines": lambda c: (
        c.standard
        and abs(sum(c.yards[:9]) - sum(c.yards[9:])) <= 50
        and abs(sum(c.over[:9]) - sum(c.over[9:])) <= 0.25 + 1e-6
    ),
    "signature_holes": lambda c: c.standard and {"nes_us/18", "jp_uk/18"} <= set(c.ids),
    "harder_coming_home": lambda c: (
        c.standard and sum(c.over[9:]) - sum(c.over[:9]) >= 2 - 1e-6
    ),
    "natural_par": lambda c: (
        68 <= sum(c.pars) <= 76
        and c.run(lambda hole: c.pars[hole] == 3) == 1
        and c.run(lambda hole: c.pars[hole] == 5) == 1
    ),
    "long_short_holes": lambda c: (
        c.standard
        and sum(yards for yards, par in zip(c.yards, c.pars, strict=True) if par == 3)
        >= 900
    ),
    "big_ones": lambda c: (
        c.standard
        and sum(
            expert or yards >= 600
            for expert, yards in zip(c.expert, c.yards, strict=True)
        )
        >= 6
    ),
    "running_order": lambda c: (
        c.standard and all(one <= other for one, other in pairwise(c.numbers))
    ),
}


def test_every_preset_has_a_check():
    assert set(CHECKS) == set(presets())


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_a_preset_draws_the_course_it_describes(name, catalog, curation):
    settings = load_request(presets()[name])
    pool = build_pool(catalog, curation, settings)
    problem = Problem(pool, course_rules(settings))
    family_of = {
        member.id: family.key for family in pool.families for member in family.members
    }
    for seed in range(6):
        holes = problem.draw(random.Random(seed))
        card = Card(holes, curation)
        assert CHECKS[name](card), f"seed {seed}: {card.ids}"
        assert len(set(card.ids)) == 18
        assert len({family_of[hole.id] for hole in holes}) == 18
        cap = settings.experts_per_nine
        if cap is not None:
            assert sum(card.expert[:9]) <= cap
            assert sum(card.expert[9:]) <= cap
        assert problem.violations(holes) == []
        assert problem.draw(random.Random(seed)) == holes


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
    with pytest.raises(GenerationError) as raised:
        problem_for(request, catalog, curation).draw(random.Random(0))
    return str(raised.value)


def test_a_rule_the_pool_cannot_meet_is_named_with_what_the_pool_has(catalog, curation):
    message = explanation(
        {
            "sources": [US_ROM],
            "constraints": [{"rule": "par_counts", "counts": {"3": 18}}],
        },
        catalog,
        curation,
    )
    assert (
        "the pool (54 holes in 54 families) has no course that keeps this rule"
        in message
    )
    assert '{"counts":{"3":18},"rule":"par_counts"}' in message
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

    message = explanation(
        {
            "constraints": [
                STANDARD,
                {"rule": "slot", "hole": [9, 10], "where": {"par": 5}},
            ]
        },
        catalog,
        curation,
    )
    assert '"rule":"standard"' in message and '"rule":"slot"' in message
    assert "expert_cap" not in message

    # twins, pinned side by side: only the family rule objects
    message = explanation(
        {
            "constraints": [
                STANDARD,
                {"rule": "pin", "hole": 1, "id": "nes_uk/01"},
                {"rule": "pin", "hole": 2, "id": "jp_japan/01"},
            ]
        },
        catalog,
        curation,
    )
    assert "distinct_families" in message and message.count('"rule":"pin"') == 2
    assert '"rule":"standard"' not in message


@pytest.mark.parametrize(
    "rule",
    [
        {"rule": "sum", "of": "distance", "min": 9300},
        {"rule": "sum", "of": "distance", "max": 6000},
        {"rule": "require", "where": {"tag": "expert"}, "min": 17},
        {"rule": "step", "of": "distance", "min": 1},
        {"rule": "limit", "per": "course", "min": 3},
    ],
)
def test_a_rule_past_what_a_standard_course_can_give_is_proved_so(
    rule, catalog, curation
):
    message = explanation(
        {"constraints": [STANDARD, rule]},
        catalog,
        curation,
    )
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


def test_a_number_a_hole_lacks_keeps_it_out_of_a_sum():
    # no hole of a made-up pool has a predicted score
    pool = pool_of([entry(f"t/hole_{n}", 4) for n in range(20)])
    problem = Problem(pool, [{"rule": "sum", "of": "over_par", "max": 100}])
    with pytest.raises(GenerationError, match="20 holes of the pool have no over_par"):
        problem.draw(random.Random(0))
    scores = {f"t/hole_{n}": 0.1 * n for n in range(19)}
    scored = Problem(pool, [{"rule": "sum", "of": "over_par", "max": 100}], scores)
    assert "t/hole_19" not in {str(hole.id) for hole in scored.draw(random.Random(0))}


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


def test_an_implication_is_a_comparison_of_two_counts(catalog, curation):
    """If the 9th is a par 5, the 10th is a par 3: one count no greater than the other."""
    rule = {
        "rule": "compare",
        "left": {"scope": [9], "of": {"count": {"par": 5}}},
        "right": {"scope": [10], "of": {"count": {"par": 3}}},
        "max": 0,
    }
    problem = problem_for({"constraints": [STANDARD, rule]}, catalog, curation)
    fives = 0
    for seed in range(60):
        pars = [hole.par for hole in problem.draw(random.Random(seed))]
        if pars[8] == 5:
            fives += 1
            assert pars[9] == 3
    assert 0 < fives < 60


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


def test_a_weight_moves_how_often_holes_are_picked(catalog, curation):
    def mario_holes(request: dict) -> float:
        problem = problem_for(request, catalog, curation)
        return (
            sum(
                sum(hole.rom == JP_ROM for hole in problem.draw(random.Random(seed)))
                for seed in range(60)
            )
            / 60
        )

    plain = mario_holes({"constraints": [STANDARD]})
    heavy = mario_holes(
        {
            "constraints": [
                STANDARD,
                {"rule": "weight", "where": {"rom": JP_ROM}, "factor": 4},
            ]
        }
    )
    light = mario_holes(
        {
            "constraints": [
                STANDARD,
                {"rule": "weight", "where": {"rom": JP_ROM}, "factor": 0.25},
            ]
        }
    )
    assert light + 2 < plain < heavy - 2


# -- Mixing ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["grand_finale", "world_tour", "tips", "palindrome", "executive"]
)
def test_a_mixed_course_keeps_every_rule(name, catalog, curation):
    settings = load_request(presets()[name])
    pool = build_pool(catalog, curation, settings)
    problem = Problem(pool, course_rules(settings))
    for seed in range(3):
        found = problem.picks(random.Random(seed))
        mixed = problem.mix(found, random.Random(seed), 400)
        assert problem.allows(mixed)
        assert mixed != found
        holes = [problem.candidates[value].entry for value in mixed]
        assert CHECKS[name](Card(holes, curation))
        assert problem.mix(found, random.Random(seed), 400) == mixed
    found = problem.picks(random.Random(9))
    assert problem.mix(found, random.Random(0), 0) == found
    assert problem.draw(random.Random(5), mix=200) == problem.draw(
        random.Random(5), mix=200
    )


def test_mixing_leaves_a_course_with_one_answer_alone(catalog, curation):
    settings = load_request(presets()["vanilla_nes_uk"])
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


def test_a_weight_carries_through_mixing(catalog, curation):
    def mario_holes(request: dict) -> float:
        problem = problem_for(request, catalog, curation)
        return (
            sum(
                sum(
                    hole.rom == JP_ROM
                    for hole in problem.draw(random.Random(seed), 300)
                )
                for seed in range(60)
            )
            / 60
        )

    plain = mario_holes({"constraints": [STANDARD]})
    heavy = mario_holes(
        {
            "constraints": [
                STANDARD,
                {"rule": "weight", "where": {"rom": JP_ROM}, "factor": 4},
            ]
        }
    )
    assert heavy > plain + 2


def test_only_the_family_rule_is_added_to_the_rules_settings_hold():
    settings = settings_from_request(
        {"constraints": [{"rule": "total_par", "max": 80}]}
    )
    assert [rule.name for rule in course_rules(settings)] == [
        "total_par",
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
        # two rules about one sum that leave it no value
        (
            [
                {"rule": "sum", "of": "distance", "max": 6700},
                {"rule": "sum", "of": "distance", "min": 8000},
            ],
            ['"max":6700', '"min":8000'],
        ),
        # ten par 4s, no two of them in a row: nine is the most eighteen holes take
        (
            [STANDARD, {"rule": "no_consecutive", "pars": [4]}],
            ["standard", "no_consecutive"],
        ),
        # no two neighbors of one par at all
        (
            [STANDARD, {"rule": "limit", "per": "par", "scope": {"window": 2}}],
            ["standard", '"per":"par"'],
        ),
    ],
)
def test_rules_that_cannot_hold_together_are_refused_at_once(
    rules, named, catalog, curation
):
    problem = problem_for({"constraints": rules}, catalog, curation)
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
    """A request in the far tail of what the pool gives, with the search cut short."""
    monkeypatch.setattr("golf.randomizer.compose.WORK_LIMIT", 3000)
    request = {
        "constraints": [
            {"rule": "standard", "par": 70},
            {"rule": "limit", "per": "course", "min": 2, "max": 5},
            {"rule": "sum", "of": "distance", "min": 8080},
        ],
    }
    problem = problem_for(request, catalog, curation)
    with pytest.raises(GenerationError) as raised:
        problem.draw(random.Random(0))
    message = str(raised.value)
    assert "the search gave up before proving there is none" in message
    assert "a course is found when any one of these is dropped" in message
    dropped = message.split("is dropped:")[1]
    assert '"rule":"sum"' in dropped and "distinct_families" not in dropped


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
