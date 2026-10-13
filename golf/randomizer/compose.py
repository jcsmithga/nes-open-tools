"""
Composing a course: a pool and a list of rules in, eighteen holes out.

`Problem` turns a pool and rules (`golf/randomizer/rules.py`) into slots, values and
propagators for `golf/randomizer/solver.py`: a slot is a hole of the course, a value is a
hole of the pool. `Problem.draw` then asks the solver for a course.

**The draw.** The slots are filled in a shuffled order, twice over: first each is
narrowed to a kind of hole (whatever the rules' selectors tell apart: a par, expert or
not), then each is given a hole. A slot takes a family, each as likely as any other still
open to it, and then one of that family's holes still open to it, so a hole with a twin
is no likelier than a hole without one. Nothing about the draw prefers one hole to
another except the rules: two holes no rule tells apart are equally likely to be on the
course. A `weight` rule is the one way to say otherwise. Past that, how often a hole
appears is something to measure (`golf/randomizer/survey.py`), and the draw is not
uniform over the courses that keep the rules: it runs toward their limits.

**When no course is found.** A search that runs out of choices has proved there is none.
`explain` then names the smallest set of rules it can find that cannot hold together,
and what the pool offers each of them. A search that gives up says so instead, and
names the rules a course is found without: it has proved nothing.

**What compiling adds.** Beside a propagator for each rule, `Problem` hands the solver
what makes the search short: the kinds of hole (`_classes`), the holes no rule can tell
apart (`_twins`), the joint check of the counts and sums over big scopes (`_add_tally`),
and a few things that follow from two rules together or from a rule over every run of
holes (`_whole`, `_together`, `_spread`).

See docs/generation_rewrite.md.
"""

import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path

from . import solver
from .catalog import REPO_ROOT, CatalogEntry, DerivedSource, RomSource
from .pool import Pool
from .rules import (
    HOLES,
    SLOT,
    Rule,
    bounds,
    compare_side,
    expand,
    scopes,
    slot_bound,
)

DEFAULT_DIFFICULTY = REPO_ROOT / "data" / "difficulty" / "holes.json"

#: (dead ends allowed, smallest domain first) for each try at a course. The shuffled
#: order is the fair one, so it goes first; smallest first is for problems tight enough
#: that a shuffled order keeps walking into the same corner. A search that is going to
#: find a course usually does so at once or not for a long while, so many short tries
#: beat a few long ones.
ATTEMPTS = (
    *[(40, False)] * 6,
    *[(40, True)] * 12,
    *[(300, True)] * 4,
    (1000, True),
)
#: of the changes `Problem.mix` tries, the share that swap two slots' holes, and the
#: share that give one slot another hole; the rest give two slots other holes at once
MIX_SWAPS = 0.25
MIX_SINGLES = 0.25
#: propagator runs (`solver.Solver.work`) every try at one course may spend between
#: them. A standard course takes about a thousand; this is a few seconds of looking.
WORK_LIMIT = 250_000
#: the same two limits for each trial solve `explain` makes
EXPLAIN_ATTEMPTS = ((400, True),)
EXPLAIN_WORK_LIMIT = 40_000

_IMPLICIT = "a hole is on the course once"
#: a count over fewer slots than this is left out of the joint check of counts
#: (`solver.Tally`): small scopes would split the course into too many parts to tally
TALLY_SCOPE = 5
#: a limit on every run of holes is turned into a cap on the course for each group
#: only when there are this few groups: pars or ROMs, not families
SPREAD_GROUPS = 12


class GenerationError(Exception):
    """Settings the catalog cannot satisfy, such as a pool too small for the rules."""


@cache
def load_over_par(path: Path = DEFAULT_DIFFICULTY) -> Mapping[str, float]:
    """Each lineage's predicted strokes over par, from the difficulty report's table."""
    if not path.exists():
        return {}
    rows = json.loads(path.read_text())["holes"]
    return {row["lineage"]: row["over_par"] for row in rows}


@dataclass(frozen=True)
class Candidate:
    """A hole of the pool, with what the rules can ask about it."""

    entry: CatalogEntry
    family: str
    tags: frozenset[str]
    over_par: float | None

    @cached_property
    def origin(self) -> RomSource | None:
        """Where in a vanilla ROM the hole, or the hole it is derived from, comes from."""
        source = self.entry.source
        if isinstance(source, DerivedSource):
            source = source.base.source
        return source if isinstance(source, RomSource) else None

    @cached_property
    def course(self) -> str | None:
        """The vanilla course the hole is from, as its lineages name it (`jp_uk`)."""
        entry = self.entry
        if isinstance(entry.source, DerivedSource):
            entry = entry.source.base
        if not isinstance(entry.source, RomSource):
            return None
        return entry.id.lineage.split("/")[0]

    @property
    def original_hole(self) -> int | None:
        return self.origin.hole if self.origin is not None else None

    def number(self, name: str) -> float | None:
        if name == "par":
            return self.entry.par
        if name == "distance":
            return self.entry.distance
        if name == "original_hole":
            return self.original_hole
        return self.over_par

    def key(self, name: str) -> object:
        """What `limit` groups by. None is a hole with no such thing, which shares it
        with no other hole."""
        if name == "family":
            return self.family
        if name == "id":
            return str(self.entry.id)
        if name in ("course", "original_hole"):
            return getattr(self, name)
        return getattr(self.entry, name)

    def matches(self, where: Mapping, slot: int) -> bool:
        entry = self.entry
        for field, wanted in where.items():
            if field == "not":
                if self.matches(wanted, slot):
                    return False
                continue
            if field == "any":
                if not any(self.matches(option, slot) for option in wanted):
                    return False
                continue
            if field in ("distance", "over_par"):
                number = self.number(field)
                if number is None or not (
                    wanted.get("min", number) <= number <= wanted.get("max", number)
                ):
                    return False
                continue
            if field == "original_hole":
                if wanted == SLOT:
                    wanted = [slot + 1]
                elif not isinstance(wanted, list):
                    wanted = [wanted]
                if self.original_hole not in wanted:
                    return False
                continue
            if not isinstance(wanted, list):
                wanted = [wanted]
            if field == "tag":
                if not self.tags & set(wanted):
                    return False
            elif field == "id":
                if str(entry.id) not in wanted and entry.id.lineage not in wanted:
                    return False
            elif self.key(field) not in wanted:
                return False
        return True


def _numbers(scope: Sequence[int]) -> str:
    return ", ".join(str(slot + 1) for slot in scope)


def _range(low: float, high: float) -> str:
    if low == high:
        return f"exactly {low:g}"
    if low == float("-inf"):
        return f"at most {high:g}"
    if high == float("inf"):
        return f"at least {low:g}"
    return f"{low:g} to {high:g}"


class Problem:
    """A pool and rules, compiled. `draw` gives a course; `violations` judges one."""

    def __init__(
        self,
        pool: Pool,
        rules: Sequence[Rule | Mapping],
        over_par: Mapping[str, float] | None = None,
    ):
        self.pool = pool
        self.rules = tuple(Rule.parse(rule) for rule in rules)
        scores = self._scores = load_over_par() if over_par is None else over_par
        self.candidates = [
            Candidate(
                member,
                family.key,
                pool.tags.get(member.id, frozenset()),
                scores.get(member.id.lineage),
            )
            for family in pool.families
            for member in family.members
        ]
        self.size = len(self.candidates)
        self._everything = (1 << self.size) - 1
        self._masks: dict[str, list[int]] = {}
        #: every set of holes a count reads, by slot, and every number and grouping in use
        self._counted: list[list[int]] = []
        self._limited: list[str] = []
        self._values: dict[str, solver.Values] = {}
        self._groups: dict[str, tuple[list[int], list[int]]] = {}
        self.family_of, self.family_masks = self.groups("family")

        general = [
            (origin, rule)
            for origin, top in enumerate(self.rules)
            for rule in expand(top.to_json())
        ]
        #: caps on what a count can reach, from the limits that cover the whole course
        self._supply = [
            (self.groups(rule["per"])[1], rule.get("max", 1))
            for _, rule in general
            if rule["rule"] == "limit"
            and scopes(rule.get("scope", "course")) == scopes("course")
        ]
        self._general = general
        #: counts over big scopes, for the joint check: (plus, minus, low, high), each
        #: side a list of (set of holes, slots)
        self._tallied: list[tuple[list, list, float, float]] = []
        #: sums over big scopes, for the same check: (number, slots, low, high)
        self._tallied_sums: list[
            tuple[solver.Values, tuple[int, ...], float, float]
        ] = []
        #: the range every rule so far holds one measure of one scope to, between them
        self._held: dict[tuple, tuple[float, float]] = {}
        #: the tightest course-wide limit, as a sum's `groups`
        self._apart: tuple[Sequence[int], int] | None = None
        for _, rule in general:
            if (
                rule["rule"] == "limit"
                and scopes(rule.get("scope", "course")) == scopes("course")
                and rule["per"] == "family"
            ):
                self._apart = (self.groups("family")[0], rule.get("max", 1))
        self.propagators: list[solver.Propagator] = []
        if not any(
            rule["rule"] == "limit"
            and rule["per"] in ("family", "id")
            and scopes(rule.get("scope", "course")) == scopes("course")
            for _, rule in general
        ):
            self._limited.append("id")
            self._supply.append((self.groups("id")[1], 1))
            self._apart = (self.groups("id")[0], 1)
            self.propagators.append(
                solver.Limit(range(HOLES), *self.groups("id"), origin=_IMPLICIT)
            )
        #: per-slot multipliers on a hole's chance of being picked, from `weight` rules
        self.weights: list[list[float]] | None = None
        #: what the pool offers each rule that asks for something, for `explain`
        self.notes: dict[int, list[str]] = {}
        for origin, rule in general:
            getattr(self, f"_compile_{rule['rule']}")(origin, rule)
        self._add_tally()
        self.solver = solver.Solver(HOLES, self.size, self.propagators)
        self.classes = self._classes()
        self.twins = self._twins()
        #: for `mix`: each family's holes, and the propagators that read each slot
        by_family: dict[int, list[int]] = {}
        for value, family in enumerate(self.family_of):
            by_family.setdefault(family, []).append(value)
        self._families = list(by_family.values())
        self._reading = [
            [
                index
                for index, propagator in enumerate(self.propagators)
                if slot in propagator.slots and not isinstance(propagator, solver.Tally)
            ]
            for slot in range(HOLES)
        ]

    # -- Compiling ----------------------------------------------------------------------------

    def masks(self, where: Mapping) -> list[int]:
        """For each slot, the holes of the pool that match a selector there."""
        text = json.dumps(where, sort_keys=True)
        found = self._masks.get(text)
        if found is None:

            def at(slot: int) -> int:
                mask = 0
                for value, candidate in enumerate(self.candidates):
                    if candidate.matches(where, slot):
                        mask |= 1 << value
                return mask

            if slot_bound(where):
                found = [at(slot) for slot in range(HOLES)]
            else:
                found = [at(0)] * HOLES
            self._masks[text] = found
        return found

    def values(self, name: str, where: Mapping | None = None) -> solver.Values:
        """A number of every hole of the pool. With `where`, a hole that does not match
        counts for nothing, so a sum is over the holes that do."""
        key = name if where is None else f"{name} {json.dumps(where, sort_keys=True)}"
        if key not in self._values:
            numbers = [candidate.number(name) for candidate in self.candidates]
            if where is not None:
                mask = self.masks(where)[0]
                numbers = [
                    number if mask >> value & 1 else 0
                    for value, number in enumerate(numbers)
                ]
            self._values[key] = solver.Values(numbers)
        return self._values[key]

    def groups(self, key: str) -> tuple[list[int], list[int]]:
        """Each hole's group under a key, and each group's holes."""
        if key not in self._groups:
            index: dict[object, int] = {}
            group_of, group_masks = [], []
            for value, candidate in enumerate(self.candidates):
                label = candidate.key(key)
                group = (
                    len(group_masks)
                    if label is None
                    else index.setdefault(label, len(group_masks))
                )
                if group == len(group_masks):
                    group_masks.append(0)
                group_of.append(group)
                group_masks[group] |= 1 << value
            self._groups[key] = (group_of, group_masks)
        return self._groups[key]

    def _classes(self) -> list[list[int]]:
        """For each slot and hole, the holes no selector tells apart from it there.

        The solver narrows a slot to one of these before it settles on a hole, so a par
        that cannot go in a slot is refused once and not once a hole.
        """
        found: dict[tuple[int, ...], list[int]] = {}
        classes = []
        for slot in range(HOLES):
            masks = tuple(dict.fromkeys(masks[slot] for masks in self._counted))
            if masks not in found:
                by_signature: dict[tuple[int, ...], int] = {}
                signatures = []
                for value in range(self.size):
                    signature = tuple(mask >> value & 1 for mask in masks)
                    signatures.append(signature)
                    by_signature[signature] = (
                        by_signature.get(signature, 0) | 1 << value
                    )
                found[masks] = [by_signature[signature] for signature in signatures]
            classes.append(found[masks])
        return classes

    def _tally(
        self,
        low: float,
        high: float,
        plus: tuple[list[int], Sequence[int]],
        minus: tuple[list[int], Sequence[int]] | None = None,
    ) -> None:
        """Offer a count, or a difference of two, to the joint check."""
        sides = [plus] if minus is None else [plus, minus]
        if all(
            len(set(masks)) == 1 and len(scope) >= TALLY_SCOPE for masks, scope in sides
        ):
            self._tallied.append(
                (
                    [(plus[0][0], tuple(plus[1]))],
                    [] if minus is None else [(minus[0][0], tuple(minus[1]))],
                    low,
                    high,
                )
            )

    def _extremes(
        self, numbers: solver.Values, cell: int
    ) -> tuple[list[float], list[float]]:
        """The least and the most that n holes of a set can total, for n from 0 to 18,
        when they must be of different families (or whatever keeps holes apart)."""
        assert self._apart is not None
        group_of, capacity = self._apart

        def running(order: Sequence[int], beyond: float) -> list[float]:
            used: dict[int, int] = {}
            totals = [0.0]
            for value in order:
                if not cell >> value & 1 or used.get(group_of[value], 0) >= capacity:
                    continue
                used[group_of[value]] = used.get(group_of[value], 0) + 1
                totals.append(totals[-1] + numbers.numbers[value])  # type: ignore[operator]
                if len(totals) > HOLES:
                    break
            return totals + [beyond] * (HOLES + 1 - len(totals))

        return (
            running(numbers.ascending, solver.INFINITY),
            running(numbers.ascending[::-1], -solver.INFINITY),
        )

    def _add_tally(self) -> None:
        if len(self._tallied) + len(self._tallied_sums) < 2:
            return
        sides = [side for plus, minus, _, _ in self._tallied for side in plus + minus]
        # the slots split by which scopes they are in, and the holes by which sets
        scope_list = list(
            dict.fromkeys(
                [scope for _, scope in sides]
                + [scope for _, scope, _, _ in self._tallied_sums]
            )
        )
        by_membership: dict[tuple[bool, ...], list[int]] = {}
        for slot in range(HOLES):
            by_membership.setdefault(
                tuple(slot in scope for scope in scope_list), []
            ).append(slot)
        atoms = [slots for inside, slots in by_membership.items() if any(inside)]
        mask_list = list(dict.fromkeys(mask for mask, _ in sides))
        by_signature: dict[tuple[int, ...], int] = {}
        for value in range(self.size):
            signature = tuple(mask >> value & 1 for mask in mask_list)
            by_signature[signature] = by_signature.get(signature, 0) | 1 << value
        cells = list(by_signature.values())

        def pairs(mask: int, scope: Sequence[int]) -> list[tuple[int, int]]:
            return [
                (cell, atom)
                for cell, cell_mask in enumerate(cells)
                if cell_mask & mask
                for atom, slots in enumerate(atoms)
                if slots[0] in scope
            ]

        rows = [
            (
                [pair for side in plus for pair in pairs(*side)],
                [pair for side in minus for pair in pairs(*side)],
                low,
                high,
            )
            for plus, minus, low, high in self._tallied
        ]
        caps: list[float] = [solver.INFINITY] * len(cells)
        for group_masks, capacity in self._supply:
            for cell, cell_mask in enumerate(cells):
                groups = sum(1 for mask in group_masks if mask & cell_mask)
                caps[cell] = min(caps[cell], groups * capacity)
        sums = []
        for numbers, scope, low, high in self._tallied_sums:
            tables = [self._extremes(numbers, cell) for cell in cells]
            sums.append(
                (
                    [atom for atom, slots in enumerate(atoms) if slots[0] in scope],
                    [least for least, _ in tables],
                    [most for _, most in tables],
                    low,
                    high,
                )
            )
        self.propagators.append(solver.Tally(atoms, cells, rows, caps, sums=sums))

    def _twins(self) -> list[int]:
        """For each hole, the holes no rule can tell from it.

        Two holes are twins when every count reads them alike in every slot, every number
        in use is the same for both, and every grouping in use either puts them in one
        group or puts each in a group of its own. Swapping two such holes throughout a
        course that keeps the rules gives another that does.
        """
        columns: list[Sequence[object]] = []
        for masks in self._counted:
            for mask in dict.fromkeys(masks):
                columns.append([mask >> value & 1 for value in range(self.size)])
        columns.extend(values.numbers for values in self._values.values())
        for key in dict.fromkeys(self._limited):
            group_of, group_masks = self.groups(key)
            columns.append(
                [
                    -1 if group_masks[group].bit_count() == 1 else group
                    for group in group_of
                ]
            )
        by_signature: dict[tuple, int] = {}
        signatures = list(zip(*columns, strict=True)) if columns else [()] * self.size
        for value, signature in enumerate(signatures):
            by_signature[signature] = by_signature.get(signature, 0) | 1 << value
        return [by_signature[signature] for signature in signatures]

    def _group_labels(self, key: str) -> list[object]:
        group_of, group_masks = self.groups(key)
        labels: list[object] = [None] * len(group_masks)
        for value, candidate in enumerate(self.candidates):
            labels[group_of[value]] = candidate.key(key)
        return labels

    def _offer(self, where: Mapping, scope: Sequence[int], others: bool = False) -> str:
        """How many holes of the pool a selector matches in a scope, or does not."""
        masks = self.masks(where)
        on_offer = 0
        for slot in scope:
            on_offer |= masks[slot]
        if others:
            on_offer = self._everything & ~on_offer
        families = sum(1 for mask in self.family_masks if mask & on_offer)
        holes = on_offer.bit_count()
        return (
            f"the pool has {holes} hole{'' if holes == 1 else 's'} "
            f"{'other than' if others else 'matching'} "
            f"{json.dumps(where, sort_keys=True)}, in {families} "
            f"famil{'y' if families == 1 else 'ies'}"
        )

    def _count(
        self, scope: Sequence[int], masks: list[int], supplied: bool
    ) -> solver.Count:
        self._counted.append(masks)
        return solver.Count(scope, masks, self._supply if supplied else ())

    def _aggregate(self, measure: Mapping, scope: Sequence[int]) -> solver.Aggregate:
        ((kind, what),) = measure.items()
        if kind == "count":
            return self._count(scope, self.masks(what), True)
        return self._total(scope, what)

    def _total(
        self, scope: Sequence[int], name: str, where: Mapping | None = None
    ) -> solver.Total:
        """A sum that knows the slots hold different families, when the rules say so."""
        return solver.Total(scope, self.values(name, where), self._apart)

    def _whole(self, measure: Mapping, scope: frozenset[int]) -> tuple[float, float]:
        """The range some rule holds a measure to over exactly these slots, if one does."""
        ((kind, what),) = measure.items()
        low, high = -solver.INFINITY, solver.INFINITY
        for _, rule in self._general:
            if rule["rule"] == "count" and kind == "count":
                same = rule["where"] == what
            elif rule["rule"] == "sum" and kind == "sum":
                same = rule["of"] == what
            else:
                continue
            if same and any(
                frozenset(part) == scope for part in scopes(rule.get("scope", "course"))
            ):
                rule_low, rule_high = bounds(rule)
                low, high = max(low, rule_low), min(high, rule_high)
        return low, high

    def _together(
        self, key: tuple, low: float, high: float
    ) -> tuple[float, float] | None:
        """The range two or more rules leave a measure between them, when this is not
        the first to bound it and narrows what the others left."""
        held = self._held.get(key)
        self._held[key] = (
            (low, high) if held is None else (max(low, held[0]), min(high, held[1]))
        )
        if held is None or self._held[key] in ((low, high), held):
            return None
        return self._held[key]

    def _spread(
        self, origin: int, rule: Mapping, masks: list[int], most: float
    ) -> None:
        """What a cap on every run of holes says about the whole course.

        At most one par 4 in any two holes in a row is at most nine on the course. No
        window says so itself, and a count of ten elsewhere would send the search
        looking for a course that cannot exist.
        """
        spec = rule.get("scope", "course")
        if not (isinstance(spec, dict) and "window" in spec) or most >= spec["window"]:
            return
        width = spec["window"]
        ceiling = most * (HOLES // width) + min(most, HOLES % width)
        course = tuple(range(HOLES))
        self.propagators.append(
            solver.Bound(self._count(course, masks, False), 0, ceiling, origin)
        )
        self._tally(0, ceiling, (masks, course))

    def _compile_count(self, origin: int, rule: Mapping) -> None:
        low, high = bounds(rule)
        masks = self.masks(rule["where"])
        self._spread(origin, rule, masks, high)
        for scope in scopes(rule.get("scope", "course")):
            both = self._together(("count", id(masks), tuple(scope)), low, high)
            if both is not None:
                self.propagators.append(
                    solver.Bound(self._count(scope, masks, True), *both, origin)
                )
            self.propagators.append(
                solver.Bound(self._count(scope, masks, low > 0), low, high, origin)
            )
            self._tally(low, high, (masks, scope))
            if low > 0:
                self.notes.setdefault(origin, []).append(
                    self._offer(rule["where"], scope)
                )
            if low <= 0 and high < len(scope) and len(scope) >= TALLY_SCOPE:
                self.notes.setdefault(origin, []).append(
                    self._offer(rule["where"], scope, others=True)
                )

    def _compile_sum(self, origin: int, rule: Mapping) -> None:
        low, high = bounds(rule)
        where = rule.get("where")
        values = self.values(rule["of"], where)
        parts = scopes(rule.get("scope", "course"))
        for scope in parts:
            self.propagators.append(
                solver.Bound(self._total(scope, rule["of"], where), low, high, origin)
            )
            both = self._together(("sum", id(values), tuple(scope)), low, high)
            if both is not None:
                self.propagators.append(
                    solver.Bound(self._total(scope, rule["of"], where), *both, origin)
                )
            if len(scope) >= TALLY_SCOPE:
                self._tallied_sums.append((values, tuple(scope), low, high))
        union = tuple(sorted({slot for part in parts for slot in part}))
        if len(parts) > 1 and len(union) == sum(len(part) for part in parts):
            # each nine under 3 is the course under 6, and the two nines draw on the
            # same holes: held to as one sum, that is a far tighter bound than two
            both = (low * len(parts), high * len(parts))
            self.propagators.append(
                solver.Bound(self._total(union, rule["of"], where), *both, origin)
            )
            self._tallied_sums.append((values, union, *both))
        if values.known != self._everything:
            missing = (self._everything & ~values.known).bit_count()
            self.notes.setdefault(origin, []).append(
                f"{missing} holes of the pool have no {rule['of']}"
            )

    def _compile_compare(self, origin: int, rule: Mapping) -> None:
        low, high = bounds(rule)
        left_scope, left_measure = compare_side(rule, "left")
        right_scope, right_measure = compare_side(rule, "right")
        left = self._aggregate(left_measure, left_scope)
        right = self._aggregate(right_measure, right_scope)
        whole = (-solver.INFINITY, solver.INFINITY)
        if left_measure == right_measure and not set(left_scope) & set(right_scope):
            whole = self._whole(left_measure, frozenset(left_scope + right_scope))
        integral = "count" in left_measure and "count" in right_measure
        if integral:
            self._tally(
                low,
                high,
                (self.masks(left_measure["count"]), left_scope),
                (self.masks(right_measure["count"]), right_scope),
            )
        self.propagators.append(
            solver.Difference(left, right, low, high, origin, whole, integral)
        )

    def _compile_limit(self, origin: int, rule: Mapping) -> None:
        group_of, group_masks = self.groups(rule["per"])
        self._limited.append(rule["per"])
        labels = self._group_labels(rule["per"])
        if scopes(rule.get("scope", "course")) == scopes("course"):
            self.notes.setdefault(origin, []).append(
                f"the pool's holes have {len(group_masks)} different values of "
                f"{rule['per']}"
            )
        if len(group_masks) <= SPREAD_GROUPS:
            for mask in group_masks:
                self._spread(origin, rule, [mask] * HOLES, rule.get("max", 1))
        for scope in scopes(rule.get("scope", "course")):
            self.propagators.append(
                solver.Limit(scope, group_of, group_masks, rule.get("max", 1), origin)
            )
            if rule.get("min", 0) > 0:
                for group, mask in enumerate(group_masks):
                    if labels[group] is None:
                        continue
                    count = self._count(scope, [mask] * HOLES, False)
                    self.propagators.append(
                        solver.Bound(count, rule["min"], solver.INFINITY, origin)
                    )
                    self._tally(rule["min"], solver.INFINITY, ([mask] * HOLES, scope))

    def _compile_step(self, origin: int, rule: Mapping) -> None:
        low = rule.get("min", -solver.INFINITY)
        high = rule.get("max", solver.INFINITY)
        for scope in scopes(rule.get("scope", "course")):
            self.propagators.append(
                solver.Step(scope, self.values(rule["of"]), low, high, origin)
            )

    def _compile_gap(self, origin: int, rule: Mapping) -> None:
        for scope in scopes(rule.get("scope", "course")):
            self.propagators.append(
                solver.Gap(scope, self.values(rule["of"]), rule["min"], origin)
            )

    def _compile_weight(self, origin: int, rule: Mapping) -> None:
        if self.weights is None:
            self.weights = [[1.0] * self.size for _ in range(HOLES)]
        masks = self.masks(rule["where"])
        for slot in range(HOLES):
            for value in solver.bits(masks[slot]):
                self.weights[slot][value] *= rule["factor"]

    # -- Drawing ------------------------------------------------------------------------------

    def _choose(self, slot: int, domain: int, rng: random.Random) -> int:
        """A family still open to the slot, then one of its holes still open to it."""
        by_family: dict[int, list[int]] = {}
        family_of = self.family_of
        for value in solver.bits(domain):
            by_family.setdefault(family_of[value], []).append(value)
        families = list(by_family.values())
        if self.weights is None:
            return rng.choice(rng.choice(families))
        weights = self.weights[slot]
        members = rng.choices(
            families,
            [
                sum(weights[value] for value in family) / len(family)
                for family in families
            ],
        )[0]
        return rng.choices(members, [weights[value] for value in members])[0]

    def picks(self, rng: random.Random) -> list[int]:
        """A course as pool indexes, or GenerationError."""
        try:
            start = self.solver.start()
        except solver.ContradictionError:
            raise GenerationError(self.explain()) from None
        work_ends = self.solver.work + WORK_LIMIT
        for limit, smallest_first in ATTEMPTS:
            if self.solver.work >= work_ends:
                break
            try:
                return self.solver.solve(
                    rng,
                    self._choose,
                    dead_end_limit=limit,
                    work_limit=work_ends - self.solver.work,
                    smallest_first=smallest_first,
                    start=start,
                    classes=self.classes,
                    twins=self.twins,
                )
            except solver.BudgetError:
                continue
            except solver.ContradictionError:
                raise GenerationError(self.explain()) from None
        raise GenerationError(self.explain_giving_up())

    def mix(self, picks: Sequence[int], rng: random.Random, steps: int) -> list[int]:
        """A course reached from `picks` by `steps` random changes that keep the rules.

        Each step tries one small change and keeps it only if every rule still holds:
        two slots swap holes, one slot takes another hole of the pool, or two slots do
        at once (the change a fixed total needs, where one hole going up a par must be
        met by another coming down). A new hole is drawn as a family and then one of
        its holes.

        Every change is as likely to be tried as the change that undoes it, so with
        enough steps the course no longer depends on the one the search found: each
        course that keeps the rules is as likely as any other the changes can reach,
        counting a hole with a twin for no more than a hole without one. A `weight`
        rule makes a course likelier by the product of its holes' factors.
        """
        picks = list(picks)
        propagators, reading, weights = self.propagators, self._reading, self.weights
        families = self._families
        for _ in range(steps):
            roll = rng.random()
            if roll < MIX_SWAPS:
                first, second = rng.sample(range(HOLES), 2)
                changed = ((first, picks[second]), (second, picks[first]))
            else:
                count = 1 if roll < MIX_SWAPS + MIX_SINGLES else 2
                changed = tuple(
                    (slot, rng.choice(rng.choice(families)))
                    for slot in rng.sample(range(HOLES), count)
                )
            before = [picks[slot] for slot, _ in changed]
            if before == [value for _, value in changed]:
                continue
            if weights is not None:
                odds = 1.0
                for slot, value in changed:
                    odds *= weights[slot][value] / weights[slot][picks[slot]]
                if odds < 1 and rng.random() >= odds:
                    continue
            for slot, value in changed:
                picks[slot] = value
            if len(changed) == 1:
                indexes = reading[changed[0][0]]
            else:
                indexes = dict.fromkeys(
                    index for slot, _ in changed for index in reading[slot]
                )
            if not all(propagators[index].holds(picks) for index in indexes):
                for (slot, _), value in zip(changed, before, strict=True):
                    picks[slot] = value
        return picks

    def draw(self, rng: random.Random, mix: int = 0) -> list[CatalogEntry]:
        """Eighteen holes of the pool that keep every rule. With `mix`, the course the
        search finds is then put through that many steps of `Problem.mix`."""
        picks = self.picks(rng)
        if mix:
            picks = self.mix(picks, rng, mix)
        return [self.candidates[value].entry for value in picks]

    # -- Judging ------------------------------------------------------------------------------

    def violations(self, holes: Sequence[CatalogEntry]) -> list[str]:
        """The rules a finished course breaks, as text. Empty for a course `draw` could give."""
        index = {
            candidate.entry.id: value for value, candidate in enumerate(self.candidates)
        }
        missing = [str(hole.id) for hole in holes if hole.id not in index]
        if len(holes) != HOLES:
            return [f"a course has {HOLES} holes, got {len(holes)}"]
        if missing:
            return [f"not in the pool: {', '.join(missing)}"]
        picks = [index[hole.id] for hole in holes]
        broken = dict.fromkeys(
            propagator.origin
            for propagator in self.propagators
            if not propagator.holds(picks)
        )
        return [
            str(self.rules[origin]) if isinstance(origin, int) else str(origin)
            for origin in broken
        ]

    def allows(self, picks: Sequence[int]) -> bool:
        return all(propagator.holds(picks) for propagator in self.propagators)

    # -- Explaining ---------------------------------------------------------------------------

    def _listing(self, origins) -> str:
        lines = []
        for origin in origins:
            lines.append(f"  - {self.rules[origin]}")
            lines.extend(
                f"      ({note})" for note in dict.fromkeys(self.notes.get(origin, ()))
            )
        return "\n".join(lines)

    def _feasible(self, origins: Sequence[int]) -> bool | None:
        """Whether some course keeps these rules: None when the search gave up."""
        trial = Problem(
            self.pool, [self.rules[origin] for origin in origins], self._scores
        )
        rng = random.Random(0)
        try:
            start = trial.solver.start()
            for limit, smallest_first in EXPLAIN_ATTEMPTS:
                try:
                    trial.solver.solve(
                        rng,
                        trial._choose,
                        dead_end_limit=limit,
                        work_limit=EXPLAIN_WORK_LIMIT,
                        smallest_first=smallest_first,
                        start=start,
                        classes=trial.classes,
                        twins=trial.twins,
                    )
                    return True
                except solver.BudgetError:
                    continue
        except solver.ContradictionError:
            return False
        return None

    def conflict(self) -> list[int]:
        """The rules of a smallest set found that cannot hold together, by index.

        Each rule is dropped in turn, and stays dropped when the rest still provably
        cannot be met. What is left is a set no rule can leave without the proof being
        lost, though a smaller one may exist elsewhere.
        """
        core = list(range(len(self.rules)))
        for origin in list(core):
            trial = [other for other in core if other != origin]
            if self._feasible(trial) is False:
                core = trial
        return core

    def explain_giving_up(self) -> str:
        """What to say when the search ran out before finding a course or proving
        there is none: the rules, and which of them a course is found without."""
        message = (
            "no course was found for these rules, and the search gave up before "
            "proving there is none:\n" + self._listing(range(len(self.rules)))
        )
        everything = list(range(len(self.rules)))
        loosening = [
            origin
            for origin in everything
            if self._feasible([other for other in everything if other != origin])
        ]
        if loosening:
            message += (
                "\na course is found when any one of these is dropped:\n"
                + "\n".join(f"  - {self.rules[origin]}" for origin in loosening)
            )
        return message

    def explain(self) -> str:
        if not self.size:
            return "the pool is empty: no hole passes the settings' filters"
        core = self.conflict()
        families = len(self.pool.families)
        head = f"the pool ({self.size} holes in {families} families) "
        if not core:
            return head + f"cannot fill {HOLES} slots with different holes"
        if len(core) == 1:
            return head + "has no course that keeps this rule:\n" + self._listing(core)
        return (
            head
            + "has no course that keeps these rules together:\n"
            + self._listing(core)
        )


def compose(
    pool: Pool, rules: Sequence[Rule | Mapping], rng: random.Random
) -> list[CatalogEntry]:
    """Eighteen holes of the pool that keep every rule, or GenerationError."""
    return Problem(pool, rules).draw(rng)
