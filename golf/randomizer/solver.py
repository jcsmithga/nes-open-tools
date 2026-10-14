"""
A small constraint solver: slots that each take one value, and propagators that narrow
what each slot may still take.

It knows nothing about golf. A problem is `slots` slots, values numbered from 0, and a
list of propagators; a slot's domain is an int used as a bit set of the values it may
still take. `solve` fills the slots by a randomized depth-first search: take a slot, give
it one of its values, let every propagator narrow the other slots, and go on; a slot left
with no value undoes the last choice. Given the kinds of value a problem has, it settles
every slot's kind before any slot's value. `golf/randomizer/compose.py` builds the
problems this solves.

A propagator only ever removes values, and must remove no value some solution uses. It
need not remove everything it could: whatever it misses, the search finds the hard way.
What it must do is refuse a complete assignment that breaks its rule, since nothing else
will.

The pieces:

- **Aggregates** measure a group of slots: `Count` (how many hold a value from a set) and
  `Total` (the sum of a number each value carries). Each gives the bounds its measure
  still has and can narrow domains to keep it inside a range.
- **Propagators** hold aggregates to something: `Bound` (one aggregate in a range),
  `Difference` (two aggregates' difference in a range), `Limit` (no more than so many
  slots sharing a group), `Step` (how a number changes from one slot to the next) and
  `Gap` (neighboring slots' numbers at least so far apart).
- **`Tally`** checks several counts and sums together, where each alone would let a
  state through that no assignment can come out of.

A search is bounded by its dead ends and by its work, both counts of steps and never
time, so the same problem and random generator stop at the same place on any machine.

See docs/generation_rewrite.md.
"""

import math
import random
from bisect import bisect_left, bisect_right
from collections import deque
from collections.abc import Callable, Iterator, Sequence

INFINITY = math.inf
#: slack when comparing sums of floats
EPSILON = 1e-9


class ContradictionError(Exception):
    """Some slot has no value left. `origin` is the propagator's, when one found it."""

    def __init__(self, origin: object = None):
        super().__init__(origin)
        self.origin = origin


class BudgetError(Exception):
    """The search gave up before finding a solution or proving there is none."""


def bits(mask: int) -> Iterator[int]:
    """The values in a bit set, lowest first."""
    while mask:
        low = mask & -mask
        yield low.bit_length() - 1
        mask ^= low


class View:
    """The domains a propagator reads and narrows, and the queue its narrowing feeds."""

    __slots__ = ("domains", "_touch", "work", "thorough")

    def __init__(
        self, domains: list[int], touch: Callable[[int], None], thorough: bool = False
    ):
        self.domains = domains
        self._touch = touch
        #: set before any choice is made, when a contradiction proves the whole
        #: problem has no answer and so is worth a costly propagator's full effort
        self.thorough = thorough
        #: what a propagator adds to when one run of it costs far more than a run
        #: usually does, in runs' worth, so the search's budget sees it
        self.work = 0

    def narrow(self, slot: int, mask: int) -> None:
        """Keep only the values of `slot` that are in `mask`."""
        old = self.domains[slot]
        new = old & mask
        if new == old:
            return
        if not new:
            raise ContradictionError
        self.domains[slot] = new
        self._touch(slot)


# -- Aggregates -------------------------------------------------------------------------------


class Count:
    """How many of `slots` hold a value from that slot's set, `masks[slot]`.

    `supply` caps the count from above when the slots must also take values from
    different groups: each `(group_masks, capacity)` says no more than `capacity` slots
    may share a group, so the count can be no more than the groups still on offer allow.
    """

    def __init__(
        self,
        slots: Sequence[int],
        masks: Sequence[int],
        supply: Sequence[tuple[Sequence[int], int]] = (),
    ):
        self.slots = tuple(slots)
        self.masks = masks
        self.supply = tuple(supply)

    def _split(self, domains: Sequence[int]) -> tuple[list[int], list[int]]:
        """The slots that must count, and the slots that may or may not."""
        must, maybe = [], []
        for slot in self.slots:
            domain, mask = domains[slot], self.masks[slot]
            if domain & mask:
                (maybe if domain & ~mask else must).append(slot)
        return must, maybe

    def _supplied(self, domains: Sequence[int], ceiling: int) -> int:
        if not self.supply or ceiling == 0:
            return ceiling
        on_offer = 0
        for slot in self.slots:
            on_offer |= domains[slot] & self.masks[slot]
        for group_masks, capacity in self.supply:
            groups = sum(1 for mask in group_masks if mask & on_offer)
            ceiling = min(ceiling, groups * capacity)
        return ceiling

    def bounds(self, domains: Sequence[int]) -> tuple[float, float]:
        must, maybe = self._split(domains)
        return len(must), self._supplied(domains, len(must) + len(maybe))

    def restrict(self, view: View, low: float, high: float) -> None:
        must, maybe = self._split(view.domains)
        least, most = len(must), len(must) + len(maybe)
        if least > high or self._supplied(view.domains, most) < low:
            raise ContradictionError
        if not maybe:
            return
        if least >= high:
            for slot in maybe:
                view.narrow(slot, ~self.masks[slot])
        elif most <= low:
            for slot in maybe:
                view.narrow(slot, self.masks[slot])

    def measure(self, picks: Sequence[int]) -> float:
        return sum(self.masks[slot] >> picks[slot] & 1 for slot in self.slots)


class Values:
    """A number for each value, indexed so a domain's least and greatest are quick.

    A value with no number (None) is in no range: `known` is the set of those that have
    one.
    """

    def __init__(self, numbers: Sequence[float | None]):
        self.numbers = numbers
        self.levels = sorted({number for number in numbers if number is not None})
        at = {level: 0 for level in self.levels}
        self.known = 0
        for value, number in enumerate(numbers):
            if number is not None:
                at[number] |= 1 << value
                self.known |= 1 << value
        #: the values that have a number, from least to greatest
        self.ascending = sorted(
            (value for value, number in enumerate(numbers) if number is not None),
            key=lambda value: (numbers[value], value),
        )
        #: at_most[i]: the values numbered levels[i] or less; at_least[i]: or more
        self.at_most, self.at_least = [], [0] * len(self.levels)
        running = 0
        for level in self.levels:
            running |= at[level]
            self.at_most.append(running)
        running = 0
        for index in range(len(self.levels) - 1, -1, -1):
            running |= at[self.levels[index]]
            self.at_least[index] = running

    def of(self, picks: Sequence[int], slots: Sequence[int]) -> list[float] | None:
        """The numbers of the values some slots were given, or None if one has none."""
        numbers = [self.numbers[picks[slot]] for slot in slots]
        if any(number is None for number in numbers):
            return None
        return [number for number in numbers if number is not None]

    def least(self, domain: int) -> float:
        """The smallest number among a domain's values. The domain must hold a known one."""
        low, high = 0, len(self.levels) - 1
        while low < high:
            middle = (low + high) // 2
            if domain & self.at_most[middle]:
                high = middle
            else:
                low = middle + 1
        return self.levels[low]

    def greatest(self, domain: int) -> float:
        low, high = 0, len(self.levels) - 1
        while low < high:
            middle = (low + high + 1) // 2
            if domain & self.at_least[middle]:
                low = middle
            else:
                high = middle - 1
        return self.levels[low]

    def between(self, low: float, high: float) -> int:
        """The values numbered from `low` to `high`."""
        first = bisect_left(self.levels, low - EPSILON)
        last = bisect_right(self.levels, high + EPSILON) - 1
        if first > last:
            return 0
        return self.at_least[first] & self.at_most[last]


class Total:
    """The sum over `slots` of the number each slot's value carries.

    `groups`, as `(group_of, capacity)`, says the slots also hold values of different
    groups (no more than `capacity` to a group). Slots that still have the same domain
    then cannot all take its least value: between them they take at least its least few
    from different groups, which is a far tighter bound on the sum, and the one that
    lets a rule out in the tail of what the pool gives (the shortest course, the
    easiest nine) narrow anything.
    """

    def __init__(
        self,
        slots: Sequence[int],
        values: Values,
        groups: tuple[Sequence[int], int] | None = None,
    ):
        self.slots = tuple(slots)
        self.values = values
        self.groups = groups

    def _extremes(self, domain: int, count: int, order: Sequence[int]) -> list[float]:
        """The first `count` numbers along `order` that a domain's values offer, taking
        no more from a group than it may give."""
        numbers = self.values.numbers
        group_of, capacity = self.groups  # type: ignore[misc]
        used: dict[int, int] = {}
        found: list[float] = []
        for value in order:
            if not domain >> value & 1:
                continue
            group = group_of[value]
            taken = used.get(group, 0)
            if taken >= capacity:
                continue
            used[group] = taken + 1
            found.append(numbers[value])  # type: ignore[arg-type]
            if len(found) == count:
                return found
        raise ContradictionError

    def _ranges(
        self, domains: Sequence[int]
    ) -> tuple[float, float, list[tuple[int, float, float]]]:
        """The least and greatest the sum can be, and for each slot the part of each
        that is its own: what it may exceed the one by, or fall short of the other by."""
        values = self.values
        if self.groups is None:
            ranges = []
            for slot in self.slots:
                domain = domains[slot] & values.known
                if not domain:
                    raise ContradictionError
                ranges.append((slot, values.least(domain), values.greatest(domain)))
            return (
                sum(low for _, low, _ in ranges),
                sum(high for _, _, high in ranges),
                ranges,
            )
        sharing: dict[int, list[int]] = {}
        for slot in self.slots:
            sharing.setdefault(domains[slot] & values.known, []).append(slot)
        least = most = 0.0
        ranges = []
        for domain, slots in sharing.items():
            if not domain:
                raise ContradictionError
            if len(slots) == 1 or not domain & (domain - 1):
                low, high = values.least(domain), values.greatest(domain)
                least += low * len(slots)
                most += high * len(slots)
            else:
                lows = self._extremes(domain, len(slots), values.ascending)
                highs = self._extremes(domain, len(slots), values.ascending[::-1])
                least += sum(lows)
                most += sum(highs)
                low, high = lows[-1], highs[-1]
            ranges.extend((slot, low, high) for slot in slots)
        return least, most, ranges

    def bounds(self, domains: Sequence[int]) -> tuple[float, float]:
        least, most, _ = self._ranges(domains)
        return least, most

    def restrict(self, view: View, low: float, high: float) -> None:
        values = self.values
        for slot in self.slots:
            view.narrow(slot, values.known)
        least, most, ranges = self._ranges(view.domains)
        if least > high + EPSILON or most < low - EPSILON:
            raise ContradictionError
        for slot, slot_low, slot_high in ranges:
            ceiling = high - (least - slot_low)
            floor = low - (most - slot_high)
            domain = view.domains[slot]
            if (
                values.greatest(domain) > ceiling + EPSILON
                or values.least(domain) < floor - EPSILON
            ):
                view.narrow(slot, values.between(floor, ceiling))

    def measure(self, picks: Sequence[int]) -> float:
        numbers = self.values.numbers
        total = 0.0
        for slot in self.slots:
            number = numbers[picks[slot]]
            if number is None:
                return math.nan
            total += number
        return total


Aggregate = Count | Total


# -- Propagators ------------------------------------------------------------------------------


class Propagator:
    """Narrows domains to keep one rule. `slots` are the ones it reads."""

    slots: tuple[int, ...]
    #: whatever the caller wants back when this propagator finds a contradiction
    origin: object = None
    #: a costly propagator waits until the others have nothing left to do
    costly = False

    def propagate(self, view: View) -> None:
        raise NotImplementedError

    def holds(self, picks: Sequence[int]) -> bool:
        """Whether a complete assignment keeps the rule."""
        raise NotImplementedError


class Bound(Propagator):
    """`low <= aggregate <= high`."""

    def __init__(self, aggregate: Aggregate, low: float, high: float, origin=None):
        self.aggregate = aggregate
        self.low, self.high = low, high
        self.slots = aggregate.slots
        self.origin = origin

    def propagate(self, view: View) -> None:
        if self.low > self.high + EPSILON:
            raise ContradictionError
        self.aggregate.restrict(view, self.low, self.high)

    def holds(self, picks: Sequence[int]) -> bool:
        return (
            self.low - EPSILON <= self.aggregate.measure(picks) <= self.high + EPSILON
        )


class Difference(Propagator):
    """`low <= left - right <= high`.

    `whole` is a range `left + right` is known to stay in, when some other rule holds
    their sum to one. A sum and a difference together pin each side down far better
    than either does alone: four par 3s on the course, the nines within one of each
    other, is two on each nine. `integral` says the measures are whole numbers.
    """

    def __init__(
        self,
        left: Aggregate,
        right: Aggregate,
        low: float,
        high: float,
        origin=None,
        whole: tuple[float, float] = (-INFINITY, INFINITY),
        integral: bool = False,
    ):
        self.left, self.right = left, right
        self.low, self.high = low, high
        self.slots = tuple(dict.fromkeys(left.slots + right.slots))
        self.origin = origin
        whole_low, whole_high = whole
        # left = (sum + difference) / 2 and right = (sum - difference) / 2
        self.left_range = self._halved(whole_low + low, whole_high + high, integral)
        self.right_range = self._halved(whole_low - high, whole_high - low, integral)

    @staticmethod
    def _halved(low: float, high: float, integral: bool) -> tuple[float, float]:
        low, high = low / 2, high / 2
        if integral:
            if low != -INFINITY:
                low = math.ceil(low - EPSILON)
            if high != INFINITY:
                high = math.floor(high + EPSILON)
        return low, high

    def propagate(self, view: View) -> None:
        right_low, right_high = self.right.bounds(view.domains)
        self.left.restrict(
            view,
            max(right_low + self.low, self.left_range[0]),
            min(right_high + self.high, self.left_range[1]),
        )
        left_low, left_high = self.left.bounds(view.domains)
        self.right.restrict(
            view,
            max(left_low - self.high, self.right_range[0]),
            min(left_high - self.low, self.right_range[1]),
        )

    def holds(self, picks: Sequence[int]) -> bool:
        difference = self.left.measure(picks) - self.right.measure(picks)
        return self.low - EPSILON <= difference <= self.high + EPSILON


class Limit(Propagator):
    """No more than `capacity` of `slots` hold values of any one group.

    `group_of[value]` is the value's group and `group_masks[group]` its values. With a
    capacity of 1 the slots hold values of different groups.

    Beside the forward check (a full group leaves every other slot's domain), it keeps a
    matching of slots to groups, so a set of slots that share too few groups between them
    is refused before the search walks into it.
    """

    def __init__(
        self,
        slots: Sequence[int],
        group_of: Sequence[int],
        group_masks: Sequence[int],
        capacity: int = 1,
        origin=None,
    ):
        self.slots = tuple(slots)
        self.group_of = group_of
        self.group_masks = group_masks
        self.capacity = capacity
        self.origin = origin
        self._largest = max((mask.bit_count() for mask in group_masks), default=1)

    def _groups(self, domain: int) -> list[int]:
        """The groups a domain's values belong to."""
        if len(self.group_masks) <= domain.bit_count():
            return [
                group for group, mask in enumerate(self.group_masks) if mask & domain
            ]
        return list(dict.fromkeys(self.group_of[value] for value in bits(domain)))

    def propagate(self, view: View) -> None:
        domains = view.domains
        group_of, group_masks = self.group_of, self.group_masks
        held: dict[int, int] = {}
        settled = 0
        for slot in self.slots:
            domain = domains[slot]
            group = group_of[(domain & -domain).bit_length() - 1]
            if not domain & ~group_masks[group]:
                held[group] = held.get(group, 0) + 1
                settled += 1
        for group, count in held.items():
            if count > self.capacity:
                raise ContradictionError
            if count == self.capacity:
                mask = group_masks[group]
                for slot in self.slots:
                    if domains[slot] & ~mask:
                        view.narrow(slot, ~mask)
        if not self._roomy(domains, settled):
            self._match(domains)

    def _roomy(self, domains: Sequence[int], settled: int) -> bool:
        """Whether a greedy fill plainly works, so no matching is needed.

        `settled` slots are down to one group, and a group they filled has left every
        other domain. Take the rest from fewest values to most: each can be given a group
        with room, as long as it has more groups than the slots before it could have
        filled. A domain of n values spans at least n / (largest group) groups.
        """
        group_masks = self.group_masks
        sizes = sorted(
            domain.bit_count()
            for domain in (domains[slot] for slot in self.slots)
            if domain & ~group_masks[self.group_of[(domain & -domain).bit_length() - 1]]
        )
        taken = 0 if self.capacity == 1 else settled
        return all(
            size // self._largest > (taken + before) // self.capacity
            for before, size in enumerate(sizes)
        )

    def _match(self, domains: Sequence[int]) -> None:
        holders: dict[int, list[int]] = {}
        choices = {slot: self._groups(domains[slot]) for slot in self.slots}

        def place(slot: int, seen: set[int]) -> bool:
            for group in choices[slot]:
                if group in seen:
                    continue
                seen.add(group)
                held = holders.setdefault(group, [])
                if len(held) < self.capacity:
                    held.append(slot)
                    return True
                for index, other in enumerate(held):
                    if place(other, seen):
                        held[index] = slot
                        return True
            return False

        for slot in sorted(self.slots, key=lambda slot: len(choices[slot])):
            if not place(slot, set()):
                raise ContradictionError

    def holds(self, picks: Sequence[int]) -> bool:
        counts: dict[int, int] = {}
        for slot in self.slots:
            group = self.group_of[picks[slot]]
            counts[group] = counts.get(group, 0) + 1
        return all(count <= self.capacity for count in counts.values())


class Step(Propagator):
    """`low <= next - previous <= high` for each pair of neighbors along `slots`."""

    def __init__(
        self,
        slots: Sequence[int],
        values: Values,
        low: float,
        high: float,
        origin=None,
    ):
        self.slots = tuple(slots)
        self.values = values
        self.low, self.high = low, high
        self.origin = origin

    def propagate(self, view: View) -> None:
        values, domains = self.values, view.domains
        for slot in self.slots:
            view.narrow(slot, values.known)
        pairs = list(zip(self.slots, self.slots[1:], strict=False))
        for previous, following in pairs:
            domain = domains[previous]
            view.narrow(
                following,
                values.between(
                    values.least(domain) + self.low, values.greatest(domain) + self.high
                ),
            )
        for previous, following in reversed(pairs):
            domain = domains[following]
            view.narrow(
                previous,
                values.between(
                    values.least(domain) - self.high, values.greatest(domain) - self.low
                ),
            )

    def holds(self, picks: Sequence[int]) -> bool:
        numbers = self.values.of(picks, self.slots)
        if numbers is None:
            return False
        return all(
            self.low - EPSILON <= after - before <= self.high + EPSILON
            for before, after in zip(numbers, numbers[1:], strict=False)
        )


class Gap(Propagator):
    """Neighbors along `slots` carry numbers at least `least` apart.

    The values allowed beside a value are no range, so this keeps a table of them and
    only consults it once a neighbor is down to a few values.
    """

    #: a domain larger than this narrows its neighbors too little to be worth the work
    SMALL = 12

    def __init__(self, slots: Sequence[int], values: Values, least: float, origin=None):
        self.slots = tuple(slots)
        self.values = values
        self.least = least
        self.origin = origin
        numbers = values.numbers
        self.beside = [
            0
            if number is None
            else values.between(-INFINITY, number - least)
            | values.between(number + least, INFINITY)
            for number in numbers
        ]

    def propagate(self, view: View) -> None:
        domains = view.domains
        for slot in self.slots:
            view.narrow(slot, self.values.known)
        for one, other in zip(self.slots, self.slots[1:], strict=False):
            for source, target in ((one, other), (other, one)):
                domain = domains[source]
                if domain.bit_count() > self.SMALL:
                    continue
                allowed = 0
                for value in bits(domain):
                    allowed |= self.beside[value]
                view.narrow(target, allowed)

    def holds(self, picks: Sequence[int]) -> bool:
        numbers = self.values.of(picks, self.slots)
        if numbers is None:
            return False
        return all(
            abs(after - before) >= self.least - EPSILON
            for before, after in zip(numbers, numbers[1:], strict=False)
        )


class Tally(Propagator):
    """Several counts, checked together.

    Each count propagator alone asks whether its own range can still be met. Counts
    that share slots can each say yes while no assignment meets them all: twelve expert
    holes, four par 3s, and only two expert par 3s to be had. This asks the joint
    question, and only that: it narrows nothing, and refuses a state no assignment of
    kinds to slots can come out of.

    It works on tallies, not slots. `cells` are sets of values that split every value
    one way (the kinds), and `atoms` groups of slots (the nines, say). The unknowns are
    how many slots of each atom hold each cell's values. A row says a sum of those, less
    another sum of them, is in a range: `(plus, minus, low, high)`, each side a list of
    `(cell, atom)`. Every atom's tallies add up to its size, and `caps[cell]` is the
    most slots the whole course can give a cell.

    `sums` brings totals into it: `(atoms, least, most, low, high)` holds a total over
    those atoms to a range, where `least[cell][n]` and `most[cell][n]` are the least and
    most that n slots holding the cell's values can add up to. So a course that must be
    longer than its mix of pars can be is refused before a hole is picked.

    The tallies are found by a small search of their own, bounded by `node_limit`
    steps (`root_limit` before any choice is made, when refusing the state proves there
    is no course at all); past that the state is let through, which is always safe.
    """

    costly = True

    def __init__(
        self,
        atoms: Sequence[Sequence[int]],
        cells: Sequence[int],
        rows: Sequence[
            tuple[Sequence[tuple[int, int]], Sequence[tuple[int, int]], float, float]
        ],
        caps: Sequence[float],
        origin=None,
        node_limit: int = 150,
        root_limit: int = 5000,
        sums: Sequence[
            tuple[
                Sequence[int],
                Sequence[Sequence[float]],
                Sequence[Sequence[float]],
                float,
                float,
            ]
        ] = (),
    ):
        self.sums = list(sums)
        self.atoms = [tuple(atom) for atom in atoms]
        self.cells = list(cells)
        self.slots = tuple(slot for atom in self.atoms for slot in atom)
        self.origin = origin
        self.node_limit = node_limit
        self.root_limit = root_limit
        width = len(self.atoms)

        def at(cell: int, atom: int) -> int:
            return cell * width + atom

        self.rows: list[tuple[tuple[int, ...], tuple[int, ...], float, float]] = [
            (
                tuple(at(*pair) for pair in plus),
                tuple(at(*pair) for pair in minus),
                low,
                high,
            )
            for plus, minus, low, high in rows
        ]
        for atom, slots in enumerate(self.atoms):
            everything = tuple(at(cell, atom) for cell in range(len(self.cells)))
            self.rows.append((everything, (), len(slots), len(slots)))
        for cell, cap in enumerate(caps):
            if cap < len(self.slots):
                self.rows.append(
                    (tuple(at(cell, atom) for atom in range(width)), (), 0, cap)
                )
        self._memo: dict[tuple, bool] = {}
        #: states let through only because the search for their tallies ran out
        self._unsure: set[tuple] = set()
        self._rows_of: list[list[int]] = [[] for _ in range(len(self.cells) * width)]
        for index, (plus, minus, _, _) in enumerate(self.rows):
            for unknown in plus + minus:
                self._rows_of[unknown].append(index)
        self._atom_of = {
            slot: atom for atom, slots in enumerate(self.atoms) for slot in slots
        }

    def propagate(self, view: View) -> None:
        domains = view.domains
        cells, width = self.cells, len(self.atoms)
        lows = [0] * (len(cells) * width)
        highs = lows.copy()
        settled = True
        for slot, atom in self._atom_of.items():
            domain = domains[slot]
            for cell, mask in enumerate(cells):
                if domain & mask:
                    highs[cell * width + atom] += 1
                    if not domain & ~mask:
                        lows[cell * width + atom] += 1
                        break
                    settled = False
        if settled:
            return  # every slot's kind is settled, and the counts themselves judge that
        key = (tuple(lows), tuple(highs))
        possible = self._memo.get(key)
        if possible is None or (view.thorough and key in self._unsure):
            if len(self._memo) > 20000:
                self._memo.clear()
                self._unsure.clear()
            possible, sure = self._possible(lows, highs, view)
            self._memo[key] = possible
            if not sure:
                self._unsure.add(key)
            else:
                self._unsure.discard(key)
        if not possible:
            raise ContradictionError

    def _tighten(
        self, lows: list[int], highs: list[int], dirty: Sequence[int] | None = None
    ) -> bool:
        """Narrow each tally to what the rows allow. `dirty` is the tallies that just
        changed, when only their rows need another look."""
        rows, rows_of = self.rows, self._rows_of
        if dirty is None:
            pending = list(range(len(rows)))
        else:
            pending = list(dict.fromkeys(row for i in dirty for row in rows_of[i]))
        waiting = set(pending)
        while pending:
            index = pending.pop()
            waiting.discard(index)
            plus, minus, low, high = rows[index]
            least = most = 0
            for i in plus:
                least += lows[i]
                most += highs[i]
            for i in minus:
                least -= highs[i]
                most -= lows[i]
            if least > high + EPSILON or most < low - EPSILON:
                return False
            if least >= low - EPSILON and most <= high + EPSILON:
                continue  # the row holds whatever its tallies turn out to be
            moved = []
            for sign, side in ((1, plus), (-1, minus)):
                for i in side:
                    if sign > 0:
                        ceiling = high - (least - lows[i])
                        floor = low - (most - highs[i])
                    else:
                        ceiling = most + lows[i] - low
                        floor = least + highs[i] - high
                    if ceiling < highs[i] - EPSILON:
                        highs[i] = math.floor(ceiling + EPSILON)
                        moved.append(i)
                    if floor > lows[i] + EPSILON:
                        lows[i] = math.ceil(floor - EPSILON)
                        moved.append(i)
                    if lows[i] > highs[i]:
                        return False
            for i in moved:
                for row in rows_of[i]:
                    if row not in waiting:
                        waiting.add(row)
                        pending.append(row)
        return True

    def _sums_reachable(self, lows: list[int], highs: list[int]) -> bool:
        width = len(self.atoms)
        for atoms, least, most, low, high in self.sums:
            smallest = largest = 0.0
            for cell in range(len(self.cells)):
                # a cell's slots across the atoms draw on the same values, so it is
                # their number together that the tables are read at
                fewest = sum(lows[cell * width + atom] for atom in atoms)
                span = range(
                    fewest,
                    min(
                        sum(highs[cell * width + atom] for atom in atoms),
                        len(least[cell]) - 1,
                    )
                    + 1,
                )
                if not span:
                    return False
                smallest += min(least[cell][n] for n in span)
                largest += max(most[cell][n] for n in span)
            if smallest > high + EPSILON or largest < low - EPSILON:
                return False
        return True

    #: what one step of the tally search costs, in propagator runs
    STEP_COST = 10

    def _possible(
        self, lows: list[int], highs: list[int], view: View
    ) -> tuple[bool, bool]:
        """Whether some tallies fit, and whether that was found out or assumed."""
        budget = self.root_limit if view.thorough else self.node_limit
        sure = True

        def search(
            lows: list[int], highs: list[int], dirty: Sequence[int] | None
        ) -> bool:
            nonlocal budget, sure
            budget -= 1
            view.work += self.STEP_COST
            if budget < 0:
                sure = False
                return True  # out of patience: let the state through
            if not self._tighten(lows, highs, dirty) or not self._sums_reachable(
                lows, highs
            ):
                return False
            widest = [i for i in range(len(lows)) if highs[i] > lows[i]]
            if not widest:
                return True
            pick = min(widest, key=lambda i: highs[i] - lows[i])
            for number in range(lows[pick], highs[pick] + 1):
                trial_lows, trial_highs = lows.copy(), highs.copy()
                trial_lows[pick] = trial_highs[pick] = number
                if search(trial_lows, trial_highs, (pick,)):
                    return True
            return False

        return search(list(lows), list(highs), None), sure

    def holds(self, picks: Sequence[int]) -> bool:
        return True


# -- Search -----------------------------------------------------------------------------------

#: picks a value for a slot from its domain
Chooser = Callable[[int, int, random.Random], int]


def uniform_chooser(slot: int, domain: int, rng: random.Random) -> int:
    return rng.choice(list(bits(domain)))


class Solver:
    """One problem: propagators over `slots` slots of values `0..size-1`."""

    def __init__(self, slots: int, size: int, propagators: Sequence[Propagator]):
        self.slot_count = slots
        self.size = size
        self.propagators = list(propagators)
        self._watchers: list[list[int]] = [[] for _ in range(slots)]
        for index, propagator in enumerate(self.propagators):
            for slot in propagator.slots:
                self._watchers[slot].append(index)
        #: propagator runs and dead ends over this solver's life, for measurement
        self.runs = 0
        self.dead_ends = 0
        #: runs, plus what the costly propagators report: what a search's budget counts
        self.work = 0

    def start(self) -> list[int]:
        """Every slot's domain before any choice, narrowed as far as propagation goes."""
        domains = [(1 << self.size) - 1] * self.slot_count
        if not self.size:
            raise ContradictionError
        self._propagate(domains, None)
        return domains

    def _propagate(self, domains: list[int], dirty: Sequence[int] | None) -> None:
        propagators, watchers = self.propagators, self._watchers
        queued = [False] * len(propagators)
        queue: deque[int] = deque()
        #: the costly propagators, run only once the others have settled
        later: deque[int] = deque()

        def enqueue(index: int) -> None:
            if not queued[index]:
                queued[index] = True
                (later if propagators[index].costly else queue).append(index)

        def touch(slot: int) -> None:
            for index in watchers[slot]:
                enqueue(index)

        if dirty is None:
            for index in range(len(propagators)):
                enqueue(index)
        for slot in dirty or ():
            touch(slot)
        view = View(domains, touch, thorough=dirty is None)
        try:
            while queue or later:
                index = queue.popleft() if queue else later.popleft()
                queued[index] = False
                self.runs += 1
                self.work += 1
                try:
                    propagators[index].propagate(view)
                except ContradictionError as dead:
                    if dead.origin is None:
                        dead.origin = propagators[index].origin
                    raise
        finally:
            self.work += view.work

    def solve(
        self,
        rng: random.Random,
        chooser: Chooser = uniform_chooser,
        *,
        dead_end_limit: int = 2000,
        work_limit: float = INFINITY,
        smallest_first: bool = False,
        start: Sequence[int] | None = None,
        classes: Sequence[Sequence[int]] | None = None,
    ) -> list[int]:
        """A value for every slot.

        Slots are filled in a shuffled order, or smallest domain first when
        `smallest_first` (ties still shuffled). Raises `ContradictionError` when no assignment
        exists, and `BudgetError` after `dead_end_limit` dead ends or `work_limit`
        propagator runs (`Solver.work`), whichever comes first. Both count steps, not
        time, so the same problem and generator give up at the same place anywhere.

        `classes[slot][value]` is the set of values the caller holds to be alike for that
        slot. The search first narrows every slot to one class, and only then picks
        values: when a kind of value cannot go in a slot, every value of the kind leaves
        at once, and by the time values are picked the propagators know what kind each
        slot holds. Any classes are sound: they only decide how the choices are grouped.
        """
        domains = list(start) if start is not None else self.start()
        order = list(range(self.slot_count))
        rng.shuffle(order)
        rank = {slot: position for position, slot in enumerate(order)}
        budget = dead_end_limit
        work_ends = self.work + work_limit

        def kinds_open(domains: list[int]) -> list[int]:
            """The slots whose domain still spans more than one class."""
            if classes is None:
                return []
            found = []
            for slot in order:
                domain = domains[slot]
                low = (domain & -domain).bit_length() - 1
                if domain & ~classes[slot][low]:
                    found.append(slot)
            return found

        def descend(domains: list[int]) -> list[int]:
            nonlocal budget
            while True:
                # every slot's kind of value first, and only then the values
                open_slots = kinds_open(domains)
                by_kind = bool(open_slots)
                if not by_kind:
                    open_slots = [
                        slot for slot in order if domains[slot] & (domains[slot] - 1)
                    ]
                    if not open_slots:
                        return domains
                if smallest_first:
                    slot = min(
                        open_slots, key=lambda s: (domains[s].bit_count(), rank[s])
                    )
                else:
                    slot = open_slots[0]
                value = chooser(slot, domains[slot], rng)
                if by_kind:
                    assert classes is not None
                    decision = classes[slot][value] & domains[slot]
                else:
                    decision = 1 << value
                child = domains.copy()
                child[slot] = decision
                try:
                    self._propagate(child, (slot,))
                    if self.work > work_ends:
                        raise BudgetError
                    return descend(child)
                except ContradictionError:
                    self.dead_ends += 1
                    budget -= 1
                    if budget < 0 or self.work > work_ends:
                        raise BudgetError from None
                # the choice led nowhere: drop it here, and let that narrow the rest
                domains[slot] &= ~decision
                if not domains[slot]:
                    raise ContradictionError
                self._propagate(domains, (slot,))

        solved = descend(domains)
        picks = [domain.bit_length() - 1 for domain in solved]
        for propagator in self.propagators:
            if not propagator.holds(picks):  # pragma: no cover - a propagator bug
                raise AssertionError(
                    f"the search returned an assignment that breaks {propagator!r}"
                )
        return picks
