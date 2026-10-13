"""Unit tests for the course rule vocabulary: shapes, scopes and expansion."""

import pytest

from golf.randomizer.rules import (
    HOLES,
    RULES,
    Rule,
    RuleError,
    bounds,
    check_where,
    expand,
    parse_rules,
    scopes,
)

#: one well-formed example of every rule
EXAMPLES = {
    "count": {"rule": "count", "where": {"par": 3}, "scope": "front", "max": 2},
    "sum": {"rule": "sum", "of": "distance", "min": 7000},
    "compare": {
        "rule": "compare",
        "of": {"sum": "over_par"},
        "left": "back",
        "right": "front",
        "min": 0,
    },
    "limit": {"rule": "limit", "per": "course", "min": 2, "max": 3},
    "step": {"rule": "step", "of": "distance", "min": 1},
    "gap": {"rule": "gap", "of": "distance", "min": 40},
    "weight": {"rule": "weight", "where": {"rom": "mario_open_jp"}, "factor": 2},
    "par_counts": {"rule": "par_counts", "counts": {"3": 18}},
    "balanced_nines": {"rule": "balanced_nines"},
    "no_consecutive": {"rule": "no_consecutive", "pars": [3, 5]},
    "total_par": {"rule": "total_par", "min": 70, "max": 72},
    "standard": {"rule": "standard", "par": 71},
    "expert_cap": {"rule": "expert_cap", "per_nine": 1},
    "distinct_families": {"rule": "distinct_families"},
    "slot": {"rule": "slot", "hole": [9, 18], "where": {"par": 5}},
    "pin": {"rule": "pin", "hole": 1, "id": "nes_us/01"},
    "vanilla_course": {"rule": "vanilla_course", "course": "jp_uk"},
    "highest": {"rule": "highest", "of": "over_par", "hole": 18},
    "lowest": {"rule": "lowest", "of": "distance", "hole": 1, "among": "front"},
    "exclude": {"rule": "exclude", "where": {"tag": "expert"}, "scope": "back"},
    "require": {"rule": "require", "where": {"id": "nes_us/18"}},
}
GENERAL = {"count", "sum", "compare", "limit", "step", "gap", "weight"}


def test_there_is_an_example_of_every_rule():
    assert set(EXAMPLES) == set(RULES)


@pytest.mark.parametrize("name", sorted(EXAMPLES))
def test_every_rule_parses_and_expands_to_general_rules(name):
    rule = Rule.parse(EXAMPLES[name])
    assert rule.name == name
    assert rule.to_json() == EXAMPLES[name]
    expanded = rule.expand()
    assert expanded
    assert {general["rule"] for general in expanded} <= GENERAL
    for general in expanded:
        Rule.parse(general)
    assert (RULES[name].expand is None) == (name in GENERAL)


def test_a_rule_is_the_same_however_its_keys_are_ordered():
    one = Rule.parse({"rule": "count", "where": {"par": 3}, "max": 2})
    other = Rule.parse({"max": 2, "where": {"par": 3}, "rule": "count"})
    assert one == other and hash(one) == hash(other)
    assert Rule.parse(one) is one
    assert parse_rules([one, EXAMPLES["sum"]]) == (one, Rule.parse(EXAMPLES["sum"]))


@pytest.mark.parametrize(
    "data, message",
    [
        ("standard", "an object"),
        ({"par": 72}, "an object"),
        ({"rule": "shuffle"}, "unknown rule"),
        ({"rule": "standard", "par": 69}, "standard par"),
        ({"rule": "standard", "pars": 72}, "unknown"),
        ({"rule": "count", "where": {"par": 3}}, "needs min and/or max"),
        ({"rule": "count", "where": {"par": 3}, "min": 3, "max": 2}, "over max"),
        ({"rule": "count", "where": {"par": 3}, "exactly": 2, "max": 3}, "exactly"),
        ({"rule": "count", "where": {"pars": 3}, "max": 1}, "unknown selector field"),
        ({"rule": "count", "where": {"par": "3"}, "max": 1}, "bad par"),
        ({"rule": "count", "where": {"par": 3}, "max": 1.5}, "must be a number"),
        ({"rule": "count", "where": {"distance": {}}, "max": 1}, "needs a min"),
        ({"rule": "sum", "of": "handicap", "max": 1}, "expected one of"),
        ({"rule": "limit", "per": "tag"}, "limit per"),
        ({"rule": "limit", "per": "course", "max": 0}, "at least 1"),
        ({"rule": "gap", "of": "distance", "min": 0}, "positive"),
        ({"rule": "weight", "where": {"par": 3}, "factor": 0}, "positive"),
        ({"rule": "no_consecutive"}, "pars or where"),
        ({"rule": "expert_cap", "per_nine": 10}, "per_nine"),
        ({"rule": "slot", "hole": 19, "where": {"par": 5}}, "hole number"),
        ({"rule": "compare", "left": "front", "right": "back", "min": 0}, "no measure"),
        (
            {
                "rule": "compare",
                "of": {"sum": "par"},
                "left": "each_nine",
                "right": "back",
                "min": 0,
            },
            "single group",
        ),
        ({"rule": "par_counts", "counts": {"three": 4}}, "a par is a number"),
        ({"rule": "pin", "hole": 1, "id": 7}, "pin id"),
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
    assert scopes("each_hole") == [(slot,) for slot in range(18)]
    assert scopes([9, 18, 1]) == [(8, 17, 0)]
    assert scopes({"from": 16, "to": 18}) == [(15, 16, 17)]
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


def test_selectors_nest_under_not():
    check_where({"par": [3, 4], "not": {"tag": "expert", "not": {"rom": "x"}}})
    with pytest.raises(RuleError, match="not"):
        check_where({"not": {"speed": 3}})


def test_standard_is_the_three_rules_the_layouts_were_built_on():
    general = expand({"rule": "standard", "par": 70})
    counts = {
        rule["where"]["par"]: rule["exactly"]
        for rule in general
        if rule["rule"] == "count" and "exactly" in rule
    }
    assert counts == {3: 4, 4: 12, 5: 2}
    assert sum(rule["rule"] == "compare" for rule in general) == 3
    windows = [rule for rule in general if rule.get("scope") == {"window": 2}]
    assert [rule["where"]["par"] for rule in windows] == [3, 5]
    assert all(rule["max"] == 1 for rule in windows)


def test_a_vanilla_course_is_a_pin_in_every_slot():
    general = expand({"rule": "vanilla_course", "course": "nes_us", "holes": "front"})
    assert general == [
        {
            "rule": "count",
            "where": {"course": "nes_us", "kind": "vanilla", "original_hole": "slot"},
            "scope": [hole],
            "exactly": 1,
        }
        for hole in range(1, 10)
    ]


def test_highest_is_a_step_from_every_other_hole():
    general = expand({"rule": "highest", "of": "over_par", "hole": 18})
    assert len(general) == HOLES - 1
    assert general[0] == {"rule": "step", "of": "over_par", "scope": [1, 18], "min": 0}
    lowest = expand({"rule": "lowest", "of": "over_par", "hole": 1, "among": "front"})
    assert len(lowest) == 8 and lowest[0]["max"] == 0
