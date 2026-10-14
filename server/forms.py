"""The site's forms: what they show, what a submission holds, and what it becomes.

The generate form becomes a request (ADR 0016), which the route resolves into `Settings`;
the seed page's download form becomes `PlayerOptions` and the ROM hashes a download is
gated on.

`FormState` is the form's values as strings and sets, the way a browser sends them and the
template renders them, so a refused submission comes back with the player's choices. A
state becomes a request through `request_from_state`, which raises `FormError` naming the
problem for the template to show. A download submission is a `DownloadState`, checked by
`check_rom_hashes` and turned into `PlayerOptions` by `player_options_from_state`.

`SavedSettings` is what the site remembers of a player's download choices, read leniently
from untrusted JSON. `fit` turns it into the download form a seed starts with, under the
seed's club rules and finish ABI, and `to_save` applies the saving rule to a download. See
`docs/planning/download_settings.md`.

The mercy point and excluded tags are not on the form: a seed from the site takes their
`Settings` defaults. The course is a mode (`MODE_CHOICES`), and for a vanilla course which
one (`COURSE_CHOICES`); the cap on expert holes is one select over `DRAW_RULE_CHOICES`, which
a vanilla course ignores, and so are its sources: it takes its own ROM's holes. The two
transform chances are a select each over `CHANCE_CHOICES`, and the two wind profiles a
select each over every profile (ADR 0025).
"""

import base64
import binascii
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from golf.core.patches.extended_sram_defaults import BallSpin, SwingSpeed
from golf.core.patches.sram_defaults import (
    BAG_SIZE,
    NAME_CHARS,
    NAME_LENGTH,
    VANILLA_CLUBS,
    VANILLA_NAME,
    Club,
    parse_club,
)
from golf.randomizer.build import FINISH_ABI_VERSION, BuildError, PlayerOptions
from golf.randomizer.manifest import (
    SOURCES,
    ClubRules,
    ManifestError,
    Settings,
    mode_rules,
)
from golf.randomizer.music import RANDOM, TRACKS
from golf.randomizer.request import resolve
from golf.randomizer.roms import vanilla_rom
from golf.randomizer.rules import MODES
from golf.randomizer.wind import DIRECTION_PROFILES, SPEED_PROFILES

VANILLA_COURSE = "vanilla_course"
#: the course modes, in the order the form lists them
MODE_CHOICES = MODES
#: the vanilla courses, by the slug their music and their holes' lineages share
COURSE_CHOICES = tuple(TRACKS)
#: each hole's chance of a transform, a whole percent, as the form offers them
CHANCE_CHOICES = ("0", "25", "50", "75", "100")
#: every club a rule can name: the putter is always allowed and never listed
RULE_CLUBS = tuple(club for club in Club if club != Club.PT)
MUSIC_CHOICES = (RANDOM, *TRACKS)
#: the caps on a nine's expert holes the form offers, by the value its `draw_rule` select
#: sends, in the order it lists them. None is no cap.
DRAW_RULE_CHOICES: dict[str, int | None] = {
    "experts_0": 0,
    "experts_1": 1,
    "experts_2": 2,
    "uniform": None,
}

#: FormError reasons, each shown by its own strings key in generate.html
NO_SOURCES = "no_sources"
REQUIRED_BAG_OVER_MAX = "required_bag_over_max"
REQUIRED_BAG_BANNED = "required_bag_banned"
INVALID = "invalid"

#: FormError reasons a download refuses with, each shown by its own strings key in download.js
INVALID_NAME = "invalid_name"
CLUBS_BANNED = "clubs_banned"
CLUBS_OVER_MAX = "clubs_over_max"
ROMS_MISSING = "roms_missing"

#: the download form's hash fields are this prefix and a catalog ROM id
ROM_HASH_PREFIX = "rom_"


class FormError(ValueError):
    def __init__(self, reason: str, **values: object):
        super().__init__(f"{reason}: {values}" if values else reason)
        self.reason = reason
        self.values = values


@dataclass
class FormState:
    sources: set[str]
    allow_family_repeats: bool
    music: str
    clubs_max: str
    #: one of `MODE_CHOICES`
    mode: str = "standard"
    #: one of `COURSE_CHOICES`, read only for a vanilla course
    course: str = COURSE_CHOICES[0]
    #: a key of `DRAW_RULE_CHOICES`
    draw_rule: str = ""
    #: each one of `CHANCE_CHOICES`
    mirror_chance: str = "0"
    hazard_chance: str = "0"
    #: one of `SPEED_PROFILES`
    wind_speed: str = ""
    #: one of `DIRECTION_PROFILES`
    wind_direction: str = ""
    banned: set[str] = field(default_factory=set)
    required_bag: set[str] = field(default_factory=set)

    @classmethod
    def default(cls) -> "FormState":
        settings = Settings()
        return cls(
            sources=set(settings.sources),
            allow_family_repeats=settings.allow_family_repeats,
            music=settings.music,
            clubs_max=str(settings.clubs.max),
            draw_rule=next(
                name
                for name, cap in DRAW_RULE_CHOICES.items()
                if cap == settings.experts_per_nine
            ),
            mirror_chance=str(settings.mirror_chance),
            hazard_chance=str(settings.hazard_chance),
            wind_speed=settings.wind_speed_profile,
            wind_direction=settings.wind_direction_profile,
        )

    @classmethod
    def from_form(cls, form) -> "FormState":
        """A submission's values, unchecked. `form` is Starlette's FormData, or anything with get and getlist."""

        def text(name: str) -> str:
            value = form.get(name)
            return value.strip() if isinstance(value, str) else ""

        def chosen(name: str) -> set[str]:
            return {value for value in form.getlist(name) if isinstance(value, str)}

        return cls(
            sources=chosen("sources"),
            allow_family_repeats=bool(text("allow_family_repeats")),
            music=text("music"),
            clubs_max=text("clubs_max"),
            mode=text("mode"),
            course=text("course"),
            draw_rule=text("draw_rule"),
            mirror_chance=text("mirror_chance"),
            hazard_chance=text("hazard_chance"),
            wind_speed=text("wind_speed"),
            wind_direction=text("wind_direction"),
            banned=chosen("banned"),
            required_bag=chosen("required_bag"),
        )

    def has_club_rules(self) -> bool:
        """Whether the club rules differ from the defaults."""
        return bool(
            self.banned
            or self.required_bag
            or self.clubs_max != str(Settings().clubs.max)
        )

    def to_pairs(self) -> list[tuple[str, str]]:
        """The state as the fields a browser would submit for it."""
        pairs = [("mode", self.mode), ("course", self.course)]
        pairs += [("sources", source) for source in SOURCES if source in self.sources]
        if self.allow_family_repeats:
            pairs.append(("allow_family_repeats", "on"))
        pairs += [
            ("draw_rule", self.draw_rule),
            ("mirror_chance", self.mirror_chance),
            ("hazard_chance", self.hazard_chance),
            ("wind_speed", self.wind_speed),
            ("wind_direction", self.wind_direction),
            ("music", self.music),
            ("clubs_max", self.clubs_max),
        ]
        pairs += [
            ("banned", club.label) for club in RULE_CLUBS if club.label in self.banned
        ]
        pairs += [
            ("required_bag", club.label)
            for club in RULE_CLUBS
            if club.label in self.required_bag
        ]
        return pairs


def _labels(clubs: Iterable[Club]) -> str:
    return " ".join(club.label for club in sorted(clubs))


def _rule_clubs(labels: set[str], name: str) -> frozenset[Club]:
    try:
        clubs = frozenset(parse_club(label) for label in labels)
    except ValueError:
        raise FormError(INVALID, field=name) from None
    if Club.PT in clubs:
        raise FormError(INVALID, field=name)
    return clubs


def request_from_state(state: FormState) -> dict:
    """The request a submission makes (ADR 0016): a settings request with no weighted
    field. Raises FormError for anything the form cannot use."""
    if state.mode not in MODE_CHOICES:
        raise FormError(INVALID, field="mode")
    vanilla = state.mode == VANILLA_COURSE
    if vanilla and state.course not in COURSE_CHOICES:
        raise FormError(INVALID, field="course")
    if not vanilla and not state.sources:
        raise FormError(NO_SOURCES)
    if not state.sources <= set(SOURCES):
        raise FormError(INVALID, field="sources")
    if state.draw_rule not in DRAW_RULE_CHOICES:
        raise FormError(INVALID, field="draw_rule")
    for name in ("mirror_chance", "hazard_chance"):
        if getattr(state, name) not in CHANCE_CHOICES:
            raise FormError(INVALID, field=name)
    if state.wind_speed not in SPEED_PROFILES:
        raise FormError(INVALID, field="wind_speed")
    if state.wind_direction not in DIRECTION_PROFILES:
        raise FormError(INVALID, field="wind_direction")
    if state.music not in MUSIC_CHOICES:
        raise FormError(INVALID, field="music")
    if state.clubs_max not in {str(count) for count in range(1, BAG_SIZE + 1)}:
        raise FormError(INVALID, field="clubs_max")

    clubs_max = int(state.clubs_max)
    banned = _rule_clubs(state.banned, "banned")
    required = _rule_clubs(state.required_bag, "required_bag")
    if required:
        bag = required | {Club.PT}
        if bag & banned:
            raise FormError(REQUIRED_BAG_BANNED, clubs=_labels(bag & banned))
        if len(bag) > clubs_max:
            raise FormError(REQUIRED_BAG_OVER_MAX, count=len(bag), max=clubs_max)

    if vanilla:
        mode = {"rule": VANILLA_COURSE, "course": state.course}
        constraints = mode_rules(mode, None)
        sources = [TRACKS[state.course].rom]
    else:
        constraints = mode_rules(state.mode, DRAW_RULE_CHOICES[state.draw_rule])
        sources = [source for source in SOURCES if source in state.sources]
    request = {
        "sources": sources,
        "allow_family_repeats": state.allow_family_repeats,
        "constraints": [rule.to_json() for rule in constraints],
        "wind_speed_profile": state.wind_speed,
        "wind_direction_profile": state.wind_direction,
        "mirror_chance": int(state.mirror_chance),
        "hazard_chance": int(state.hazard_chance),
        "music": state.music,
        "clubs": {
            "max": clubs_max,
            "banned": [club.label for club in sorted(banned)],
            "required_bag": None
            if not required
            else [club.label for club in sorted(required)],
        },
    }
    if not vanilla and state.mode == "par_3":
        # par 3s beyond the vanilla ones are the forward tees of longer holes
        request["include"] = ["derived"]
    return request


def settings_from_state(state: FormState, prng_seed: str | None = None) -> Settings:
    """The settings a submission's request resolves to. Raises FormError for anything
    the form cannot use."""
    request = request_from_state(state)
    try:
        return resolve(request, prng_seed)
    except (
        ManifestError
    ):  # pragma: no cover - every rule Settings checks is checked above
        raise FormError(INVALID, field="settings") from None


# -- Download ---------------------------------------------------------------------------------


#: the swing and putt speed choices, as the form and saved settings name them
SPEED_CHOICES = tuple(speed.name.lower() for speed in SwingSpeed)
#: the ball spin choices, as the form and saved settings name them
SPIN_CHOICES = tuple(spin.name.lower() for spin in BallSpin)
#: what swing, putt and spin are when the player has not chosen: vanilla, keep the last one
OFF = "off"


def writes_option_defaults(abi: int) -> bool:
    """Whether a seed of this finish ABI can write swing, putt and spin defaults."""
    return abi >= 2


@dataclass
class DownloadState:
    """The seed page's download form: the player's choices and the ROM hashes the script adds.

    Swing, putt and spin are named as in `SPEED_CHOICES` and `SPIN_CHOICES`. The form sends
    BGM as a hidden `off` followed by a checkbox's `on`, so a submission with no `bgm`
    field at all, from a page that predates it, keeps the music on.
    """

    player_name: str
    clubs: set[str]
    #: catalog ROM id -> the SHA-1 the browser's ROM store recorded
    rom_hashes: dict[str, str] = field(default_factory=dict)
    bgm: bool = True
    swing: str = OFF
    putt: str = OFF
    spin: str = OFF

    @classmethod
    def default(
        cls, rules: ClubRules, abi: int = FINISH_ABI_VERSION
    ) -> "DownloadState":
        """What the form shows with nothing saved: the vanilla settings, fitted to the seed."""
        return fit(SavedSettings(), rules, abi).state

    @classmethod
    def from_form(cls, form) -> "DownloadState":
        """A submission's values, unchecked. `form` is Starlette's FormData, or anything with keys, get and getlist."""
        name = form.get("player_name")
        hashes = {}
        for key in form.keys():  # noqa: SIM118 (FormData, not a dict)
            value = form.get(key)
            if key.startswith(ROM_HASH_PREFIX) and isinstance(value, str):
                hashes[key.removeprefix(ROM_HASH_PREFIX)] = value.strip().lower()

        def choice(name: str) -> str:
            value = form.get(name)
            return value.strip().lower() if isinstance(value, str) else OFF

        bgm = form.getlist("bgm")
        return cls(
            player_name=name.strip() if isinstance(name, str) else "",
            clubs={value for value in form.getlist("clubs") if isinstance(value, str)},
            rom_hashes=hashes,
            bgm="on" in bgm if bgm else True,
            swing=choice("swing"),
            putt=choice("putt"),
            spin=choice("spin"),
        )

    def to_pairs(self) -> list[tuple[str, str]]:
        """The state as the fields a browser would submit for it, hashes included."""
        pairs = [("player_name", self.player_name)]
        pairs += [("clubs", club.label) for club in Club if club.label in self.clubs]
        pairs.append(("bgm", "off"))
        if self.bgm:
            pairs.append(("bgm", "on"))
        pairs += [("swing", self.swing), ("putt", self.putt), ("spin", self.spin)]
        pairs += [
            (ROM_HASH_PREFIX + rom_id, sha1) for rom_id, sha1 in self.rom_hashes.items()
        ]
        return pairs


# -- Saved settings ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SavedSettings:
    """A player's remembered download settings: one versioned record, stored as JSON.

    `clubs` holds the putter, as `PlayerOptions` does.
    """

    player_name: str = VANILLA_NAME
    clubs: frozenset[Club] = frozenset(VANILLA_CLUBS)
    bgm: bool = True
    swing: SwingSpeed = SwingSpeed.OFF
    putt: SwingSpeed = SwingSpeed.OFF
    spin: BallSpin = BallSpin.OFF

    #: the record's version; a new setting is a new optional field, not a new version
    VERSION = 1

    @classmethod
    def from_json(cls, data: object) -> "SavedSettings":
        """A record from untrusted JSON. A missing, unknown or invalid field reads as vanilla."""
        if not isinstance(data, dict):
            return cls()
        vanilla = cls()
        return cls(
            player_name=_saved_name(data.get("name"), vanilla.player_name),
            clubs=_saved_clubs(data.get("clubs"), vanilla.clubs),
            bgm=data["bgm"] if isinstance(data.get("bgm"), bool) else vanilla.bgm,
            swing=_saved_choice(data.get("swing"), SwingSpeed, vanilla.swing),
            putt=_saved_choice(data.get("putt"), SwingSpeed, vanilla.putt),
            spin=_saved_choice(data.get("spin"), BallSpin, vanilla.spin),
        )

    @classmethod
    def from_cookie(cls, value: str | None) -> "SavedSettings":
        """A record from the `golf_download` cookie. Anything that fails to decode reads as vanilla."""
        if not value:
            return cls()
        try:
            text = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            data = json.loads(text)
        except (
            binascii.Error,
            ValueError,
        ):  # UnicodeDecodeError and JSONDecodeError too
            return cls()
        return cls.from_json(data)

    def to_cookie(self) -> str:
        """The record as the `golf_download` cookie holds it: compact JSON, base64url, unpadded."""
        text = json.dumps(self.to_json(), separators=(",", ":"))
        return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")

    def with_entry(self, player_name: str, clubs: Iterable[str]) -> "SavedSettings":
        """These settings with an entry's name and bag, as the entry's seed page starts."""
        return replace(
            self,
            player_name=_saved_name(player_name, self.player_name),
            clubs=_saved_clubs(list(clubs), self.clubs),
        )

    def to_json(self) -> dict:
        return {
            "v": self.VERSION,
            "name": self.player_name,
            "clubs": [club.label for club in sorted(self.clubs)],
            "bgm": self.bgm,
            "swing": self.swing.name.lower(),
            "putt": self.putt.name.lower(),
            "spin": self.spin.name.lower(),
        }


def _saved_name(value: object, vanilla: str) -> str:
    if not isinstance(value, str):
        return vanilla
    name = value.upper()
    if set(name) - set(NAME_CHARS) or not name.strip() or len(name) > NAME_LENGTH:
        return vanilla
    return name


def _saved_clubs(value: object, vanilla: frozenset[Club]) -> frozenset[Club]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        return vanilla
    try:
        bag = frozenset(parse_club(label) for label in value) | {Club.PT}
    except ValueError:
        return vanilla
    return bag if len(bag) <= BAG_SIZE else vanilla


def _saved_choice[E: (SwingSpeed, BallSpin)](
    value: object, kind: type[E], vanilla: E
) -> E:
    if not isinstance(value, str) or value.upper() not in kind.__members__:
        return vanilla
    return kind[value.upper()]


@dataclass(frozen=True)
class FittedDownload:
    """A seed's download form as saved settings start it."""

    state: DownloadState
    #: saved clubs the seed bans, taken out of the bag
    removed: frozenset[str]
    #: whether the bag is over the seed's max, so the form refuses it as it stands
    over_max: bool


def fit(saved: SavedSettings, rules: ClubRules, abi: int) -> FittedDownload:
    """Saved settings fitted to a seed. Never refuses: it removes what the seed forbids and flags the rest.

    A required bag replaces the saved bag. Banned clubs are removed and their slots left
    empty. A bag over the max is left for the player to trim, flagged. A seed whose ABI
    cannot write swing, putt and spin starts them off.
    """
    if rules.required_bag is not None:
        bag = rules.required_bag
        removed: frozenset[Club] = frozenset()
    else:
        bag = saved.clubs - rules.banned
        removed = saved.clubs & rules.banned
    options = writes_option_defaults(abi)
    state = DownloadState(
        player_name=saved.player_name,
        clubs={club.label for club in bag if club != Club.PT},
        bgm=saved.bgm,
        swing=saved.swing.name.lower() if options else OFF,
        putt=saved.putt.name.lower() if options else OFF,
        spin=saved.spin.name.lower() if options else OFF,
    )
    return FittedDownload(
        state=state,
        removed=frozenset(club.label for club in removed),
        over_max=len(bag | {Club.PT}) > rules.max,
    )


def to_save(
    options: PlayerOptions, rules: ClubRules, abi: int, previous: SavedSettings
) -> SavedSettings:
    """What a download saves: each setting the seed did not force, the rest kept from `previous`.

    Name and BGM are always saved. The bag is saved only from a seed with the default club
    rules. Swing, putt and spin are saved only from a seed whose ABI can write them.
    """
    options_written = writes_option_defaults(abi)
    return SavedSettings(
        player_name=options.player_name,
        clubs=options.clubs if rules == ClubRules() else previous.clubs,
        bgm=options.bgm,
        swing=options.swing if options_written else previous.swing,
        putt=options.putt if options_written else previous.putt,
        spin=options.spin if options_written else previous.spin,
    )


def saved_from_state(state: DownloadState, previous: SavedSettings) -> SavedSettings:
    """The settings /me saves from its form: any bag of up to 14 with the putter, every option.

    Raises FormError as a download does.
    """
    options = player_options_from_state(state, ClubRules(), FINISH_ABI_VERSION)
    return to_save(options, ClubRules(), FINISH_ABI_VERSION, previous)


def check_rom_hashes(state: DownloadState, required: Iterable[str]) -> None:
    """Raise FormError naming every required ROM the submission has no vanilla hash for."""
    missing = [
        rom_id
        for rom_id in required
        if state.rom_hashes.get(rom_id) != vanilla_rom(rom_id).sha1
    ]
    if missing:
        raise FormError(
            ROMS_MISSING,
            roms=", ".join(vanilla_rom(rom_id).title for rom_id in missing),
        )


def player_options_from_state(
    state: DownloadState, rules: ClubRules, abi: int = FINISH_ABI_VERSION
) -> PlayerOptions:
    """The options a download asks for under the seed's club rules. Raises FormError for anything they forbid.

    A seed with a required bag ignores the submitted clubs and uses its bag. A seed whose
    ABI cannot write swing, putt and spin, whose form leaves them out, takes only off.
    """
    name = state.player_name.upper()
    bad = "".join(sorted(set(name) - set(NAME_CHARS)))
    if bad:
        raise FormError(INVALID_NAME, chars=bad)
    if not name.strip() or len(name) > NAME_LENGTH:
        raise FormError(INVALID_NAME, chars="")

    if rules.required_bag is not None:
        clubs = rules.required_bag
    else:
        try:
            clubs = frozenset(parse_club(label) for label in state.clubs)
        except ValueError:
            raise FormError(INVALID, field="clubs") from None
    bag = clubs | {Club.PT}
    if bag & rules.banned:
        raise FormError(CLUBS_BANNED, clubs=_labels(bag & rules.banned))
    if len(bag) > rules.max:
        raise FormError(CLUBS_OVER_MAX, count=len(bag), max=rules.max)

    for field_name, value, choices in (
        ("swing", state.swing, SPEED_CHOICES),
        ("putt", state.putt, SPEED_CHOICES),
        ("spin", state.spin, SPIN_CHOICES),
    ):
        if value not in choices or (value != OFF and not writes_option_defaults(abi)):
            raise FormError(INVALID, field=field_name)

    try:
        return PlayerOptions(
            player_name=name,
            clubs=bag,
            bgm=state.bgm,
            swing=SwingSpeed[state.swing.upper()],
            putt=SwingSpeed[state.putt.upper()],
            spin=BallSpin[state.spin.upper()],
        )
    except (
        BuildError
    ):  # pragma: no cover - every rule PlayerOptions checks is checked above
        raise FormError(INVALID, field="player") from None
