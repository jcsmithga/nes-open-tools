"""
Generation: a catalog, a curation snapshot and settings in, a manifest out.

Every random choice comes from `settings.prng_seed`, drawn fresh when the settings have
none. Each purpose (a rolled rule's par mix, the hole draw, the transforms, the wind
directions, the wind speeds, the music, the magic words, the SRAM magic) gets its own
generator derived from the seed, and the wind seeds come from `derive_hole_seeds`, so a
change to how one thing is drawn leaves the others where they were.

The holes are composed (`golf/randomizer/compose.py`): `course_rules` rolls the
settings' rolled rules and adds the family rule, and the course is eighteen holes of the
pool that keep them all. The course the search finds is then mixed for `MIX_STEPS`
steps, which takes out the search's lean toward the limits of the rules. Each hole is
then mirrored with `mirror_chance` and has its hazards shuffled with `hazard_chance`,
each rolled on its own (ADR 0025).

Within one release, the same catalog, curation, settings and seed give the same
manifest. That is a property the tests hold generation to, not a promise across releases:
nothing regenerates a stored seed, which is what the site stored rather than its inputs
(ADR 0024). `GENERATOR_VERSION` is provenance, bumped when a change to generation is worth
telling seeds apart by. See docs/manifest.md.
"""

import random
import secrets
from dataclasses import replace

from golf.core.patches.seeded_wind import derive_hole_seeds
from golf.core.rng import predict_hole

from .build import BUILD_VERSION, FINISH_ABI_VERSION
from .catalog import JP_ROM, Catalog
from .compose import GenerationError, Problem
from .curation import CurationSnapshot
from .manifest import HOLE_COUNT, SCHEMA, Course, Manifest, Settings, Slot
from .music import RANDOM, choose_music
from .pool import build_pool, source_rom
from .rules import Rule, is_rolled, roll
from .transforms import MAX_SEED
from .wind import draw_directions, draw_speeds
from .words import draw_magic_words

GENERATOR_VERSION = 4
#: the steps of `Problem.mix` every course is put through: enough to bring a standard
#: course, with or without the expert cap, to the figures of a uniform draw
MIX_STEPS = 800
#: the transforms the settings' chances add, a slot that gets both mirrored first
MIRROR = "mirror@1"
HAZARDS = "hazards@1"
PRNG_SEED_BYTES = 8


#: SRAM magic bytes are drawn from this range: neither may be $00 or $FF, what blank SRAM holds
SRAM_MAGIC_BYTES = range(0x01, 0xFF)


__all__ = [
    "GENERATOR_VERSION",
    "MIX_STEPS",
    "GenerationError",
    "course_rules",
    "generate",
]


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


def course_rules(settings: Settings, rng: random.Random | None = None) -> list[Rule]:
    """The rules a seed's course keeps: the settings' own, each rolled rule rolled from
    `rng`, then the family rule. Settings with a rolled rule need an `rng`."""
    rules = settings.constraints
    if any(is_rolled(rule) for rule in rules):
        if rng is None:
            raise ValueError("settings with a rolled rule need a generator to roll it")
        rules = roll(rules, rng)
    return [*rules, Rule.parse({"rule": "distinct_families"})]


def draw_transforms(settings: Settings, prng_seed: str) -> list[tuple[str, ...]]:
    """Each slot's transforms. Every slot rolls both chances and draws a hazard seed,
    whatever it gets, so a change to one chance moves no other slot's rolls."""
    mirrors = stream(prng_seed, "mirror")
    hazards = stream(prng_seed, "hazards")
    slots = []
    for _ in range(HOLE_COUNT):
        mirrored = mirrors.randrange(100) < settings.mirror_chance
        shuffled = hazards.randrange(100) < settings.hazard_chance
        seed = hazards.randrange(MAX_SEED + 1)
        slots.append(
            (
                *([MIRROR] if mirrored else []),
                *([f"{HAZARDS}:{seed}"] if shuffled else []),
            )
        )
    return slots


def generate(
    catalog: Catalog,
    curation: CurationSnapshot,
    settings: Settings,
    request: dict | None = None,
) -> Manifest:
    """A seed's manifest. `request` is what `settings` were resolved from, recorded as
    submitted (`golf/randomizer/request.py`)."""
    prng_seed = (
        settings.prng_seed if settings.prng_seed is not None else new_prng_seed()
    )
    pool = build_pool(catalog, curation, settings)
    rules = course_rules(settings, stream(prng_seed, "par_mix"))
    entries = Problem(pool, rules).draw(stream(prng_seed, "holes"), mix=MIX_STEPS)
    transforms = draw_transforms(settings, prng_seed)

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
                transformed,
                wind_direction=direction,
                wind_speed=speed,
            )
            for entry, wind_seed, transformed, direction, speed in zip(
                entries, wind_seeds, transforms, directions, speeds, strict=True
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
        request=request,
    )
