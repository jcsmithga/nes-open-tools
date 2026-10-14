"""Unit tests for the constraint solver, checked against brute force on small problems."""

import itertools
import random

import pytest

from golf.randomizer import solver
from golf.randomizer.solver import (
    Bound,
    BudgetError,
    ContradictionError,
    Count,
    Difference,
    Limit,
    Solver,
    Tally,
    bits,
)

SLOTS = 5
SIZE = 7


def mask_of(values) -> int:
    return sum(1 << value for value in values)


def brute_force(propagators, slots=SLOTS, size=SIZE) -> set[tuple[int, ...]]:
    """Every assignment each propagator's `holds` accepts."""
    return {
        picks
        for picks in itertools.product(range(size), repeat=slots)
        if all(propagator.holds(picks) for propagator in propagators)
    }


def tally_of(tallied, sets, group_masks, limited, slots, size) -> Tally:
    """The joint check of some counts, built the way `compose.Problem` builds one.

    `tallied` is (plus, minus, low, high) with each side a list of `Count`.
    """
    scopes = list(
        dict.fromkeys(
            count.slots for plus, minus, _, _ in tallied for count in plus + minus
        )
    )
    by_membership: dict[tuple, list[int]] = {}
    for slot in range(slots):
        by_membership.setdefault(tuple(slot in scope for scope in scopes), []).append(
            slot
        )
    atoms = [members for inside, members in by_membership.items() if any(inside)]
    by_signature: dict[tuple, int] = {}
    for value in range(size):
        signature = tuple(mask >> value & 1 for mask in sets)
        by_signature[signature] = by_signature.get(signature, 0) | 1 << value
    cells = list(by_signature.values())

    def pairs(count: Count) -> list[tuple[int, int]]:
        return [
            (cell, atom)
            for cell, cell_mask in enumerate(cells)
            if cell_mask & count.masks[0]
            for atom, members in enumerate(atoms)
            if members[0] in count.slots
        ]

    rows = [
        (
            [pair for count in plus for pair in pairs(count)],
            [pair for count in minus for pair in pairs(count)],
            low,
            high,
        )
        for plus, minus, low, high in tallied
    ]
    caps = [
        sum(1 for mask in group_masks if mask & cell) if limited else solver.INFINITY
        for cell in cells
    ]
    return Tally(atoms, cells, rows, caps, node_limit=10_000)


def random_problem(rng: random.Random, slots=SLOTS, size=SIZE):
    """A handful of random propagators over `slots` slots of `size` values, with the classes
    a caller would hand the solver for them."""
    group_of = [rng.randrange(4) for _ in range(size)]
    group_masks = [
        mask_of(v for v in range(size) if group_of[v] == group) for group in range(4)
    ]
    sets = [
        mask_of(rng.sample(range(size), rng.randint(1, size - 2))) for _ in range(3)
    ]

    def scope() -> tuple[int, ...]:
        return tuple(sorted(rng.sample(range(slots), rng.randint(2, slots))))

    def count(supplied: bool = False) -> Count:
        return Count(
            scope(),
            [rng.choice(sets)] * slots,
            [(group_masks, 1)] if supplied else (),
        )

    propagators: list = []
    limited = rng.random() < 0.6
    if limited:
        propagators.append(Limit(range(slots), group_of, group_masks, 1))
    tallied = []
    for _ in range(rng.randint(1, 4)):
        kind = rng.choice(["count", "difference", "limit"])
        low = rng.randint(0, 3)
        high = low + rng.randint(0, 3)
        if kind == "count":
            counted = count(limited)
            propagators.append(Bound(counted, low, high))
            tallied.append(([counted], [], low, high))
        elif kind == "difference":
            left, right = count(), count()
            propagators.append(
                Difference(left, right, low - 2, high - 2, integral=True)
            )
            tallied.append(([left], [right], low - 2, high - 2))
        else:
            propagators.append(Limit(scope(), group_of, group_masks, rng.randint(1, 2)))
    if tallied:
        propagators.append(tally_of(tallied, sets, group_masks, limited, slots, size))

    def signature(value: int) -> tuple:
        return tuple(mask >> value & 1 for mask in sets)

    classes = [
        [
            mask_of(v for v in range(size) if signature(v) == signature(value))
            for value in range(size)
        ]
    ] * slots
    return propagators, classes


@pytest.mark.parametrize("seed", range(150))
def test_random_problems_agree_with_brute_force(seed):
    rng = random.Random(seed)
    propagators, classes = random_problem(rng)
    answers = brute_force(propagators)
    problem = Solver(SLOTS, SIZE, propagators)
    found = set()
    for attempt in range(12):
        for smallest_first in (False, True):
            try:
                picks = problem.solve(
                    random.Random(f"{seed}/{attempt}"),
                    classes=classes,
                    smallest_first=smallest_first,
                    dead_end_limit=100_000,
                )
            except ContradictionError:
                assert not answers, "the solver proved a solvable problem unsolvable"
                return
            assert tuple(picks) in answers
            found.add(tuple(picks))
    assert answers, "the solver solved a problem brute force cannot"
    assert found


def small_problem(seed: int):
    """A random problem of three slots and five values with a few solutions, so every
    one of them can be waited for."""
    rng = random.Random(seed)
    while True:
        propagators, classes = random_problem(rng, 3, 5)
        answers = brute_force(propagators, 3, 5)
        if 2 <= len(answers) <= 30:
            return propagators, classes, answers


@pytest.mark.parametrize("seed", range(60))
def test_every_solution_can_be_drawn(seed):
    """Nothing the search does for speed puts a valid assignment out of reach."""
    propagators, classes, answers = small_problem(seed)
    problem = Solver(3, 5, propagators)
    found = set()
    for attempt in range(200 * len(answers)):
        found.add(
            tuple(problem.solve(random.Random(f"{seed}/{attempt}"), classes=classes))
        )
        if found == answers:
            break
    assert found == answers


def test_bits_lists_a_set_lowest_first():
    assert list(bits(0b101001)) == [0, 3, 5]
    assert list(bits(0)) == []


def test_a_count_forces_and_forbids():
    evens = mask_of([0, 2, 4, 6])
    exactly_two = Bound(Count(range(3), [evens] * 3), 2, 2)
    problem = Solver(3, SIZE, [exactly_two])
    domains = problem.start()
    domains[0] = 1 << 1  # an odd value: the other two slots must hold even ones
    problem._propagate(domains, (0,))
    assert domains[1] == domains[2] == evens
    domains = problem.start()
    domains[0], domains[1] = 1 << 0, 1 << 2  # two even: the third may not be
    problem._propagate(domains, (0, 1))
    assert not domains[2] & evens


def test_a_count_knows_the_groups_it_can_draw_on():
    # three slots must all hold values of the set, but the set spans two groups and
    # the slots take different groups
    group_of = [0, 0, 1, 1, 2, 2, 3]
    group_masks = [0b11, 0b1100, 0b110000, 0b1000000]
    wanted = mask_of([0, 1, 2, 3])
    count = Count(range(3), [wanted] * 3, [(group_masks, 1)])
    problem = Solver(
        3, SIZE, [Limit(range(3), group_of, group_masks), Bound(count, 3, 3)]
    )
    with pytest.raises(ContradictionError):
        problem.start()


def test_a_sum_and_a_difference_pin_both_sides():
    """Four of a kind in all, the halves within one of each other: two in each half."""
    kind = mask_of([0, 1, 2])
    left, right = Count(range(3), [kind] * 6), Count(range(3, 6), [kind] * 6)
    whole = Count(range(6), [kind] * 6)
    problem = Solver(
        6,
        SIZE,
        [
            Bound(whole, 4, 4),
            Difference(left, right, -1, 1, whole=(4, 4), integral=True),
        ],
    )
    domains = problem.start()
    for slot in (0, 1):  # two of the kind on the left: the third slot may not be
        domains[slot] = 1 << slot
    problem._propagate(domains, (0, 1))
    assert not domains[2] & kind


def test_a_limit_refuses_slots_that_share_too_few_groups():
    group_of = [0, 0, 1, 1, 2, 2, 3]
    group_masks = [0b11, 0b1100, 0b110000, 0b1000000]
    problem = Solver(3, SIZE, [Limit(range(3), group_of, group_masks)])
    domains = problem.start()
    for slot in range(3):  # three slots, values of two groups between them
        domains[slot] = 0b1111
    with pytest.raises(ContradictionError):
        problem._propagate(domains, range(3))


def test_a_tally_sees_what_no_single_count_does():
    """Two of kind A and two of kind B in three slots: each count alone looks fine."""
    kind_a, kind_b = mask_of([0, 1, 2]), mask_of([3, 4])
    counts = [
        Bound(Count(range(3), [kind_a] * 3), 2, 3),
        Bound(Count(range(3), [kind_b] * 3), 2, 3),
    ]
    assert Solver(3, SIZE, counts).start()
    cells = [kind_a, kind_b, mask_of([5, 6])]
    tally = Tally(
        [range(3)],
        cells,
        [([(0, 0)], [], 2, 3), ([(1, 0)], [], 2, 3)],
        [solver.INFINITY] * 3,
    )
    with pytest.raises(ContradictionError):
        Solver(3, SIZE, [*counts, tally]).start()


def test_a_search_out_of_budget_says_so():
    # a pigeonhole the propagators cannot see: every pair of six slots differs, with
    # five values, stated pair by pair
    identity = list(range(5))
    singles = [1 << value for value in range(5)]
    pairs = [
        Limit(pair, identity, singles) for pair in itertools.combinations(range(6), 2)
    ]
    with pytest.raises(BudgetError):
        Solver(6, 5, pairs).solve(random.Random(0), dead_end_limit=20)
    # work counts steps, so the search stops where it is told to, give or take the
    # round of propagation it was in the middle of
    worked = Solver(6, 5, pairs)
    domains = worked.start()
    before = worked.work
    with pytest.raises(BudgetError):
        worked.solve(random.Random(0), start=domains, work_limit=300)
    assert 300 < worked.work - before < 300 + 4 * len(pairs)
    with pytest.raises(ContradictionError):
        Solver(6, 5, pairs).solve(random.Random(0), dead_end_limit=100_000)
