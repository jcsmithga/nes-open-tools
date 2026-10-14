"""Unit tests for the course rule vocabulary: shapes, scopes, expansion and rolling."""

import random
from collections import Counter

import pytest

from golf.randomizer.rules import (
    RULES,
    Rule,
    RuleError,
    bounds,
    check_public,
    check_where,
    expand,
    is_rolled,
    natural_mixes,
    parse_rules,
    roll,
    scopes,
)

#: one well-formed example of every rule
EXAMPLES = {
    "count": {"rule": "count", "where": {"par": 3}, "scope": "front", "max": 2},
    "compare": {
        "rule": "compare",
        "of": {"count": {"par": 3}},
        "left": "back",
        "right": "front",
        "min": 0,
    },
    "limit": {"rule": "limit", "per": "family", "max": 2},
    "par_counts": {"rule": "par_counts", "counts": {"3": 14, "4": {"min": 2}}},
    "balanced_nines": {"rule": "balanced_nines"},
    "no_consecutive": {"rule": "no_consecutive", "pars": [3, 5]},
    "standard": {"rule": "standard"},
    "natural_par": {"rule": "natural_par"},
    "executive": {"rule": "executive"},
    "par_3": {"rule": "par_3"},
    "expert_cap": {"rule": "expert_cap", "per_nine": 1},
    "distinct_families": {"rule": "distinct_families"},
    "slot": {"rule": "slot", "hole": [9, 18], "where": {"par": 5}},
    "vanilla_course": {"rule": "vanilla_course", "course": "jp_uk"},
}
GENERAL = {"count", "compare", "limit"}
#: the rules a seed's settings may hold (ADR 0025)
PUBLIC = {
    "standard",
    "natural_par",
    "executive",
    "par_3",
    "vanilla_course",
    "expert_cap",
}
ROLLED = {"natural_par", "executive"}


def test_there_is_an_example_of_every_rule():
    assert set(EXAMPLES) == set(RULES)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_every_rule_parses_and_expands_to_general_rules(name):
    rule = Rule.parse(EXAMPLES[name])
    assert rule.name == name
    assert rule.to_json() == EXAMPLES[name]
    assert is_rolled(rule) == (name in ROLLED)
    if name in ROLLED:
        with pytest.raises(RuleError, match="rolled before it is expanded"):
            rule.expand()
        (rule,) = [r for r in roll([rule], random.Random(0)) if r.name == "par_counts"]
    expanded = rule.expand()
    assert expanded
    assert {general["rule"] for general in expanded} <= GENERAL
    for general in expanded:
        Rule.parse(general)
    assert (RULES[name].expand is None) == (name in GENERAL | ROLLED)


def test_a_rule_is_the_same_however_its_keys_are_ordered():
    one = Rule.parse({"rule": "count", "where": {"par": 3}, "max": 2})
    other = Rule.parse({"max": 2, "where": {"par": 3}, "rule": "count"})
    assert one == other and hash(one) == hash(other)
    assert Rule.parse(one) is one
    assert parse_rules([one, EXAMPLES["par_3"]]) == (
        one,
        Rule.parse(EXAMPLES["par_3"]),
    )


def test_only_the_modes_and_the_expert_cap_are_public():
    assert {name for name, kind in RULES.items() if kind.public} == PUBLIC
    for name in sorted(PUBLIC - {"expert_cap"}):
        check_public(parse_rules([EXAMPLES[name]]))
    for name in sorted(set(EXAMPLES) - PUBLIC):
        with pytest.raises(RuleError, match="not a rule settings may hold"):
            check_public(parse_rules([EXAMPLES["standard"], EXAMPLES[name]]))


@pytest.mark.parametrize(
    "data, message",
    [
        ("standard", "an object"),
        ({"par": 72}, "an object"),
        ({"rule": "shuffle"}, "unknown rule"),
        ({"rule": "standard", "par": 72}, "unknown"),
        ({"rule": "natural_par", "min": 68}, "unknown"),
        ({"rule": "count", "where": {"par": 3}}, "needs min and/or max"),
        ({"rule": "count", "where": {"par": 3}, "min": 3, "max": 2}, "over max"),
        ({"rule": "count", "where": {"par": 3}, "exactly": 2, "max": 3}, "exactly"),
        ({"rule": "count", "where": {"pars": 3}, "max": 1}, "unknown selector field"),
        ({"rule": "count", "where": {"par": "3"}, "max": 1}, "bad par"),
        ({"rule": "count", "where": {"par": 3}, "max": 1.5}, "must be a whole number"),
        ({"rule": "count", "where": {"distance": {"min": 1}}, "max": 1}, "unknown"),
        ({"rule": "limit", "per": "course"}, "limit per"),
        ({"rule": "limit", "per": "family", "max": 0}, "1 or more"),
        ({"rule": "par_3", "par": 3}, "unknown"),
        ({"rule": "vanilla_course", "course": "nes_us", "holes": "front"}, "unknown"),
        ({"rule": "no_consecutive"}, "pars or where"),
        ({"rule": "expert_cap", "per_nine": 10}, "per_nine"),
        ({"rule": "slot", "hole": 19, "where": {"par": 5}}, "hole number"),
        ({"rule": "compare", "left": "front", "right": "back", "min": 0}, "missing"),
        (
            {
                "rule": "compare",
                "of": {"sum": "par"},
                "left": "front",
                "right": "back",
                "min": 0,
            },
            "count",
        ),
        (
            {
                "rule": "compare",
                "of": {"count": {"par": 3}},
                "left": "each_nine",
                "right": "back",
                "min": 0,
            },
            "single group",
        ),
        ({"rule": "par_counts", "counts": {"three": 4}}, "a par is a number"),
    ],
)
def test_a_malformed_rule_is_refused(data, message):
    with pytest.raises(RuleError, match=message):
        Rule.parse(data)


def test_scopes_name_groups_of_slots_from_zero():
    assert scopes("course") == [tuple(range(18))]
    assert scopes("front") == [tuple(range(9))]
    assert scopes("back") == [tuple(range(9, 18))]
    assert scopes("each_nine") == scopes("front") + scopes("back")
    assert scopes([9, 18, 1]) == [(8, 17, 0)]
    windows = scopes({"window": 3})
    assert (
        len(windows) == 16 and windows[0] == (0, 1, 2) and windows[-1] == (15, 16, 17)
    )


@pytest.mark.parametrize(
    "spec",
    [
        "nine",
        [],
        [0],
        [1, 1],
        [19],
        {"window": 1},
        {"window": 19},
        "each_hole",
        {"from": 5, "to": 2},
        7,
    ],
)
def test_a_malformed_scope_is_refused(spec):
    with pytest.raises(RuleError):
        scopes(spec)


def test_bounds_read_min_max_and_exactly():
    assert bounds({"exactly": 4}) == (4, 4)
    assert bounds({"min": 2}) == (2, float("inf"))
    assert bounds({"max": 2}) == (float("-inf"), 2)


def test_a_selector_reads_par_tag_course_kind_and_original_hole():
    check_where(
        {
            "par": [3, 4],
            "tag": "expert",
            "course": "nes_us",
            "kind": "vanilla",
            "original_hole": "slot",
        }
    )
    for field in ("rom", "id", "distance", "not", "any"):
        with pytest.raises(RuleError, match="unknown selector field"):
            check_where({field: 3})


def test_standard_is_the_three_rules_the_layouts_were_built_on():
    general = expand({"rule": "standard"})
    counts = {
        rule["where"]["par"]: rule["exactly"]
        for rule in general
        if rule["rule"] == "count" and "exactly" in rule
    }
    assert counts == {3: 4, 4: 10, 5: 4}
    assert sum(rule["rule"] == "compare" for rule in general) == 3
    windows = [rule for rule in general if rule.get("scope") == {"window": 2}]
    assert [rule["where"]["par"] for rule in windows] == [3, 5]
    assert all(rule["max"] == 1 for rule in windows)
    assert {"rule": "count", "where": {"par": 3}, "scope": [18], "max": 0} in general


def test_a_par_3_course_is_eighteen_par_3s():
    assert expand({"rule": "par_3"}) == [
        {"rule": "count", "where": {"par": 3}, "exactly": 18}
    ]


def test_a_vanilla_course_is_its_own_hole_in_every_slot():
    general = expand({"rule": "vanilla_course", "course": "nes_us"})
    assert general == [
        {
            "rule": "count",
            "where": {"course": "nes_us", "kind": "vanilla", "original_hole": "slot"},
            "scope": [hole],
            "exactly": 1,
        }
        for hole in range(1, 19)
    ]


def mix_of(rules) -> dict[int, int]:
    (counts,) = [
        rule.to_json()["counts"] for rule in rules if rule.name == "par_counts"
    ]
    return {int(par): n for par, n in counts.items() if n}


def test_rolling_replaces_only_the_rolled_rules_in_place():
    cap = Rule.parse({"rule": "expert_cap", "per_nine": 1})
    rolled = roll([Rule.parse({"rule": "natural_par"}), cap], random.Random(3))
    assert [rule.name for rule in rolled] == [
        "par_counts",
        "no_consecutive",
        "count",
        "expert_cap",
    ]
    standard = parse_rules([{"rule": "standard"}, cap])
    assert roll(standard, random.Random(3)) == standard
    again = roll([Rule.parse({"rule": "natural_par"}), cap], random.Random(3))
    assert again == rolled


def test_natural_par_draws_each_mix_by_its_weight():
    """The odds ADR 0025 states: par 72 on 20.3% of seeds, 68 and 76 on 2.7% each."""
    mixes = natural_mixes()
    total = sum(weight for _, weight in mixes)
    by_par: dict[int, float] = {}
    for counts, weight in mixes:
        assert sum(counts.values()) == 18
        par = sum(par * n for par, n in counts.items())
        by_par[par] = by_par.get(par, 0.0) + weight / total
    assert set(by_par) == set(range(68, 77))
    assert round(by_par[72], 3) == 0.203 and round(by_par[68], 3) == 0.027
    assert by_par[68] == pytest.approx(by_par[76])
    drawn = Counter()
    rng = random.Random(0)
    for _ in range(4000):
        mix = mix_of(roll([Rule.parse({"rule": "natural_par"})], rng))
        drawn[sum(par * n for par, n in mix.items())] += 1
    assert abs(drawn[72] / 4000 - by_par[72]) < 0.02


def test_executive_draws_two_to_six_par_fours_and_no_fives():
    rng = random.Random(0)
    fours = Counter()
    for _ in range(500):
        mix = mix_of(roll([Rule.parse({"rule": "executive"})], rng))
        assert 5 not in mix and mix[3] + mix[4] == 18
        fours[mix[4]] += 1
    assert set(fours) == {2, 3, 4, 5, 6}
    assert min(fours.values()) > 60
