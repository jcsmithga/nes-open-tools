"""
Course rules: what a seed's settings may say about the eighteen holes it is given.

A rule is a JSON object with a `rule` name and that rule's arguments. A course satisfies a
list of rules when it satisfies every one: rules only ever add, so a list is stricter than
any part of it. This module is the vocabulary and nothing else. It checks a rule's shape
and expands each rule into the few general ones; `golf/randomizer/compose.py` turns those
into a problem for `golf/randomizer/solver.py`, against a pool.

**Public rules** are the ones a seed's settings, and so a request, may hold: the course
modes and the expert cap (ADR 0025). The rest are building blocks the public rules
expand into, and generation's own family rule.

**General rules**, which every other rule is written in:

| Rule | Holds when |
|---|---|
| `count` | the number of holes in a scope that match `where` is within `min`/`max` |
| `compare` | one scope's count (`left`), less another's (`right`), is within `min`/`max` |
| `limit` | no more than `max` holes of the course share a family |

**A selector** (`where`) describes holes, and a hole matches when every field does:
`par`, `tag`, `course` and `kind` (a value or a list of them), and `original_hole` (a
number, a list, or `"slot"` for the number of the slot the hole is being put in).

**A scope** says which holes of the course a rule looks at. Some scopes are several, and
the rule then holds on each: `"course"`, `"front"`, `"back"`, `"each_nine"`, a list of
hole numbers, and `{"window": 3}` for every run of three holes in a row.

See docs/manifest.md.
"""

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard

from .layout import COUNTS

HOLES = 18
NINE = 9

#: what `limit` can group holes by
KEYS = ("family",)
_VALUE_FIELDS = ("par", "tag", "course", "kind")
SLOT = "slot"


class RuleError(ValueError):
    """A rule this code does not know, or one written wrongly."""


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


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
    raise RuleError(
        f"{what}: expected course, front, back, each_nine, a list of hole numbers or "
        f'{{"window": n}}, got {spec!r}'
    )


def one_scope(spec: object, what: str) -> tuple[int, ...]:
    found = scopes(spec, what)
    if len(found) != 1:
        raise RuleError(f"{what}: needs a single group of holes, got {spec!r}")
    return found[0]


# -- Selectors --------------------------------------------------------------------------------


def _check_range(data: object, what: str) -> None:
    if not isinstance(data, dict) or set(data) - {"min", "max"} or not data:
        raise RuleError(f"{what}: expected min and/or max, got {data!r}")
    for key, value in data.items():
        if not _is_int(value):
            raise RuleError(f"{what}: {key} must be a whole number, got {value!r}")
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
        elif key == "original_hole":
            if value == SLOT:
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                _hole_number(item, f"{what} original_hole")
        else:
            raise RuleError(f"{what}: unknown selector field {key!r}")


def slot_bound(where: Mapping) -> bool:
    """Whether a selector's answer depends on the slot the hole is going in."""
    return where.get("original_hole") == SLOT


def _check_bounds(data: Mapping, name: str) -> None:
    """`min`, `max` and `exactly`: at least one, and `exactly` alone."""
    given = [key for key in ("min", "max", "exactly") if key in data]
    if not given or ("exactly" in data and len(given) > 1):
        raise RuleError(f"{name}: needs min and/or max, or exactly alone")
    for key in given:
        value = data[key]
        if not _is_int(value):
            raise RuleError(f"{name}: {key} must be a whole number, got {value!r}")
    if "min" in data and "max" in data and data["min"] > data["max"]:
        raise RuleError(f"{name}: min {data['min']} is over max {data['max']}")


def bounds(data: Mapping) -> tuple[float, float]:
    """A rule's `min`/`max`/`exactly` as a (low, high) pair, infinite where absent."""
    if "exactly" in data:
        return data["exactly"], data["exactly"]
    return data.get("min", float("-inf")), data.get("max", float("inf"))


# -- The rules --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleType:
    name: str
    summary: str
    check: Callable[[Mapping], None]
    #: for a rule written in other rules, what it stands for
    expand: Callable[[Mapping], list[dict]] | None = None
    #: whether a seed's settings may hold it (ADR 0025)
    public: bool = False


RULES: dict[str, RuleType] = {}


def _register(name: str, summary: str, *, expand: bool = False, public: bool = False):
    def decorate(function):
        if expand:

            def check(data: Mapping) -> None:
                for inner in function(data):
                    check_rule(inner)

            RULES[name] = RuleType(name, summary, check, function, public)
        else:
            RULES[name] = RuleType(name, summary, function, public=public)
        return function

    return decorate


@_register("count", "how many holes of a scope match a selector")
def _count(data: Mapping) -> None:
    _fields(data, "count", ("where",), ("scope", "min", "max", "exactly"))
    check_where(data["where"], "count where")
    scopes(data.get("scope", "course"), "count scope")
    _check_bounds(data, "count")


@_register("compare", "one scope's count less another's")
def _compare(data: Mapping) -> None:
    """`of` is `{"count": selector}`, measured over the `left` and `right` scopes."""
    _fields(data, "compare", ("of", "left", "right"), ("min", "max", "exactly"))
    measure = data["of"]
    if not isinstance(measure, dict) or set(measure) != {"count"}:
        raise RuleError(f'compare of: expected {{"count": selector}}, got {measure!r}')
    check_where(measure["count"], "compare of count")
    one_scope(data["left"], "compare left")
    one_scope(data["right"], "compare right")
    _check_bounds(data, "compare")


@_register("limit", "how many holes of the course may share a family")
def _limit(data: Mapping) -> None:
    _fields(data, "limit", ("per",), ("max",))
    if data["per"] not in KEYS:
        raise RuleError(f"limit per: expected one of {list(KEYS)}, got {data['per']!r}")
    if "max" in data and (not _is_int(data["max"]) or data["max"] < 1):
        raise RuleError(
            f"limit max: expected a count of 1 or more, got {data['max']!r}"
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
            _check_range(wanted, f"par_counts par {par}")
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


@_register(
    "standard", "a traditional course of the given par", expand=True, public=True
)
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


@_register(
    "expert_cap", "at most so many expert holes on each nine", expand=True, public=True
)
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


@_register("par_3", "eighteen par 3s", expand=True, public=True)
def _par_3(data: Mapping) -> list[dict]:
    _fields(data, "par_3", ())
    return [{"rule": "par_counts", "counts": {"3": 18}}]


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


@_register(
    "vanilla_course",
    "a vanilla course's holes, each in its own slot",
    expand=True,
    public=True,
)
def _vanilla_course(data: Mapping) -> list[dict]:
    _fields(data, "vanilla_course", ("course",))
    if not isinstance(data["course"], str):
        raise RuleError(f"vanilla_course course: got {data['course']!r}")
    return [
        {
            "rule": "slot",
            "hole": list(range(1, HOLES + 1)),
            "where": {
                "course": data["course"],
                "kind": "vanilla",
                "original_hole": SLOT,
            },
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


def check_public(rules: Sequence[Rule]) -> None:
    """Refuse a rule a seed's settings may not hold (ADR 0025)."""
    public = sorted(name for name, kind in RULES.items() if kind.public)
    for rule in rules:
        if not RULES[rule.name].public:
            raise RuleError(
                f"{rule.name!r} is not a rule settings may hold; they may hold "
                f"{', '.join(public)}"
            )
