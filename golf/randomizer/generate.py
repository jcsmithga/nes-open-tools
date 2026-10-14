"""
Generation: a catalog, a curation snapshot and settings in, a manifest out.

Every random choice comes from `settings.prng_seed`, drawn fresh when the settings have
none. Each purpose (the layout, the hole draw, the wind directions, the wind speeds, the
music, the magic words, the SRAM magic) gets its own generator derived from the seed, and
the wind seeds come from `derive_hole_seeds`, so a change to how one thing is drawn leaves
the others where they were.

The settings' draw rule picks the hole draw: `draw_holes` for `uniform`, and
`draw_holes_capped` for `expert_cap`.

Within one release, the same catalog, curation, settings and seed give the same
manifest. That is a property the tests hold generation to, not a promise across releases:
nothing regenerates a stored seed, which is what the site stored rather than its inputs
(ADR 0024). `GENERATOR_VERSION` is provenance, bumped when a change to generation is worth
telling seeds apart by. See docs/manifest.md.
"""

import random
import secrets
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

from golf.core.patches.seeded_wind import derive_hole_seeds
from golf.core.rng import predict_hole

from .build import BUILD_VERSION, FINISH_ABI_VERSION
from .catalog import JP_ROM, Catalog, CatalogEntry
from .curation import CurationSnapshot
from .layout import choose_layout
from .manifest import NINE, SCHEMA, UNIFORM, Course, Manifest, Settings, Slot
from .music import RANDOM, choose_music
from .pool import Family, Pool, build_pool, source_rom
from .wind import draw_directions, draw_speeds
from .words import draw_magic_words

GENERATOR_VERSION = 2
PRNG_SEED_BYTES = 8


#: SRAM magic bytes are drawn from this range: neither may be $00 or $FF, what blank SRAM holds
SRAM_MAGIC_BYTES = range(0x01, 0xFF)


class GenerationError(Exception):
    """Settings the catalog cannot satisfy, such as a pool too small for the layout."""


def new_prng_seed() -> str:
    return secrets.token_hex(PRNG_SEED_BYTES)


def stream(prng_seed: str, purpose: str) -> random.Random:
    """The generator for one purpose. String seeds are hashed, so this is stable across runs."""
    return random.Random(f"{purpose}\0{prng_seed}")


def draw_sram_magic(rng: random.Random) -> int:
    """A 16-bit SRAM magic, high byte first, with neither byte $00 or $FF."""
    high = rng.choice(SRAM_MAGIC_BYTES)
    low = rng.choice(SRAM_MAGIC_BYTES)
    return (high << 8) | low


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


def generate(
    catalog: Catalog, curation: CurationSnapshot, settings: Settings
) -> Manifest:
    prng_seed = (
        settings.prng_seed if settings.prng_seed is not None else new_prng_seed()
    )
    pool = build_pool(catalog, curation, settings)
    layout = choose_layout(settings.par, stream(prng_seed, "layout"))
    rule = settings.draw_rule
    if rule.rule == UNIFORM:
        entries = draw_holes(pool, layout, stream(prng_seed, "holes"))
    else:
        assert rule.per_nine is not None
        entries = draw_holes_capped(
            pool, layout, stream(prng_seed, "holes"), rule.per_nine
        )

    if settings.music == RANDOM:
        mario_open = any(source_rom(entry) == JP_ROM for entry in entries)
        music = choose_music(stream(prng_seed, "music"), include_mario_open=mario_open)
    else:
        music = settings.music

    wind_seeds = derive_hole_seeds(prng_seed)
    dealt = [predict_hole(wind_seed, swings=0) for wind_seed in wind_seeds]
    directions = draw_directions(
        settings.wind_direction_profile,
        [forecast.direction_anchor for forecast in dealt],
        stream(prng_seed, "wind_direction"),
    )
    speeds = draw_speeds(
        settings.wind_speed_profile,
        [forecast.speed_anchor for forecast in dealt],
        stream(prng_seed, "wind_speed"),
    )
    course = Course(
        holes=tuple(
            Slot(
                entry.id,
                entry.par,
                wind_seed,
                wind_direction=direction,
                wind_speed=speed,
            )
            for entry, wind_seed, direction, speed in zip(
                entries, wind_seeds, directions, speeds, strict=True
            )
        ),
        music=music,
        mercy_point=settings.mercy_point,
        clubs=settings.clubs,
        magic_words=draw_magic_words(stream(prng_seed, "magic_words")),
        sram_magic=draw_sram_magic(stream(prng_seed, "sram_magic")),
    )
    return Manifest(
        schema=SCHEMA,
        generator_version=GENERATOR_VERSION,
        build_version=BUILD_VERSION,
        finish_abi_version=FINISH_ABI_VERSION,
        catalog_version=catalog.version,
        curation_stamp=curation.stamp,
        settings=replace(settings, prng_seed=prng_seed),
        course=course,
    )
