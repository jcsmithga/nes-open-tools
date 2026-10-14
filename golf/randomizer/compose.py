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
course. Past that, the search is not uniform over the courses that keep the rules: it
runs toward their limits. `Problem.mix` then walks the course it found through random
changes that keep the rules, which takes the lean out (`golf/randomizer/survey.py`
measures both).

**When no course is found.** A search that runs out of choices has proved there is none.
`explain` then names the smallest set of rules it can find that cannot hold together,
and what the pool offers each of them. A search that gives up says so instead, and
names the rules a course is found without: it has proved nothing.

**What compiling adds.** Beside a propagator for each rule, `Problem` hands the solver
what makes the search short: the kinds of hole (`_classes`), the joint check of the
counts over big scopes (`_add_tally`), and a few things that follow from two rules
together or from a rule over every run of holes (`_whole`, `_together`, `_spread`).

See docs/manifest.md and docs/generation_rewrite.md.
"""

import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property

from . import solver
from .catalog import CatalogEntry, DerivedSource, RomSource
from .pool import Pool
from .rules import HOLES, SLOT, Rule, bounds, expand, one_scope, scopes, slot_bound

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


class GenerationError(Exception):
    """Settings the catalog cannot satisfy, such as a pool too small for the rules."""


@dataclass(frozen=True)
class Candidate:
    """A hole of the pool, with what the rules can ask about it."""

    entry: CatalogEntry
    family: str
    tags: frozenset[str]

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

    def key(self, name: str) -> object:
        """What a selector or `limit` reads. None is a hole with no such thing, which
        shares it with no other hole."""
        if name == "family":
            return self.family
        if name == "id":
            return str(self.entry.id)
        if name in ("course", "original_hole"):
            return getattr(self, name)
        return getattr(self.entry, name)

    def matches(self, where: Mapping, slot: int) -> bool:
        for field, wanted in where.items():
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

    def __init__(self, pool: Pool, rules: Sequence[Rule | Mapping]):
        self.pool = pool
        self.rules = tuple(Rule.parse(rule) for rule in rules)
        self.candidates = [
            Candidate(member, family.key, pool.tags.get(member.id, frozenset()))
            for family in pool.families
            for member in family.members
        ]
        self.size = len(self.candidates)
        self._everything = (1 << self.size) - 1
        self._masks: dict[str, list[int]] = {}
        #: every set of holes a count reads, by slot
        self._counted: list[list[int]] = []
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
        ]
        self._general = general
        #: counts over big scopes, for the joint check: (plus, minus, low, high), each
        #: side a list of (set of holes, slots)
        self._tallied: list[tuple[list, list, float, float]] = []
        #: the range every rule so far holds one measure of one scope to, between them
        self._held: dict[tuple, tuple[float, float]] = {}
        self.propagators: list[solver.Propagator] = []
        if not any(rule["rule"] == "limit" for _, rule in general):
            self._supply.append((self.groups("id")[1], 1))
            self.propagators.append(
                solver.Limit(range(HOLES), *self.groups("id"), origin=_IMPLICIT)
            )
        #: what the pool offers each rule that asks for something, for `explain`
        self.notes: dict[int, list[str]] = {}
        for origin, rule in general:
            getattr(self, f"_compile_{rule['rule']}")(origin, rule)
        self._add_tally()
        self.solver = solver.Solver(HOLES, self.size, self.propagators)
        self.classes = self._classes()
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

    def _add_tally(self) -> None:
        if len(self._tallied) < 2:
            return
        sides = [side for plus, minus, _, _ in self._tallied for side in plus + minus]
        # the slots split by which scopes they are in, and the holes by which sets
        scope_list = list(dict.fromkeys(scope for _, scope in sides))
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
        self.propagators.append(solver.Tally(atoms, cells, rows, caps))

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

    def _whole(self, where: Mapping, scope: frozenset[int]) -> tuple[float, float]:
        """The range some count holds a selector to over exactly these slots, if one
        does."""
        low, high = -solver.INFINITY, solver.INFINITY
        for _, rule in self._general:
            if (
                rule["rule"] == "count"
                and rule["where"] == where
                and any(
                    frozenset(part) == scope
                    for part in scopes(rule.get("scope", "course"))
                )
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

    def _compile_compare(self, origin: int, rule: Mapping) -> None:
        low, high = bounds(rule)
        where = rule["of"]["count"]
        masks = self.masks(where)
        left_scope = one_scope(rule["left"], "compare left")
        right_scope = one_scope(rule["right"], "compare right")
        whole = (-solver.INFINITY, solver.INFINITY)
        if not set(left_scope) & set(right_scope):
            whole = self._whole(where, frozenset(left_scope + right_scope))
        self._tally(low, high, (masks, left_scope), (masks, right_scope))
        self.propagators.append(
            solver.Difference(
                self._count(left_scope, masks, True),
                self._count(right_scope, masks, True),
                low,
                high,
                origin,
                whole,
                integral=True,
            )
        )

    def _compile_limit(self, origin: int, rule: Mapping) -> None:
        group_of, group_masks = self.groups(rule["per"])
        self.notes.setdefault(origin, []).append(
            f"the pool's holes have {len(group_masks)} different values of "
            f"{rule['per']}"
        )
        self.propagators.append(
            solver.Limit(
                range(HOLES), group_of, group_masks, rule.get("max", 1), origin
            )
        )

    # -- Drawing ------------------------------------------------------------------------------

    def _choose(self, slot: int, domain: int, rng: random.Random) -> int:
        """A family still open to the slot, then one of its holes still open to it."""
        by_family: dict[int, list[int]] = {}
        family_of = self.family_of
        for value in solver.bits(domain):
            by_family.setdefault(family_of[value], []).append(value)
        return rng.choice(rng.choice(list(by_family.values())))

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
        counting a hole with a twin for no more than a hole without one.
        """
        picks = list(picks)
        propagators, reading = self.propagators, self._reading
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
        trial = Problem(self.pool, [self.rules[origin] for origin in origins])
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
