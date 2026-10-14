"""
The manifest: a randomized seed as JSON, holding everything that decides its unfinished ROM.

Three parts:

- The version fields: the manifest `schema`, generator, unfinished-build and finish-ABI
  versions, catalog version and the curation stamp that produced it.
- `settings`: every input to generation, the PRNG seed and the course's rules included,
  as a record of what the seed was made from. Nothing regenerates a stored seed (ADR 0024).
- `course`: concrete values, and the only part the recipe reads after version checks.
  Hole ids, never filters; a music slug, never "random".

The patches every seed gets are implied by `build_version`; the interface their unfinished
artifact exposes to personalization is `finish_abi_version`.

Two readers. `Manifest` is strict, for a manifest generation makes or a build reads: it is
this release's schema, and a missing or unknown field is an error. `StoredManifest` reads
a stored seed's manifest, issued by whichever release made the seed, for what showing and
finishing the seed need (ADR 0024). See docs/manifest.md and docs/patch_stack.md.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypeGuard

from golf.core.patches.sram_defaults import BAG_SIZE, Club, magic_bytes, parse_club
from golf.core.patches.wind_anchors import DIRECTIONS as WIND_DIRECTIONS
from golf.core.patches.wind_anchors import SPEEDS as WIND_SPEEDS
from golf.core.rng import predict_hole

from .catalog import (
    COMMUNITY,
    DERIVED,
    JP_ROM,
    US_ROM,
    Catalog,
    CatalogError,
    HoleId,
)
from .layout import COUNTS
from .music import RANDOM, TRACKS, track
from .rules import Rule, RuleError, parse_rules
from .transforms import TransformError, parse_transform
from .wind import DIRECTION_PROFILES, SPEED_PROFILES, VANILLA
from .words import MagicWordsError, check_magic_words

SCHEMA = 5
#: what a stored manifest without `build_version` or `finish_abi_version`, as schema 1's
#: are, was built with and exposes
LEGACY_BUILD_VERSION = 1
LEGACY_FINISH_ABI_VERSION = 1
HOLE_COUNT = 18
SOURCES = (US_ROM, JP_ROM)
#: the kinds of hole a seed's settings may add to its pool, which otherwise holds
#: vanilla holes only (ADR 0023)
INCLUDABLE = (DERIVED, COMMUNITY)
DEFAULT_PAR = 72
DEFAULT_MERCY_POINT = 9
MERCY_POINTS = range(1, 256)
HOLE_PARS = (3, 4, 5)
NINE = 9
#: the rule that caps the expert holes on each nine, and its cap on a default course
EXPERT_CAP = "expert_cap"
#: what a stored seed's draw rule is called when its course has no cap on expert holes
UNIFORM = "uniform"
DEFAULT_EXPERTS_PER_NINE = 1
WIND_SEEDS = range(0x10000)


class ManifestError(ValueError):
    """A malformed manifest, or settings no generation could use."""


def _fields(data: object, keys: tuple[str, ...], what: str) -> dict:
    if not isinstance(data, dict):
        raise ManifestError(f"{what} must be an object, got {data!r}")
    missing = [key for key in keys if key not in data]
    unknown = sorted(set(data) - set(keys))
    if missing or unknown:
        raise ManifestError(f"{what}: missing fields {missing}, unknown {unknown}")
    return data


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _strings(value: object, what: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ManifestError(
            f"{what} must be a list of non-empty strings, got {value!r}"
        )
    if len(set(value)) != len(value):
        raise ManifestError(f"{what} lists an entry more than once: {value!r}")
    return value


def _check_mercy(mercy_point: object) -> None:
    if mercy_point is not None and not (
        _is_int(mercy_point) and mercy_point in MERCY_POINTS
    ):
        raise ManifestError(f"mercy_point must be null or 1-255, got {mercy_point!r}")


def check_sram_magic(sram_magic: object) -> None:
    """A 16-bit value neither of whose stored bytes is $00 or $FF, the rule `sram_defaults` applies."""
    if not _is_int(sram_magic):
        raise ManifestError(f"sram_magic must be an integer, got {sram_magic!r}")
    try:
        magic_bytes(sram_magic)
    except ValueError as problem:
        raise ManifestError(str(problem)) from None


def dealt_wind(wind_seed: int) -> tuple[int, int]:
    """The (direction, speed) anchors a hole's wind seed deals with no table."""
    forecast = predict_hole(wind_seed, swings=0)
    return forecast.direction_anchor, forecast.speed_anchor


def _check_wind(slot: "Slot | StoredSlot") -> None:
    """Check a slot's wind seed, and fill in its missing anchors with the ones the seed
    deals and check them."""
    if not _is_int(slot.wind_seed) or slot.wind_seed not in WIND_SEEDS:
        raise ManifestError(
            f"{slot.id}: wind_seed must be 0-65535, got {slot.wind_seed!r}"
        )
    dealt = dealt_wind(slot.wind_seed)
    if slot.wind_direction is None:
        object.__setattr__(slot, "wind_direction", dealt[0])
    if slot.wind_speed is None:
        object.__setattr__(slot, "wind_speed", dealt[1])
    if not _is_int(slot.wind_direction) or slot.wind_direction not in WIND_DIRECTIONS:
        raise ManifestError(
            f"{slot.id}: wind_direction must be a multiple of 16 from 0 to 240, "
            f"got {slot.wind_direction!r}"
        )
    if not _is_int(slot.wind_speed) or slot.wind_speed not in WIND_SPEEDS:
        raise ManifestError(
            f"{slot.id}: wind_speed must be 0-10, got {slot.wind_speed!r}"
        )


def _hole_id(text: object) -> HoleId:
    if not isinstance(text, str):
        raise ManifestError(f"hole id must be a string, got {text!r}")
    try:
        hole_id = HoleId.parse(text)
    except CatalogError as problem:
        raise ManifestError(str(problem)) from None
    if str(hole_id) != text:
        raise ManifestError(f"hole id {text!r} is not canonical; write it as {hole_id}")
    return hole_id


def _transform_names(
    hole_id: HoleId, transforms: object, registered: bool = True
) -> tuple[str, ...]:
    """A slot's transform names: strings, and with `registered` ones the registry knows."""
    if not isinstance(transforms, list):
        raise ManifestError(f"{hole_id}: transforms must be a list, got {transforms!r}")
    for name in transforms:
        if not isinstance(name, str):
            raise ManifestError(f"{hole_id}: transform must be a string, got {name!r}")
        if registered:
            try:
                parse_transform(name)
            except TransformError as problem:
                raise ManifestError(f"{hole_id}: {problem}") from None
    return tuple(transforms)


def _clubs_from_labels(labels: object, what: str) -> frozenset[Club]:
    try:
        return frozenset(parse_club(label) for label in _strings(labels, what))
    except ValueError as problem:
        raise ManifestError(f"{what}: {problem}") from None


def _club_labels(clubs: Iterable[Club]) -> list[str]:
    return [club.label for club in sorted(clubs)]


@dataclass(frozen=True)
class ClubRules:
    """Limits on the bag a player may carry. The putter is always allowed.

    `max` counts the putter. A `required_bag` is the one bag every player carries; the
    putter is added to it if missing.
    """

    max: int = BAG_SIZE
    banned: frozenset[Club] = frozenset()
    required_bag: frozenset[Club] | None = None

    def __post_init__(self):
        object.__setattr__(self, "banned", frozenset(self.banned))
        if not _is_int(self.max) or not 1 <= self.max <= BAG_SIZE:
            raise ManifestError(f"clubs max must be 1-{BAG_SIZE}, got {self.max!r}")
        if Club.PT in self.banned:
            raise ManifestError("the putter cannot be banned")
        if self.required_bag is not None:
            bag = frozenset(self.required_bag) | {Club.PT}
            object.__setattr__(self, "required_bag", bag)
            if len(bag) > self.max:
                raise ManifestError(
                    f"the required bag has {len(bag)} clubs with the putter, over the max of {self.max}"
                )
            if bag & self.banned:
                raise ManifestError(
                    f"the required bag includes banned clubs: {', '.join(_club_labels(bag & self.banned))}"
                )

    def to_json(self) -> dict:
        return {
            "max": self.max,
            "banned": _club_labels(self.banned),
            "required_bag": None
            if self.required_bag is None
            else _club_labels(self.required_bag),
        }

    @classmethod
    def from_json(cls, data: object) -> "ClubRules":
        data = _fields(data, ("max", "banned", "required_bag"), "clubs")
        required = data["required_bag"]
        return cls(
            max=data["max"],
            banned=_clubs_from_labels(data["banned"], "clubs banned"),
            required_bag=None
            if required is None
            else _clubs_from_labels(required, "clubs required_bag"),
        )


def _check_par(par: object) -> None:
    if not _is_int(par) or par not in COUNTS:
        raise ManifestError(f"par must be one of {sorted(COUNTS)}, got {par!r}")


def standard_rules(
    par: int, experts_per_nine: int | None = DEFAULT_EXPERTS_PER_NINE
) -> tuple[Rule, ...]:
    """The rules of a standard course of `par` with at most `experts_per_nine` expert
    holes on each nine, or with no cap when that is None."""
    _check_par(par)
    rules: list[dict] = [{"rule": "standard", "par": par}]
    if experts_per_nine is not None:
        rules.append({"rule": EXPERT_CAP, "per_nine": experts_per_nine})
    try:
        return parse_rules(rules)
    except RuleError as problem:
        raise ManifestError(str(problem)) from None


_SLOT_KEYS = ("id", "par", "transforms", "wind_seed", "wind_direction", "wind_speed")
_SETTINGS_KEYS = (
    "prng_seed",
    "par",
    "sources",
    "include",
    "exclude_tags",
    "allow_family_repeats",
    "constraints",
    "wind_speed_profile",
    "wind_direction_profile",
    "music",
    "mercy_point",
    "clubs",
)


@dataclass(frozen=True)
class Settings:
    """What a seed is generated from. A `prng_seed` of None asks generation to draw one."""

    prng_seed: str | None = None
    par: int = DEFAULT_PAR
    sources: frozenset[str] = frozenset(SOURCES)
    #: the kinds of hole added to the pool beside the vanilla ones, from `INCLUDABLE`
    include: frozenset[str] = frozenset()
    exclude_tags: frozenset[str] = frozenset()
    allow_family_repeats: bool = False
    #: the course's rules (`golf/randomizer/rules.py`). None is the default course:
    #: a standard one of `par` with at most `DEFAULT_EXPERTS_PER_NINE` expert holes on
    #: each nine. A list is the whole of the rules, with nothing added but the family
    #: rule, and `par` then says nothing.
    constraints: tuple[Rule, ...] | None = None
    #: how the holes' wind speeds are chosen, one of `wind.SPEED_PROFILES`
    wind_speed_profile: str = VANILLA
    #: how the holes' wind directions are chosen, one of `wind.DIRECTION_PROFILES`
    wind_direction_profile: str = VANILLA
    #: a music slug, or "random"
    music: str = RANDOM
    #: the stroke a hole ends on with a tap-in; None leaves the patch out
    mercy_point: int | None = DEFAULT_MERCY_POINT
    clubs: ClubRules = ClubRules()

    def __post_init__(self):
        object.__setattr__(self, "sources", frozenset(self.sources))
        object.__setattr__(self, "include", frozenset(self.include))
        object.__setattr__(self, "exclude_tags", frozenset(self.exclude_tags))
        if self.prng_seed is not None and not (
            isinstance(self.prng_seed, str) and self.prng_seed
        ):
            raise ManifestError(
                f"prng_seed must be a non-empty string, got {self.prng_seed!r}"
            )
        _check_par(self.par)
        if not self.sources or not self.sources <= set(SOURCES):
            raise ManifestError(
                f"sources must be a non-empty selection of {list(SOURCES)}, got {list(self.sources)}"
            )
        if not self.include <= set(INCLUDABLE):
            raise ManifestError(
                f"include must be a selection of {list(INCLUDABLE)}, got {sorted(self.include)}"
            )
        if not all(isinstance(tag, str) and tag for tag in self.exclude_tags):
            raise ManifestError(
                f"exclude_tags must be non-empty strings, got {list(self.exclude_tags)}"
            )
        if not isinstance(self.allow_family_repeats, bool):
            raise ManifestError("allow_family_repeats must be true or false")
        if self.constraints is not None:
            try:
                rules = parse_rules(self.constraints)
            except RuleError as problem:
                raise ManifestError(f"constraints: {problem}") from None
            object.__setattr__(self, "constraints", rules)
        if self.wind_speed_profile not in SPEED_PROFILES:
            raise ManifestError(
                f"wind_speed_profile must be one of {', '.join(SPEED_PROFILES)}, "
                f"got {self.wind_speed_profile!r}"
            )
        if self.wind_direction_profile not in DIRECTION_PROFILES:
            raise ManifestError(
                f"wind_direction_profile must be one of {', '.join(DIRECTION_PROFILES)}, "
                f"got {self.wind_direction_profile!r}"
            )
        if self.music != RANDOM and self.music not in TRACKS:
            raise ManifestError(
                f"music must be {RANDOM!r} or one of {', '.join(TRACKS)}, got {self.music!r}"
            )
        _check_mercy(self.mercy_point)
        if not isinstance(self.clubs, ClubRules):
            raise ManifestError(f"clubs must be ClubRules, got {self.clubs!r}")

    @property
    def rules(self) -> tuple[Rule, ...]:
        """The rules the settings ask for: their constraints, or the default course's."""
        if self.constraints is None:
            return standard_rules(self.par)
        return self.constraints

    @property
    def experts_per_nine(self) -> int | None:
        """The cap the rules' `expert_cap` sets, the lowest if there are several. None
        when no rule of that name is among them."""
        caps = [
            rule.to_json()["per_nine"] for rule in self.rules if rule.name == EXPERT_CAP
        ]
        return min(caps, default=None)

    def to_json(self) -> dict:
        return {
            "prng_seed": self.prng_seed,
            "par": self.par,
            "sources": [source for source in SOURCES if source in self.sources],
            "include": [kind for kind in INCLUDABLE if kind in self.include],
            "exclude_tags": sorted(self.exclude_tags),
            "allow_family_repeats": self.allow_family_repeats,
            "constraints": None
            if self.constraints is None
            else [rule.to_json() for rule in self.constraints],
            "wind_speed_profile": self.wind_speed_profile,
            "wind_direction_profile": self.wind_direction_profile,
            "music": self.music,
            "mercy_point": self.mercy_point,
            "clubs": self.clubs.to_json(),
        }

    @classmethod
    def from_json(cls, data: object) -> "Settings":
        data = _fields(data, _SETTINGS_KEYS, "settings")
        constraints = data["constraints"]
        if constraints is not None and not isinstance(constraints, list):
            raise ManifestError(
                f"constraints must be null or a list of rules, got {constraints!r}"
            )
        return cls(
            prng_seed=data["prng_seed"],
            par=data["par"],
            sources=frozenset(_strings(data["sources"], "sources")),
            include=frozenset(_strings(data["include"], "include")),
            exclude_tags=frozenset(_strings(data["exclude_tags"], "exclude_tags")),
            allow_family_repeats=data["allow_family_repeats"],
            constraints=None if constraints is None else tuple(constraints),
            wind_speed_profile=data["wind_speed_profile"],
            wind_direction_profile=data["wind_direction_profile"],
            music=data["music"],
            mercy_point=data["mercy_point"],
            clubs=ClubRules.from_json(data["clubs"]),
        )


@dataclass(frozen=True)
class Slot:
    """One hole of the course.

    `wind_seed` is the 16-bit state the ROM's own RNG starts the hole from
    (docs/seeded_wind.md), not a PRNG seed for generation. `transforms` names hole
    transforms (`golf/randomizer/transforms.py`), applied to the hole in order.

    `wind_direction` and `wind_speed` are the hole's wind anchors (docs/wind.md). Left
    as None, each becomes the anchor `wind_seed` deals, which is the vanilla wind.
    """

    id: HoleId
    par: int
    wind_seed: int
    transforms: tuple[str, ...] = ()
    wind_direction: int | None = None
    wind_speed: int | None = None

    def __post_init__(self):
        if not isinstance(self.id, HoleId):
            raise ManifestError(f"slot id must be a HoleId, got {self.id!r}")
        if not _is_int(self.par) or self.par not in HOLE_PARS:
            raise ManifestError(
                f"{self.id}: par must be one of {list(HOLE_PARS)}, got {self.par!r}"
            )
        _check_wind(self)
        object.__setattr__(self, "transforms", tuple(self.transforms))
        for name in self.transforms:
            if not isinstance(name, str):
                raise ManifestError(
                    f"{self.id}: transform must be a string, got {name!r}"
                )
            try:
                parse_transform(name)
            except TransformError as problem:
                raise ManifestError(f"{self.id}: {problem}") from None

    @property
    def dealt_wind(self) -> tuple[int, int]:
        """The (direction, speed) anchors the hole's wind seed deals with no table."""
        return dealt_wind(self.wind_seed)

    @property
    def wind(self) -> tuple[int, int]:
        """The hole's (direction, speed) anchors."""
        assert self.wind_direction is not None and self.wind_speed is not None
        return self.wind_direction, self.wind_speed

    def to_json(self) -> dict:
        return {
            "id": str(self.id),
            "par": self.par,
            "transforms": list(self.transforms),
            "wind_seed": self.wind_seed,
            "wind_direction": self.wind_direction,
            "wind_speed": self.wind_speed,
        }

    @classmethod
    def from_json(cls, data: object) -> "Slot":
        data = _fields(data, _SLOT_KEYS, "hole")
        hole_id = _hole_id(data["id"])
        return cls(
            hole_id,
            data["par"],
            data["wind_seed"],
            # the registry check is the constructor's
            _transform_names(hole_id, data["transforms"], registered=False),
            data["wind_direction"],
            data["wind_speed"],
        )


@dataclass(frozen=True)
class Course:
    """The concrete course: what a build reads."""

    holes: tuple[Slot, ...]
    music: str
    mercy_point: int | None
    clubs: ClubRules
    #: shown on the title menus, joined as the scorecard title, and on the seed page
    magic_words: tuple[str, ...]
    #: marks a save as this seed's; a save holding any other magic is rebuilt at boot
    sram_magic: int

    def __post_init__(self):
        object.__setattr__(self, "holes", tuple(self.holes))
        if len(self.holes) != HOLE_COUNT or not all(
            isinstance(slot, Slot) for slot in self.holes
        ):
            raise ManifestError(
                f"a course has {HOLE_COUNT} holes, got {len(self.holes)}"
            )
        ids = [slot.id for slot in self.holes]
        repeated = sorted({hole_id for hole_id in ids if ids.count(hole_id) > 1})
        if repeated:
            raise ManifestError(
                f"holes appear more than once: {', '.join(map(str, repeated))}"
            )
        if self.music not in TRACKS:
            raise ManifestError(
                f"course music must be one of {', '.join(TRACKS)}, got {self.music!r}"
            )
        _check_mercy(self.mercy_point)
        if not isinstance(self.clubs, ClubRules):
            raise ManifestError(f"clubs must be ClubRules, got {self.clubs!r}")
        words = tuple(self.magic_words)
        try:
            checked = check_magic_words(words)
        except MagicWordsError as problem:
            raise ManifestError(f"magic_words: {problem}") from None
        if checked != words:
            raise ManifestError(f"magic_words must be uppercase, got {list(words)}")
        object.__setattr__(self, "magic_words", words)
        check_sram_magic(self.sram_magic)

    @property
    def layout(self) -> tuple[int, ...]:
        return tuple(slot.par for slot in self.holes)

    @property
    def par(self) -> int:
        return sum(self.layout)

    def to_json(self) -> dict:
        return {
            "holes": [slot.to_json() for slot in self.holes],
            "music": self.music,
            "mercy_point": self.mercy_point,
            "clubs": self.clubs.to_json(),
            "magic_words": list(self.magic_words),
            "sram_magic": self.sram_magic,
        }

    @classmethod
    def from_json(cls, data: object) -> "Course":
        data = _fields(
            data,
            ("holes", "music", "mercy_point", "clubs", "magic_words", "sram_magic"),
            "course",
        )
        if not isinstance(data["holes"], list):
            raise ManifestError(f"course holes must be a list, got {data['holes']!r}")
        if not isinstance(data["magic_words"], list):
            raise ManifestError(
                f"magic_words must be a list, got {data['magic_words']!r}"
            )
        return cls(
            holes=tuple(Slot.from_json(slot) for slot in data["holes"]),
            music=data["music"],
            mercy_point=data["mercy_point"],
            clubs=ClubRules.from_json(data["clubs"]),
            magic_words=tuple(data["magic_words"]),
            sram_magic=data["sram_magic"],
        )


_MANIFEST_KEYS = (
    "schema",
    "generator_version",
    "build_version",
    "finish_abi_version",
    "catalog_version",
    "curation_stamp",
    "settings",
    "course",
)
_VERSION_FIELDS = (
    "generator_version",
    "build_version",
    "finish_abi_version",
    "catalog_version",
)


def _check_versions(manifest: "Manifest | StoredManifest") -> None:
    for name in _VERSION_FIELDS:
        value = getattr(manifest, name)
        if not _is_int(value) or value < 1:
            raise ManifestError(f"{name} must be a positive integer, got {value!r}")
    if not isinstance(manifest.curation_stamp, str) or not manifest.curation_stamp:
        raise ManifestError("curation_stamp must be a non-empty string")


@dataclass(frozen=True)
class Manifest:
    """A manifest of this release's schema, as generation makes it and a build reads it."""

    schema: int
    generator_version: int
    #: the unfinished-ROM recipe this manifest requires
    build_version: int
    #: the interface the stored unfinished artifact exposes to its finisher
    finish_abi_version: int
    catalog_version: int
    curation_stamp: str
    settings: Settings
    course: Course

    def __post_init__(self):
        if self.schema != SCHEMA:
            raise ManifestError(
                f"manifest schema {self.schema!r} is not this release's, {SCHEMA}; a "
                "stored seed's manifest is read as a StoredManifest"
            )
        _check_versions(self)
        if self.settings.prng_seed is None:
            raise ManifestError(
                "a manifest's settings record the prng_seed generation used"
            )

    def to_json(self) -> dict:
        return {
            "schema": self.schema,
            "generator_version": self.generator_version,
            "build_version": self.build_version,
            "finish_abi_version": self.finish_abi_version,
            "catalog_version": self.catalog_version,
            "curation_stamp": self.curation_stamp,
            "settings": self.settings.to_json(),
            "course": self.course.to_json(),
        }

    @classmethod
    def from_json(cls, data: object) -> "Manifest":
        schema = data.get("schema") if isinstance(data, dict) else None
        if schema != SCHEMA:
            raise ManifestError(
                f"manifest schema {schema!r} is not this release's, {SCHEMA}; a "
                "stored seed's manifest is read as a StoredManifest"
            )
        data = _fields(data, _MANIFEST_KEYS, "manifest")
        return cls(
            schema=data["schema"],
            generator_version=data["generator_version"],
            build_version=data["build_version"],
            finish_abi_version=data["finish_abi_version"],
            catalog_version=data["catalog_version"],
            curation_stamp=data["curation_stamp"],
            settings=Settings.from_json(data["settings"]),
            course=Course.from_json(data["course"]),
        )


# -- Stored manifests -----------------------------------------------------------------------
#
# A stored seed's manifest was issued by the release that made the seed, in that release's
# schema, and is never built again (ADR 0024). It is read for what showing and finishing
# the seed need, tolerant of its shape:
#
# - a field this release does not read is ignored, whatever schema added it;
# - a field some schema lacked takes the value that schema's seeds had: no `build_version`
#   or `finish_abi_version` is 1, no `draw_rule` or `constraints` is a uniform draw, no
#   wind profile is `vanilla`, no `include` is none, and a hole without wind anchors has
#   the ones its wind seed deals;
# - schema 5's `constraints` stand in for the `draw_rule` of schemas 3 and 4: the draw
#   rule a seed shows is the expert cap its rules set, or `uniform` without one;
# - a field every schema has must be present;
# - a value that is present must be legal, and the finish inputs (`finish_abi_version`,
#   `sram_magic` and the club rules) are checked as strictly as `Manifest` checks them.
#
# Names only shown are not checked against what this release knows: a draw rule, a course
# rule, a wind profile, a music slug or a transform name can be retired, and its stored seeds show it
# by name. A transform version can leave `TRANSFORMS` once nothing generates it, since a
# stored seed's holes are stored as built. The names a seed is finished or joined by are
# permanent: ROM ids, club labels and hole ids (docs/manifest.md, **What a stored seed
# depends on**).


def _field(data: dict, key: str, what: str) -> Any:
    if key not in data:
        raise ManifestError(f"{what}: missing field {key!r}")
    return data[key]


def _object(data: object, what: str) -> dict:
    if not isinstance(data, dict):
        raise ManifestError(f"{what} must be an object, got {data!r}")
    return data


@dataclass(frozen=True)
class StoredDrawRule:
    """A stored seed's draw rule by name, which this release may no longer implement."""

    rule: str = UNIFORM
    #: the most expert holes on a nine under `expert_cap`; None under any other rule
    per_nine: int | None = None

    def to_json(self) -> dict:
        if self.per_nine is None:
            return {"rule": self.rule}
        return {"rule": self.rule, "per_nine": self.per_nine}

    @classmethod
    def from_json(cls, data: object) -> "StoredDrawRule":
        data = _object(data, "draw_rule")
        rule = _field(data, "rule", "draw_rule")
        if not isinstance(rule, str) or not rule:
            raise ManifestError(f"draw rule must be a non-empty string, got {rule!r}")
        per_nine = data.get("per_nine")
        if per_nine is not None and not _is_int(per_nine):
            raise ManifestError(
                f"draw rule per_nine must be an integer, got {per_nine!r}"
            )
        return cls(rule, per_nine)

    @classmethod
    def of_constraints(
        cls, constraints: "tuple[dict[str, Any], ...] | None"
    ) -> "StoredDrawRule":
        """The draw rule a schema 5 seed's constraints amount to: the lowest expert cap
        among them, the default course's when they are null, or `uniform` without one."""
        if constraints is None:
            return cls(EXPERT_CAP, DEFAULT_EXPERTS_PER_NINE)
        caps: list[int] = []
        for rule in constraints:
            if rule["rule"] != EXPERT_CAP:
                continue
            cap = rule.get("per_nine")
            if not _is_int(cap):
                raise ManifestError(
                    f"expert_cap per_nine must be an integer, got {cap!r}"
                )
            caps.append(cap)
        return cls(EXPERT_CAP, min(caps)) if caps else cls()


def _stored_constraints(data: object) -> tuple[dict[str, Any], ...] | None:
    """A stored seed's constraints: null, or a list of objects that each name a rule."""
    if data is None:
        return None
    if not isinstance(data, list):
        raise ManifestError(
            f"constraints must be null or a list of rules, got {data!r}"
        )
    for rule in data:
        name = _field(_object(rule, "constraint"), "rule", "constraint")
        if not isinstance(name, str) or not name:
            raise ManifestError(
                f"a rule's name must be a non-empty string, got {name!r}"
            )
    return tuple(data)


@dataclass(frozen=True)
class StoredSettings:
    """What a stored seed's page shows of the settings it was generated from."""

    prng_seed: str | None
    par: int
    sources: frozenset[str]
    include: frozenset[str] = frozenset()
    exclude_tags: frozenset[str] = frozenset()
    allow_family_repeats: bool = False
    #: the course's expert cap as a draw rule, from `draw_rule` or `constraints`
    draw_rule: StoredDrawRule = StoredDrawRule()
    #: a schema 5 seed's course rules as issued; None is the default course, or a schema
    #: without them
    constraints: tuple[dict[str, Any], ...] | None = None
    wind_speed_profile: str = VANILLA
    wind_direction_profile: str = VANILLA

    @classmethod
    def from_json(cls, data: object) -> "StoredSettings":
        data = _object(data, "settings")
        prng_seed = _field(data, "prng_seed", "settings")
        if prng_seed is not None and not isinstance(prng_seed, str):
            raise ManifestError(f"prng_seed must be a string, got {prng_seed!r}")
        par = _field(data, "par", "settings")
        if not _is_int(par):
            raise ManifestError(f"par must be an integer, got {par!r}")
        allow = data.get("allow_family_repeats", False)
        if not isinstance(allow, bool):
            raise ManifestError("allow_family_repeats must be true or false")
        speed = data.get("wind_speed_profile", VANILLA)
        direction = data.get("wind_direction_profile", VANILLA)
        for name, value in (("speed", speed), ("direction", direction)):
            if not isinstance(value, str):
                raise ManifestError(
                    f"wind_{name}_profile must be a string, got {value!r}"
                )
        constraints = _stored_constraints(data.get("constraints"))
        if "constraints" in data:
            draw_rule = StoredDrawRule.of_constraints(constraints)
        elif "draw_rule" in data:
            draw_rule = StoredDrawRule.from_json(data["draw_rule"])
        else:
            draw_rule = StoredDrawRule()
        return cls(
            prng_seed=prng_seed,
            par=par,
            sources=frozenset(_strings(_field(data, "sources", "settings"), "sources")),
            include=frozenset(_strings(data.get("include", []), "include")),
            exclude_tags=frozenset(
                _strings(data.get("exclude_tags", []), "exclude_tags")
            ),
            allow_family_repeats=allow,
            draw_rule=draw_rule,
            constraints=constraints,
            wind_speed_profile=speed,
            wind_direction_profile=direction,
        )


@dataclass(frozen=True)
class StoredSlot:
    """One hole of a stored seed's course."""

    id: HoleId
    par: int
    wind_seed: int
    transforms: tuple[str, ...]
    #: the hole's wind anchors; a schema without them gets the ones its wind seed deals
    wind_direction: int | None
    wind_speed: int | None

    @property
    def wind(self) -> tuple[int, int]:
        """The hole's (direction, speed) anchors."""
        assert self.wind_direction is not None and self.wind_speed is not None
        return self.wind_direction, self.wind_speed

    @classmethod
    def from_json(cls, data: object) -> "StoredSlot":
        data = _object(data, "hole")
        hole_id = _hole_id(_field(data, "id", "hole"))
        par = _field(data, "par", "hole")
        if not _is_int(par) or par not in HOLE_PARS:
            raise ManifestError(
                f"{hole_id}: par must be one of {list(HOLE_PARS)}, got {par!r}"
            )
        slot = cls(
            hole_id,
            par,
            _field(data, "wind_seed", "hole"),
            _transform_names(hole_id, data.get("transforms", []), registered=False),
            data.get("wind_direction"),
            data.get("wind_speed"),
        )
        _check_wind(slot)
        return slot


@dataclass(frozen=True)
class StoredCourse:
    """A stored seed's concrete course, as its pages and its finisher read it."""

    holes: tuple[StoredSlot, ...]
    music: str
    mercy_point: int | None
    clubs: ClubRules
    magic_words: tuple[str, ...]
    sram_magic: int

    @property
    def layout(self) -> tuple[int, ...]:
        return tuple(slot.par for slot in self.holes)

    @property
    def par(self) -> int:
        return sum(self.layout)

    @classmethod
    def from_json(cls, data: object) -> "StoredCourse":
        data = _object(data, "course")
        holes = _field(data, "holes", "course")
        if not isinstance(holes, list):
            raise ManifestError(f"course holes must be a list, got {holes!r}")
        music = _field(data, "music", "course")
        if not isinstance(music, str):
            raise ManifestError(f"course music must be a string, got {music!r}")
        mercy_point = _field(data, "mercy_point", "course")
        _check_mercy(mercy_point)
        words = _field(data, "magic_words", "course")
        if not isinstance(words, list) or not all(isinstance(w, str) for w in words):
            raise ManifestError(f"magic_words must be a list of strings, got {words!r}")
        sram_magic = _field(data, "sram_magic", "course")
        check_sram_magic(sram_magic)
        return cls(
            holes=tuple(StoredSlot.from_json(slot) for slot in holes),
            music=music,
            mercy_point=mercy_point,
            clubs=ClubRules.from_json(_field(data, "clubs", "course")),
            magic_words=tuple(words),
            sram_magic=sram_magic,
        )


@dataclass(frozen=True)
class StoredManifest:
    """A stored seed's manifest, whatever schema it was issued in."""

    schema: int
    generator_version: int
    build_version: int
    finish_abi_version: int
    catalog_version: int
    curation_stamp: str
    settings: StoredSettings
    course: StoredCourse

    @classmethod
    def from_json(cls, data: object) -> "StoredManifest":
        data = _object(data, "manifest")
        schema = _field(data, "schema", "manifest")
        if not _is_int(schema):
            raise ManifestError(f"schema must be an integer, got {schema!r}")
        manifest = cls(
            schema=schema,
            generator_version=_field(data, "generator_version", "manifest"),
            build_version=data.get("build_version", LEGACY_BUILD_VERSION),
            finish_abi_version=data.get(
                "finish_abi_version", LEGACY_FINISH_ABI_VERSION
            ),
            catalog_version=_field(data, "catalog_version", "manifest"),
            curation_stamp=_field(data, "curation_stamp", "manifest"),
            settings=StoredSettings.from_json(_field(data, "settings", "manifest")),
            course=StoredCourse.from_json(_field(data, "course", "manifest")),
        )
        _check_versions(manifest)
        return manifest


def required_roms(
    manifest: Manifest | StoredManifest, catalog: Catalog
) -> tuple[str, ...]:
    """The vanilla ROMs a seed's ROM is built from: the US ROM, plus any its holes or music come from."""
    needed: set[str | None] = {US_ROM, track(manifest.course.music).rom}
    for slot in manifest.course.holes:
        needed.add(catalog[slot.id].rom)
    return tuple(rom for rom in SOURCES if rom in needed)
