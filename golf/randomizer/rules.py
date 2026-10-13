"""
Course rules: what a seed's settings may say about the eighteen holes it is given.

A rule is a JSON object with a `rule` name and that rule's arguments. A course satisfies a
list of rules when it satisfies every one: rules only ever add, so a list is stricter than
any part of it. This module is the vocabulary and nothing else. It checks a rule's shape
and expands the convenient rules into the few general ones; `golf/randomizer/compose.py`
turns those into a problem for `golf/randomizer/solver.py`, against a pool.

**General rules**, which the others are written in:

| Rule | Holds when |
|---|---|
| `count` | the number of holes in a scope that match `where` is within `min`/`max` |
| `sum` | the total of a number (`par`, `distance`, `over_par`, `original_hole`) over a scope is within `min`/`max`; with `where`, over the holes of the scope that match |
| `compare` | one scope's count or sum (`left`), less another's (`right`), is within `min`/`max`. A side is a scope, measured by the rule's `of`, or `{"scope": .., "of": ..}` with a measure of its own |
| `limit` | no more than `max` holes of a scope share a `per` (a family, a course, ...), and with `min`, each value in the pool has at least that many |
| `step` | a number changes by `min` to `max` from each hole of a scope to the next |
| `gap` | a number differs by at least `min` between neighbors in a scope |
| `weight` | (not a rule a course can break) holes that match are `factor` times as likely to be picked |

**A selector** (`where`) describes holes, and a hole matches when every field does:
`par`, `tag`, `rom`, `course`, `kind`, `family`, `id`, `author` (a value or a list of
them), `distance` and `over_par` (`{"min": .., "max": ..}`), `original_hole` (a number, a
list, or `"slot"` for the number of the slot the hole is being put in), `not` (a
selector that must not match) and `any` (a list of selectors, one of which must).

**A scope** says which holes of the course a rule looks at. Some scopes are several, and
the rule then holds on each: `"course"`, `"front"`, `"back"`, `"each_nine"`,
`"each_hole"`, a list of hole numbers, `{"from": 1, "to": 6}`, and `{"window": 3}` for
every run of three holes in a row.

See docs/generation_rewrite.md.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard

from .layout import COUNTS

HOLES = 18
NINE = 9

#: the numbers a hole carries that `sum`, `step` and `gap` can read
NUMBERS = ("par", "distance", "over_par", "original_hole")
#: what `limit` can group holes by
KEYS = ("family", "course", "rom", "author", "par", "kind", "id", "original_hole")
_VALUE_FIELDS = ("par", "tag", "rom", "course", "kind", "family", "id", "author")
_RANGE_FIELDS = ("distance", "over_par")
SLOT = "slot"


class RuleError(ValueError):
    """A rule this code does not know, or one written wrongly."""


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> TypeGuard[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fields(
    data: Mapping, name: str, required: Sequence[str], optional: Sequence[str] = ()
) -> None:
    missing = [key for key in required if key not in data]
    unknown = sorted(set(data) - set(required) - set(optional) - {"rule"})
    if missing or unknown:
        raise RuleError(f"{name}: missing {missing}, unknown {unknown}")


# -- Scopes -----------------------------------------------------------------------------------


def _hole_number(value: object, what: str) -> int:
    if not _is_int(value) or not 1 <= value <= HOLES:
        raise RuleError(f"{what}: a hole number is 1-{HOLES}, got {value!r}")
    return value - 1


def scopes(spec: object, what: str = "scope") -> list[tuple[int, ...]]:
    """The groups of slots (numbered from 0) a scope names, one group per application."""
    if spec == "course":
        return [tuple(range(HOLES))]
    if spec == "front":
        return [tuple(range(NINE))]
    if spec == "back":
        return [tuple(range(NINE, HOLES))]
    if spec == "each_nine":
        return [tuple(range(NINE)), tuple(range(NINE, HOLES))]
    if spec == "each_hole":
        return [(slot,) for slot in range(HOLES)]
    if isinstance(spec, list) and spec:
        slots = tuple(_hole_number(number, what) for number in spec)
        if len(set(slots)) != len(slots):
            raise RuleError(f"{what}: a hole is listed twice in {spec!r}")
        return [slots]
    if isinstance(spec, dict) and set(spec) == {"window"}:
        width = spec["window"]
        if not _is_int(width) or not 2 <= width <= HOLES:
            raise RuleError(f"{what}: a window is 2-{HOLES} holes, got {width!r}")
        return [
            tuple(range(start, start + width)) for start in range(HOLES - width + 1)
        ]
    if isinstance(spec, dict) and set(spec) == {"from", "to"}:
        first = _hole_number(spec["from"], what)
        last = _hole_number(spec["to"], what)
        if first > last:
            raise RuleError(f"{what}: from {spec['from']} is after to {spec['to']}")
        return [tuple(range(first, last + 1))]
    raise RuleError(
        f"{what}: expected course, front, back, each_nine, each_hole, a list of hole "
        f'numbers, {{"from": a, "to": b}} or {{"window": n}}, got {spec!r}'
    )


def one_scope(spec: object, what: str) -> tuple[int, ...]:
    found = scopes(spec, what)
    if len(found) != 1:
        raise RuleError(f"{what}: needs a single group of holes, got {spec!r}")
    return found[0]


# -- Selectors --------------------------------------------------------------------------------


def _check_range(data: object, what: str, need: bool = False) -> None:
    if not isinstance(data, dict) or set(data) - {"min", "max"}:
        raise RuleError(f"{what}: expected min and/or max, got {data!r}")
    if need and not data:
        raise RuleError(f"{what}: needs a min or a max")
    for key, value in data.items():
        if not _is_number(value):
            raise RuleError(f"{what}: {key} must be a number, got {value!r}")
    if "min" in data and "max" in data and data["min"] > data["max"]:
        raise RuleError(f"{what}: min {data['min']} is over max {data['max']}")


def check_where(where: object, what: str = "where") -> None:
    if not isinstance(where, dict):
        raise RuleError(f"{what}: a selector is an object, got {where!r}")
    for key, value in where.items():
        if key in _VALUE_FIELDS:
            values = value if isinstance(value, list) else [value]
            kind = _is_int if key == "par" else lambda item: isinstance(item, str)
            if not values or not all(kind(item) for item in values):
                raise RuleError(f"{what}: bad {key} {value!r}")
        elif key in _RANGE_FIELDS:
            _check_range(value, f"{what} {key}", need=True)
        elif key == "original_hole":
            if value == SLOT:
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                _hole_number(item, f"{what} original_hole")
        elif key == "not":
            check_where(value, f"{what} not")
        elif key == "any":
            if not isinstance(value, list) or not value:
                raise RuleError(f"{what} any: expected a list of selectors")
            for item in value:
                check_where(item, f"{what} any")
        else:
            raise RuleError(f"{what}: unknown selector field {key!r}")


def slot_bound(where: Mapping) -> bool:
    """Whether a selector's answer depends on the slot the hole is going in."""
    return (
        where.get("original_hole") == SLOT
        or ("not" in where and slot_bound(where["not"]))
        or any(slot_bound(item) for item in where.get("any", ()))
    )


def _check_bounds(data: Mapping, name: str, integral: bool = True) -> None:
    """`min`, `max` and `exactly`: at least one, and `exactly` alone."""
    given = [key for key in ("min", "max", "exactly") if key in data]
    if not given or ("exactly" in data and len(given) > 1):
        raise RuleError(f"{name}: needs min and/or max, or exactly alone")
    for key in given:
        value = data[key]
        if not (_is_int(value) if integral else _is_number(value)):
            raise RuleError(f"{name}: {key} must be a number, got {value!r}")
    if "min" in data and "max" in data and data["min"] > data["max"]:
        raise RuleError(f"{name}: min {data['min']} is over max {data['max']}")


def bounds(data: Mapping) -> tuple[float, float]:
    """A rule's `min`/`max`/`exactly` as a (low, high) pair, infinite where absent."""
    if "exactly" in data:
        return data["exactly"], data["exactly"]
    return data.get("min", float("-inf")), data.get("max", float("inf"))


def _check_number(name: object, what: str) -> None:
    if name not in NUMBERS:
        raise RuleError(f"{what}: expected one of {list(NUMBERS)}, got {name!r}")


def _check_measure(measure: object, what: str) -> None:
    if not isinstance(measure, dict) or len(measure) != 1:
        raise RuleError(
            f'{what}: expected {{"count": selector}} or {{"sum": number}}, got {measure!r}'
        )
    ((kind, value),) = measure.items()
    if kind == "count":
        check_where(value, f"{what} count")
    elif kind == "sum":
        _check_number(value, f"{what} sum")
    else:
        raise RuleError(f"{what}: unknown measure {kind!r}")


# -- The rules --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleType:
    name: str
    summary: str
    check: Callable[[Mapping], None]
    #: for a rule written in other rules, what it stands for
    expand: Callable[[Mapping], list[dict]] | None = None


RULES: dict[str, RuleType] = {}


def _register(name: str, summary: str, *, expand: bool = False):
    def decorate(function):
        if expand:

            def check(data: Mapping) -> None:
                for inner in function(data):
                    check_rule(inner)

            RULES[name] = RuleType(name, summary, check, function)
        else:
            RULES[name] = RuleType(name, summary, function)
        return function

    return decorate


@_register("count", "how many holes of a scope match a selector")
def _count(data: Mapping) -> None:
    _fields(data, "count", ("where",), ("scope", "min", "max", "exactly"))
    check_where(data["where"], "count where")
    scopes(data.get("scope", "course"), "count scope")
    _check_bounds(data, "count")


@_register("sum", "the total of a number over a scope")
def _sum(data: Mapping) -> None:
    _fields(data, "sum", ("of",), ("where", "scope", "min", "max", "exactly"))
    _check_number(data["of"], "sum of")
    if "where" in data:
        check_where(data["where"], "sum where")
        if slot_bound(data["where"]):
            raise RuleError("sum where: a sum's selector cannot depend on the slot")
    scopes(data.get("scope", "course"), "sum scope")
    _check_bounds(data, "sum", integral=False)


def compare_side(data: Mapping, side: str) -> tuple[tuple[int, ...], Mapping]:
    """One side of a `compare`: its slots and its measure."""
    spec = data[side]
    if isinstance(spec, dict) and "scope" in spec:
        _fields(spec, f"compare {side}", ("scope",), ("of",))
        scope, measure = spec["scope"], spec.get("of", data.get("of"))
    else:
        scope, measure = spec, data.get("of")
    if measure is None:
        raise RuleError(f"compare {side}: no measure; give the rule or the side an of")
    _check_measure(measure, f"compare {side} of")
    return one_scope(scope, f"compare {side}"), measure


@_register("compare", "one scope's count or sum less another's")
def _compare(data: Mapping) -> None:
    _fields(data, "compare", ("left", "right"), ("of", "min", "max", "exactly"))
    compare_side(data, "left")
    compare_side(data, "right")
    _check_bounds(data, "compare", integral=False)


@_register("limit", "how many holes of a scope may share a family, a course, ...")
def _limit(data: Mapping) -> None:
    _fields(data, "limit", ("per",), ("scope", "max", "min"))
    if data["per"] not in KEYS:
        raise RuleError(f"limit per: expected one of {list(KEYS)}, got {data['per']!r}")
    scopes(data.get("scope", "course"), "limit scope")
    for key in ("max", "min"):
        if key in data and (not _is_int(data[key]) or data[key] < 0):
            raise RuleError(f"limit {key}: expected a count, got {data[key]!r}")
    if data.get("max", 1) < 1:
        raise RuleError("limit max: must be at least 1; use exclude to leave holes out")
    if "min" in data and data["min"] > data.get("max", 1) and "max" in data:
        raise RuleError(f"limit: min {data['min']} is over max {data['max']}")


@_register("step", "how a number changes from each hole of a scope to the next")
def _step(data: Mapping) -> None:
    _fields(data, "step", ("of",), ("scope", "min", "max"))
    _check_number(data["of"], "step of")
    scopes(data.get("scope", "course"), "step scope")
    _check_range(
        {key: data[key] for key in ("min", "max") if key in data}, "step", True
    )


@_register("gap", "how far apart neighbors' numbers must be")
def _gap(data: Mapping) -> None:
    _fields(data, "gap", ("of", "min"), ("scope",))
    _check_number(data["of"], "gap of")
    scopes(data.get("scope", "course"), "gap scope")
    if not _is_number(data["min"]) or data["min"] <= 0:
        raise RuleError(f"gap min: expected a positive number, got {data['min']!r}")


@_register("weight", "make matching holes more or less likely to be picked")
def _weight(data: Mapping) -> None:
    _fields(data, "weight", ("where", "factor"))
    check_where(data["where"], "weight where")
    if not _is_number(data["factor"]) or data["factor"] <= 0:
        raise RuleError(
            f"weight factor: expected a positive number, got {data['factor']!r}; "
            "use exclude to leave holes out"
        )


# -- Rules written in other rules -------------------------------------------------------------


def _pars(value: object, what: str) -> list[int]:
    if not isinstance(value, list) or not value or not all(map(_is_int, value)):
        raise RuleError(f"{what}: expected a list of pars, got {value!r}")
    return value


@_register("par_counts", "how many holes of each par the course has", expand=True)
def _par_counts(data: Mapping) -> list[dict]:
    """`counts` maps a par to a number, or to `{"min": .., "max": ..}`. A par left out is
    not held to anything, so say 0 to leave one out."""
    _fields(data, "par_counts", ("counts",), ("scope",))
    counts = data["counts"]
    if not isinstance(counts, dict) or not counts:
        raise RuleError(f"par_counts counts: expected an object, got {counts!r}")
    rules = []
    for par, wanted in counts.items():
        if not (isinstance(par, str) and par.isdecimal()):
            raise RuleError(f"par_counts: a par is a number, got {par!r}")
        rule = {"rule": "count", "where": {"par": int(par)}}
        if "scope" in data:
            rule["scope"] = data["scope"]
        if isinstance(wanted, dict):
            _check_range(wanted, f"par_counts par {par}", need=True)
            rule.update(wanted)
        else:
            rule["exactly"] = wanted
        rules.append(rule)
    return rules


@_register("balanced_nines", "each par is split evenly between the nines", expand=True)
def _balanced_nines(data: Mapping) -> list[dict]:
    _fields(data, "balanced_nines", (), ("pars", "tolerance"))
    tolerance = data.get("tolerance", 1)
    if not _is_int(tolerance) or tolerance < 0:
        raise RuleError(f"balanced_nines tolerance: got {tolerance!r}")
    return [
        {
            "rule": "compare",
            "of": {"count": {"par": par}},
            "left": "front",
            "right": "back",
            "min": -tolerance,
            "max": tolerance,
        }
        for par in _pars(data.get("pars", [3, 4, 5]), "balanced_nines pars")
    ]


@_register(
    "no_consecutive", "no run of holes that match, longer than `run`", expand=True
)
def _no_consecutive(data: Mapping) -> list[dict]:
    """Either `pars`, each held to on its own (no two par 3s in a row, no two par 5s),
    or one `where`."""
    _fields(data, "no_consecutive", (), ("pars", "where", "run", "scope"))
    if ("pars" in data) == ("where" in data):
        raise RuleError("no_consecutive: needs pars or where, not both")
    run = data.get("run", 1)
    if not _is_int(run) or not 1 <= run < HOLES:
        raise RuleError(f"no_consecutive run: expected 1-{HOLES - 1}, got {run!r}")
    selectors = (
        [{"par": par} for par in _pars(data["pars"], "no_consecutive pars")]
        if "pars" in data
        else [data["where"]]
    )
    scope = data.get("scope", "course")
    if scope != "course":
        raise RuleError("no_consecutive: only the whole course is supported")
    return [
        {"rule": "count", "where": where, "scope": {"window": run + 1}, "max": run}
        for where in selectors
    ]


@_register("total_par", "the course's par", expand=True)
def _total_par(data: Mapping) -> list[dict]:
    _fields(data, "total_par", (), ("min", "max", "exactly"))
    _check_bounds(data, "total_par")
    return [{**data, "rule": "sum", "of": "par"}]


@_register("standard", "a traditional course of the given par", expand=True)
def _standard(data: Mapping) -> list[dict]:
    """Four par 3s, and par 4s and 5s to the course's par, split evenly between the
    nines, with no two par 3s or par 5s in a row."""
    _fields(data, "standard", (), ("par",))
    par = data.get("par", 72)
    if not _is_int(par) or par not in COUNTS:
        raise RuleError(f"standard par: expected one of {sorted(COUNTS)}, got {par!r}")
    return [
        {
            "rule": "par_counts",
            "counts": {str(value): count for value, count in COUNTS[par].items()},
        },
        {"rule": "balanced_nines"},
        {"rule": "no_consecutive", "pars": [3, 5]},
    ]


@_register("expert_cap", "at most so many expert holes on each nine", expand=True)
def _expert_cap(data: Mapping) -> list[dict]:
    _fields(data, "expert_cap", ("per_nine",))
    per_nine = data["per_nine"]
    if not _is_int(per_nine) or not 0 <= per_nine <= NINE:
        raise RuleError(f"expert_cap per_nine: expected 0-{NINE}, got {per_nine!r}")
    return [
        {
            "rule": "count",
            "where": {"tag": "expert"},
            "scope": "each_nine",
            "max": per_nine,
        }
    ]


@_register("distinct_families", "no two holes of one family", expand=True)
def _distinct_families(data: Mapping) -> list[dict]:
    _fields(data, "distinct_families", ())
    return [{"rule": "limit", "per": "family"}]


@_register(
    "slot", "what the hole in one slot, or each of several, must be", expand=True
)
def _slot(data: Mapping) -> list[dict]:
    _fields(data, "slot", ("hole", "where"))
    holes = data["hole"] if isinstance(data["hole"], list) else [data["hole"]]
    return [
        {"rule": "count", "where": data["where"], "scope": [hole], "exactly": 1}
        for hole in holes
    ]


@_register("pin", "the hole in one slot", expand=True)
def _pin(data: Mapping) -> list[dict]:
    _fields(data, "pin", ("hole", "id"))
    if not isinstance(data["id"], str):
        raise RuleError(f"pin id: expected a hole id, got {data['id']!r}")
    return [{"rule": "slot", "hole": data["hole"], "where": {"id": data["id"]}}]


@_register(
    "vanilla_course", "a vanilla course's holes, each in its own slot", expand=True
)
def _vanilla_course(data: Mapping) -> list[dict]:
    """`holes` is a scope, for a vanilla nine or any other part of the course."""
    _fields(data, "vanilla_course", ("course",), ("holes",))
    if not isinstance(data["course"], str):
        raise RuleError(f"vanilla_course course: got {data['course']!r}")
    slots = one_scope(data.get("holes", "course"), "vanilla_course holes")
    return [
        {
            "rule": "slot",
            "hole": [slot + 1 for slot in slots],
            "where": {
                "course": data["course"],
                "kind": "vanilla",
                "original_hole": SLOT,
            },
        }
    ]


def _extreme(data: Mapping, name: str, sign: int) -> list[dict]:
    _fields(data, name, ("of", "hole"), ("among",))
    _check_number(data["of"], f"{name} of")
    hole = _hole_number(data["hole"], f"{name} hole")
    others = one_scope(data.get("among", "course"), f"{name} among")
    bound = "min" if sign > 0 else "max"
    return [
        {"rule": "step", "of": data["of"], "scope": [other + 1, hole + 1], bound: 0}
        for other in others
        if other != hole
    ]


@_register(
    "highest", "one slot's hole has the greatest of a number on the course", expand=True
)
def _highest(data: Mapping) -> list[dict]:
    """`among` is a scope, to be the greatest of only part of the course."""
    return _extreme(data, "highest", 1)


@_register(
    "lowest", "one slot's hole has the least of a number on the course", expand=True
)
def _lowest(data: Mapping) -> list[dict]:
    return _extreme(data, "lowest", -1)


@_register("exclude", "no hole of a scope matches", expand=True)
def _exclude(data: Mapping) -> list[dict]:
    _fields(data, "exclude", ("where",), ("scope",))
    return [
        {
            "rule": "count",
            "where": data["where"],
            "scope": data.get("scope", "course"),
            "max": 0,
        }
    ]


@_register(
    "require", "at least `min` holes of a scope match (1 by default)", expand=True
)
def _require(data: Mapping) -> list[dict]:
    _fields(data, "require", ("where",), ("scope", "min"))
    return [
        {
            "rule": "count",
            "where": data["where"],
            "scope": data.get("scope", "course"),
            "min": data.get("min", 1),
        }
    ]


def check_rule(data: object) -> None:
    if not isinstance(data, dict) or not isinstance(data.get("rule"), str):
        raise RuleError(f'a rule is an object with a "rule" name, got {data!r}')
    kind = RULES.get(data["rule"])
    if kind is None:
        raise RuleError(
            f"unknown rule {data['rule']!r}; the rules are {', '.join(sorted(RULES))}"
        )
    kind.check(data)


def expand(data: Mapping) -> list[dict]:
    """The general rules a rule stands for; a general rule is itself."""
    kind = RULES[data["rule"]]
    if kind.expand is None:
        return [dict(data)]
    return [general for inner in kind.expand(data) for general in expand(inner)]


@dataclass(frozen=True)
class Rule:
    """One checked rule, held as its canonical JSON so settings that carry rules can be
    compared and hashed."""

    text: str

    @classmethod
    def parse(cls, data: "Rule | object") -> "Rule":
        if isinstance(data, Rule):
            return data
        check_rule(data)
        return cls(json.dumps(data, sort_keys=True, separators=(",", ":")))

    @property
    def name(self) -> str:
        return self.to_json()["rule"]

    def to_json(self) -> dict:
        return json.loads(self.text)

    def expand(self) -> list[dict]:
        return expand(self.to_json())

    def __str__(self) -> str:
        return self.text


def parse_rules(data: object, what: str = "constraints") -> tuple[Rule, ...]:
    if not isinstance(data, (list, tuple)):
        raise RuleError(f"{what} must be a list of rules, got {data!r}")
    return tuple(Rule.parse(item) for item in data)
