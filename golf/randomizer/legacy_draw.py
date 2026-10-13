"""
The layout-first hole draw generation used through generator version 2.

A layout (a par for each slot) is drawn first, without looking at the pool, and the
slots are then filled from families that have a hole of the slot's par. `generate` no
longer calls this; it is kept so the draw that replaced it can be measured against it
(`golf/randomizer/survey.py`, docs/generation_rewrite.md).
"""

import random
from collections import Counter
from collections.abc import Sequence

from .catalog import CatalogEntry
from .compose import GenerationError
from .layout import choose_layout
from .manifest import NINE, Settings
from .pool import Family, Pool


def _matchable(pars: Sequence[int], offers: Sequence[frozenset[int]]) -> bool:
    """Whether every par can take a different family, given the pars each family offers.

    A bipartite matching of slots to families, by augmenting paths.
    """
    owner: dict[int, int] = {}  # family index -> slot index

    def augment(slot: int, seen: set[int]) -> bool:
        for index, offered in enumerate(offers):
            if index in seen or pars[slot] not in offered:
                continue
            seen.add(index)
            if index not in owner or augment(owner[index], seen):
                owner[index] = slot
                return True
        return False

    return all(augment(slot, set()) for slot in range(len(pars)))


def _shortfall(pool: Pool, layout: Sequence[int]) -> str:
    needed = Counter(layout)
    wants = ", ".join(f"{needed[par]} par {par}" for par in sorted(needed))
    offers = ", ".join(
        f"par {par}: {pool.families_with_par(par)}" for par in sorted(needed)
    )
    return (
        f"the pool cannot fill a par {sum(layout)} course from distinct families: it needs "
        f"{wants}, and the families offering each par are {offers}"
    )


def _expert_shortfall(pool: Pool, layout: Sequence[int]) -> str:
    needed = Counter(layout)
    wants = ", ".join(f"{needed[par]} par {par}" for par in sorted(needed))
    offers = ", ".join(
        f"par {par}: "
        + str(
            sum(
                any(member.id not in pool.experts for member in family.with_par(par))
                for family in pool.families
            )
        )
        for par in sorted(needed)
    )
    return (
        f"the pool cannot fill a par {sum(layout)} course from distinct families without "
        f"expert holes, which a capped draw requires: it needs {wants}, and the families "
        f"offering each par without one are {offers}"
    )


def draw_holes(
    pool: Pool, layout: Sequence[int], rng: random.Random
) -> list[CatalogEntry]:
    """One hole per slot, from a different family each time.

    Slot by slot, the candidate families are shuffled and the first one that leaves the
    remaining slots fillable is taken, then one of its members with the slot's par. That
    is the draw a backtracking search makes, with a matching check standing in for the
    backtracking. A family can hold holes of different pars, so a greedy draw could spend
    the only family able to fill some later slot.
    """
    families = list(pool.families)
    if not _matchable(layout, [family.pars for family in families]):
        raise GenerationError(_shortfall(pool, layout))
    chosen = []
    for position, par in enumerate(layout):
        candidates = [family for family in families if par in family.pars]
        rng.shuffle(candidates)
        rest_of_layout = layout[position + 1 :]
        for family in candidates:
            if _matchable(
                rest_of_layout,
                [other.pars for other in families if other is not family],
            ):
                break
        else:  # pragma: no cover - the matching above guarantees a candidate
            raise AssertionError("no fillable family for a matchable layout")
        families = [other for other in families if other is not family]
        chosen.append(rng.choice(family.with_par(par)))
    return chosen


def draw_holes_capped(
    pool: Pool, layout: Sequence[int], rng: random.Random, per_nine: int
) -> list[CatalogEntry]:
    """One hole per slot from a different family each time, at most `per_nine` of the
    pool's expert holes on each nine.

    The draw `draw_holes` makes, with two changes:

    - A slot on a nine that has its expert holes takes only a family's other members.
    - The slots are filled in a shuffled order. Filled in play order, a nine's expert
      holes would gather at its start, since each one drawn closes the slots after it.

    A family is taken only if the slots still empty could then be filled with no further
    expert hole, so expert holes are extras the draw never depends on: the pool must fill
    the layout without them, whatever the cap.
    """
    families = list(pool.families)

    def plain(family: Family, par: int) -> list[CatalogEntry]:
        return [
            member for member in family.with_par(par) if member.id not in pool.experts
        ]

    def plain_pars(family: Family) -> frozenset[int]:
        return frozenset(par for par in family.pars if plain(family, par))

    if not _matchable(layout, [plain_pars(family) for family in families]):
        raise GenerationError(_expert_shortfall(pool, layout))
    order = list(range(len(layout)))
    rng.shuffle(order)
    experts_on = [0] * (len(layout) // NINE + 1)
    chosen: dict[int, CatalogEntry] = {}
    for position, slot in enumerate(order):
        par = layout[slot]
        capped = experts_on[slot // NINE] >= per_nine
        still_empty = [layout[other] for other in order[position + 1 :]]
        candidates = [family for family in families if par in family.pars]
        rng.shuffle(candidates)
        for family in candidates:
            members = plain(family, par) if capped else list(family.with_par(par))
            if members and _matchable(
                still_empty,
                [plain_pars(other) for other in families if other is not family],
            ):
                break
        else:  # pragma: no cover - the matching above guarantees a candidate
            raise AssertionError("no fillable family for a matchable layout")
        families = [other for other in families if other is not family]
        chosen[slot] = rng.choice(members)
        experts_on[slot // NINE] += chosen[slot].id in pool.experts
    return [chosen[slot] for slot in range(len(layout))]


def draw_legacy(
    pool: Pool, settings: Settings, layout_rng: random.Random, holes_rng: random.Random
) -> list[CatalogEntry]:
    """The holes generator version 2 drew for these settings: a standard course of
    their par, under the expert cap their rules hold, if any. It reads no other rule."""
    layout = choose_layout(settings.par, layout_rng)
    cap = settings.experts_per_nine
    if cap is None:
        return draw_holes(pool, layout, holes_rng)
    return draw_holes_capped(pool, layout, holes_rng, cap)
