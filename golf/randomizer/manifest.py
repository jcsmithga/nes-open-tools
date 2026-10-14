"""
The manifest: a randomized seed as JSON, holding everything that decides its unfinished ROM.

Three parts:

- The version fields: the manifest `schema`, generator, unfinished-build and finish-ABI
  versions, catalog version and the curation stamp that produced it.
- `settings`: every input to generation, the PRNG seed and the draw rule included, as a
  record of what the seed was made from. Nothing regenerates a stored seed (ADR 0024).
- `course`: concrete values, and the only part the recipe reads after version checks.
  Hole ids, never filters; a music slug, never "random".

The patches every seed gets are implied by `build_version`; the interface their unfinished
artifact exposes to personalization is `finish_abi_version`. Loading is strict: a missing
or unknown field is an error. See docs/manifest.md and docs/patch_stack.md.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeGuard

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
from .transforms import TransformError, parse_transform
from .wind import DIRECTION_PROFILES, SPEED_PROFILES, VANILLA
from .words import MagicWordsError, check_magic_words

SCHEMA = 4
#: schemas this code still reads whose settings have no `draw_rule` or wind profiles,
#: and whose holes have no wind anchors
LEGACY_SCHEMAS = (1, 2)
#: schemas this code still reads whose settings have no `include`: their pools held
#: vanilla holes only
VANILLA_ONLY_SCHEMAS = (*LEGACY_SCHEMAS, 3)
READABLE_SCHEMAS = (*VANILLA_ONLY_SCHEMAS, SCHEMA)
LEGACY_SCHEMA = 1
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
UNIFORM = "uniform"
EXPERT_CAP = "expert_cap"
DRAW_RULES = (UNIFORM, EXPERT_CAP)
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


@dataclass(frozen=True)
class DrawRule:
    """How generation draws a course's holes from the pool.

    - `uniform`: every family in the pool is as likely as any other.
    - `expert_cap`: at most `per_nine` expert holes on each nine, the holes curation tags
      `expert`. 0 leaves them out of the course.
    """

    rule: str = UNIFORM
    #: the most expert holes on a nine under `expert_cap`; None under any other rule
    per_nine: int | None = None

    def __post_init__(self):
        if self.rule not in DRAW_RULES:
            raise ManifestError(
                f"draw rule must be one of {list(DRAW_RULES)}, got {self.rule!r}"
            )
        if self.rule == EXPERT_CAP:
            if not _is_int(self.per_nine) or not 0 <= self.per_nine <= NINE:
                raise ManifestError(
                    f"expert_cap per_nine must be 0-{NINE}, got {self.per_nine!r}"
                )
        elif self.per_nine is not None:
            raise ManifestError(f"draw rule {self.rule!r} takes no per_nine")

    @classmethod
    def expert_cap(cls, per_nine: int) -> "DrawRule":
        return cls(EXPERT_CAP, per_nine)

    def to_json(self) -> dict:
        if self.rule == EXPERT_CAP:
            return {"rule": self.rule, "per_nine": self.per_nine}
        return {"rule": self.rule}

    @classmethod
    def from_json(cls, data: object) -> "DrawRule":
        rule = data.get("rule") if isinstance(data, dict) else None
        keys = ("rule", "per_nine") if rule == EXPERT_CAP else ("rule",)
        data = _fields(data, keys, "draw_rule")
        return cls(data["rule"], data.get("per_nine"))


DEFAULT_DRAW_RULE = DrawRule.expert_cap(1)

_SLOT_KEYS = ("id", "par", "transforms", "wind_seed", "wind_direction", "wind_speed")
_LEGACY_SLOT_KEYS = ("id", "par", "transforms", "wind_seed")
_LEGACY_SETTINGS_KEYS = (
    "prng_seed",
    "par",
    "sources",
    "exclude_tags",
    "allow_family_repeats",
    "music",
    "mercy_point",
    "clubs",
)
_SETTINGS_KEYS = (
    "prng_seed",
    "par",
    "sources",
    "include",
    "exclude_tags",
    "allow_family_repeats",
    "draw_rule",
    "wind_speed_profile",
    "wind_direction_profile",
    "music",
    "mercy_point",
    "clubs",
)
#: the settings a schema 4 manifest added
_SCHEMA_4_SETTINGS = ("include",)
_SCHEMA_3_SETTINGS_KEYS = tuple(
    key for key in _SETTINGS_KEYS if key not in _SCHEMA_4_SETTINGS
)
#: the settings a schema 1 or 2 manifest doesn't have
_SCHEMA_3_SETTINGS = tuple(
    key for key in _SETTINGS_KEYS if key not in _LEGACY_SETTINGS_KEYS
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
    draw_rule: DrawRule = DEFAULT_DRAW_RULE
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
        if not _is_int(self.par) or self.par not in COUNTS:
            raise ManifestError(
                f"par must be one of {sorted(COUNTS)}, got {self.par!r}"
            )
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
        if not isinstance(self.draw_rule, DrawRule):
            raise ManifestError(f"draw_rule must be a DrawRule, got {self.draw_rule!r}")
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

    def to_json(self) -> dict:
        return {
            "prng_seed": self.prng_seed,
            "par": self.par,
            "sources": [source for source in SOURCES if source in self.sources],
            "include": [kind for kind in INCLUDABLE if kind in self.include],
            "exclude_tags": sorted(self.exclude_tags),
            "allow_family_repeats": self.allow_family_repeats,
            "draw_rule": self.draw_rule.to_json(),
            "wind_speed_profile": self.wind_speed_profile,
            "wind_direction_profile": self.wind_direction_profile,
            "music": self.music,
            "mercy_point": self.mercy_point,
            "clubs": self.clubs.to_json(),
        }

    @classmethod
    def from_json(cls, data: object, schema: int = SCHEMA) -> "Settings":
        """Settings from a manifest of this schema.

        A schema 1 or 2 manifest's have no `draw_rule` or wind profiles: every seed then
        was drawn uniformly, with the wind its seeds dealt. A schema 3 manifest's or older
        have no `include`: every pool then held vanilla holes only."""
        legacy = schema in LEGACY_SCHEMAS
        vanilla_only = schema in VANILLA_ONLY_SCHEMAS
        keys = (
            _LEGACY_SETTINGS_KEYS
            if legacy
            else _SCHEMA_3_SETTINGS_KEYS
            if vanilla_only
            else _SETTINGS_KEYS
        )
        data = _fields(data, keys, "settings")
        return cls(
            prng_seed=data["prng_seed"],
            par=data["par"],
            sources=frozenset(_strings(data["sources"], "sources")),
            include=frozenset()
            if vanilla_only
            else frozenset(_strings(data["include"], "include")),
            exclude_tags=frozenset(_strings(data["exclude_tags"], "exclude_tags")),
            allow_family_repeats=data["allow_family_repeats"],
            draw_rule=DrawRule() if legacy else DrawRule.from_json(data["draw_rule"]),
            wind_speed_profile=VANILLA if legacy else data["wind_speed_profile"],
            wind_direction_profile=VANILLA
            if legacy
            else data["wind_direction_profile"],
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
    as None, each becomes the anchor `wind_seed` deals, which is the vanilla wind and
    what every hole of a schema 1 or 2 manifest has.
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
        if not _is_int(self.wind_seed) or self.wind_seed not in WIND_SEEDS:
            raise ManifestError(
                f"{self.id}: wind_seed must be 0-65535, got {self.wind_seed!r}"
            )
        dealt = self.dealt_wind
        if self.wind_direction is None:
            object.__setattr__(self, "wind_direction", dealt[0])
        if self.wind_speed is None:
            object.__setattr__(self, "wind_speed", dealt[1])
        if (
            not _is_int(self.wind_direction)
            or self.wind_direction not in WIND_DIRECTIONS
        ):
            raise ManifestError(
                f"{self.id}: wind_direction must be a multiple of 16 from 0 to 240, "
                f"got {self.wind_direction!r}"
            )
        if not _is_int(self.wind_speed) or self.wind_speed not in WIND_SPEEDS:
            raise ManifestError(
                f"{self.id}: wind_speed must be 0-10, got {self.wind_speed!r}"
            )
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
        forecast = predict_hole(self.wind_seed, swings=0)
        return forecast.direction_anchor, forecast.speed_anchor

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
    def from_json(cls, data: object, legacy: bool = False) -> "Slot":
        """A hole from a manifest. `legacy` reads a schema 1 or 2 manifest's, which has
        no wind anchors: its holes played the wind their seeds dealt."""
        data = _fields(data, _LEGACY_SLOT_KEYS if legacy else _SLOT_KEYS, "hole")
        text = data["id"]
        try:
            hole_id = HoleId.parse(text)
        except CatalogError as problem:
            raise ManifestError(str(problem)) from None
        if str(hole_id) != text:
            raise ManifestError(
                f"hole id {text!r} is not canonical; write it as {hole_id}"
            )
        transforms = data["transforms"]
        if not isinstance(transforms, list):
            raise ManifestError(
                f"{hole_id}: transforms must be a list, got {transforms!r}"
            )
        return cls(
            hole_id,
            data["par"],
            data["wind_seed"],
            tuple(transforms),
            None if legacy else data["wind_direction"],
            None if legacy else data["wind_speed"],
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
    def from_json(cls, data: object, legacy: bool = False) -> "Course":
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
            holes=tuple(Slot.from_json(slot, legacy) for slot in data["holes"]),
            music=data["music"],
            mercy_point=data["mercy_point"],
            clubs=ClubRules.from_json(data["clubs"]),
            magic_words=tuple(data["magic_words"]),
            sram_magic=data["sram_magic"],
        )


_READABLE = ", ".join(map(str, READABLE_SCHEMAS))
_MANIFEST_KEYS_V1 = (
    "schema",
    "generator_version",
    "catalog_version",
    "curation_stamp",
    "settings",
    "course",
)
_MANIFEST_KEYS_V2 = (
    "schema",
    "generator_version",
    "build_version",
    "finish_abi_version",
    "catalog_version",
    "curation_stamp",
    "settings",
    "course",
)


@dataclass(frozen=True)
class Manifest:
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
        if self.schema not in READABLE_SCHEMAS:
            raise ManifestError(
                f"unsupported manifest schema {self.schema!r}; this code reads schemas "
                f"{_READABLE}"
            )
        if self.schema in VANILLA_ONLY_SCHEMAS and self.settings.include:
            raise ManifestError(
                f"manifest schema {self.schema} has no include; its pool held vanilla "
                f"holes only, got {sorted(self.settings.include)}"
            )
        if self.schema in LEGACY_SCHEMAS:
            if self.settings.draw_rule != DrawRule():
                raise ManifestError(
                    f"manifest schema {self.schema} has no draw_rule; its seeds were "
                    f"drawn uniformly, got {self.settings.draw_rule.to_json()}"
                )
            profiles = (
                self.settings.wind_speed_profile,
                self.settings.wind_direction_profile,
            )
            if profiles != (VANILLA, VANILLA) or any(
                slot.wind != slot.dealt_wind for slot in self.course.holes
            ):
                raise ManifestError(
                    f"manifest schema {self.schema} has no wind profiles or anchors; its "
                    f"holes play the wind their seeds deal"
                )
        for name in (
            "generator_version",
            "build_version",
            "finish_abi_version",
            "catalog_version",
        ):
            value = getattr(self, name)
            if not _is_int(value) or value < 1:
                raise ManifestError(f"{name} must be a positive integer, got {value!r}")
        if self.schema == LEGACY_SCHEMA and self.build_version != LEGACY_BUILD_VERSION:
            raise ManifestError(
                f"manifest schema {LEGACY_SCHEMA} implies build_version "
                f"{LEGACY_BUILD_VERSION}, got {self.build_version!r}"
            )
        if (
            self.schema == LEGACY_SCHEMA
            and self.finish_abi_version != LEGACY_FINISH_ABI_VERSION
        ):
            raise ManifestError(
                f"manifest schema {LEGACY_SCHEMA} implies finish_abi_version "
                f"{LEGACY_FINISH_ABI_VERSION}, got {self.finish_abi_version!r}"
            )
        if not isinstance(self.curation_stamp, str) or not self.curation_stamp:
            raise ManifestError("curation_stamp must be a non-empty string")
        if self.settings.prng_seed is None:
            raise ManifestError(
                "a manifest's settings record the prng_seed generation used"
            )

    def to_json(self) -> dict:
        data = {
            "schema": self.schema,
            "generator_version": self.generator_version,
        }
        if self.schema != LEGACY_SCHEMA:
            data["build_version"] = self.build_version
            data["finish_abi_version"] = self.finish_abi_version
        settings = self.settings.to_json()
        course = self.course.to_json()
        if self.schema in VANILLA_ONLY_SCHEMAS:
            for key in _SCHEMA_4_SETTINGS:
                del settings[key]
        if self.schema in LEGACY_SCHEMAS:
            for key in _SCHEMA_3_SETTINGS:
                if key in settings:
                    del settings[key]
            for hole in course["holes"]:
                del hole["wind_direction"], hole["wind_speed"]
        return data | {
            "catalog_version": self.catalog_version,
            "curation_stamp": self.curation_stamp,
            "settings": settings,
            "course": course,
        }

    @classmethod
    def from_json(cls, data: object) -> "Manifest":
        schema = data.get("schema") if isinstance(data, dict) else None
        if schema not in READABLE_SCHEMAS:
            raise ManifestError(
                f"unsupported manifest schema {schema!r}; this code reads schemas "
                f"{_READABLE}"
            )
        keys = _MANIFEST_KEYS_V1 if schema == LEGACY_SCHEMA else _MANIFEST_KEYS_V2
        data = _fields(data, keys, "manifest")
        return cls(
            schema=data["schema"],
            generator_version=data["generator_version"],
            build_version=(
                LEGACY_BUILD_VERSION
                if schema == LEGACY_SCHEMA
                else data["build_version"]
            ),
            finish_abi_version=(
                LEGACY_FINISH_ABI_VERSION
                if schema == LEGACY_SCHEMA
                else data["finish_abi_version"]
            ),
            catalog_version=data["catalog_version"],
            curation_stamp=data["curation_stamp"],
            settings=Settings.from_json(data["settings"], schema),
            course=Course.from_json(data["course"], legacy=schema in LEGACY_SCHEMAS),
        )


def required_roms(manifest: Manifest, catalog: Catalog) -> tuple[str, ...]:
    """The vanilla ROMs a seed's ROM is built from: the US ROM, plus any its holes or music come from."""
    needed: set[str | None] = {US_ROM, track(manifest.course.music).rom}
    for slot in manifest.course.holes:
        needed.add(catalog[slot.id].rom)
    return tuple(rom for rom in SOURCES if rom in needed)
