"""What the generate form and the seed page show, built from the library's data.

Nothing here is English: course names, ROM titles, hole ids, club labels and file names are
data. The templates put them into strings from `server/strings/`.
"""

from dataclasses import dataclass
from datetime import date, datetime

from markupsafe import Markup

from golf.core import jp_rom_utils, rom_utils
from golf.core.patches.sram_defaults import BAG_SIZE, NAME_LENGTH
from golf.randomizer.build import FINISH_ABI_VERSION
from golf.randomizer.catalog import JP_ROM, US_ROM
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.manifest import SOURCES, ClubRules
from golf.randomizer.music import TRACKS, Track
from golf.randomizer.roms import VanillaRom, vanilla_rom
from golf.randomizer.wind import DIRECTION_PROFILES, SPEED_PROFILES

from .forms import (
    DRAW_RULE_CHOICES,
    MUSIC_CHOICES,
    PARS,
    RULE_CLUBS,
    DownloadState,
    SavedSettings,
    fit,
    writes_option_defaults,
)
from .rounds import Round, VoidedRound
from .seeds import SeedHole, SeedRow

#: (ROM id, course directory name) -> the course's display name
COURSE_NAMES: dict[tuple[str, str], str] = {
    **{
        (US_ROM, course["name"]): course["display_name"] for course in rom_utils.COURSES
    },
    **{
        (JP_ROM, course["name"]): course["display_name"]
        for course in jp_rom_utils.COURSES
    },
}


def track_course(track: Track) -> str:
    """The display name of the course a theme belongs to."""
    course = track.slug.removeprefix("nes_") if track.rom == US_ROM else track.slug
    return COURSE_NAMES[(track.rom, course)]


@dataclass(frozen=True)
class MusicOption:
    slug: str
    #: both None for a stored seed's theme this release no longer has, shown by slug
    rom_title: str | None
    course: str | None


@dataclass(frozen=True)
class GenerateOptions:
    """The choices the generate form lists."""

    pars: tuple[int, ...]
    sources: tuple[VanillaRom, ...]
    music: tuple[MusicOption, ...]
    #: the draw rule select's values, each labeled by its own strings key in generate.html
    draw_rules: tuple[str, ...]
    #: the wind selects' values, each named by `_wind.html`
    wind_speeds: tuple[str, ...]
    wind_directions: tuple[str, ...]
    club_labels: tuple[str, ...]
    clubs_max: int


def generate_options() -> GenerateOptions:
    return GenerateOptions(
        pars=PARS,
        sources=tuple(vanilla_rom(source) for source in SOURCES),
        music=tuple(
            MusicOption(
                slug, vanilla_rom(TRACKS[slug].rom).title, track_course(TRACKS[slug])
            )
            for slug in MUSIC_CHOICES
            if slug in TRACKS
        ),
        draw_rules=tuple(DRAW_RULE_CHOICES),
        wind_speeds=SPEED_PROFILES,
        wind_directions=DIRECTION_PROFILES,
        club_labels=tuple(club.label for club in RULE_CLUBS),
        clubs_max=BAG_SIZE,
    )


@dataclass(frozen=True)
class HoleView:
    number: int
    id: str
    display_name: str | None
    par: int
    distance: int
    author: str
    #: set for a vanilla hole: the ROM, course and hole number it comes from
    rom_title: str | None
    course: str | None
    source_hole: int | None


#: every downloaded ROM's file name starts with this, so tools can recognize a randomizer ROM
DOWNLOAD_PREFIX = "notgr"


def timestamp(stamp: str) -> Markup:
    """A stored UTC time as a `<time>` element, for the `timestamp` template filter.

    Its text is the UTC minute; `server/static/localtime.js` replaces it with the viewer's
    local time in their locale's format, and keeps the UTC text as the hover title.
    """
    when = datetime.fromisoformat(stamp)
    return Markup('<time datetime="{}">{} UTC</time>').format(
        stamp, when.strftime("%Y-%m-%d %H:%M")
    )


def calendar_date(day: date) -> Markup:
    """A date as a `<time>` element, for the `calendar_date` template filter.

    Its text is the ISO date; `server/static/localtime.js` replaces it with the date in the
    viewer's locale's format. A date has no time zone, so the script formats it as UTC.
    """
    return Markup('<time datetime="{0}">{0}</time>').format(day.isoformat())


def download_stem(row: SeedRow) -> str:
    """A seed's download file name without its extension, e.g. `notgr_par72_4np03ChsZL`.

    The prefix marks a randomizer ROM, the par tells seeds apart at a glance, and the id leads
    back to the seed page. It is a file name, not player-facing text: never a strings entry.
    """
    return f"{DOWNLOAD_PREFIX}_par{row.manifest.course.par}_{row.id}"


@dataclass(frozen=True)
class DownloadView:
    """The seed page's download form, started from the player's saved settings."""

    #: where the form posts, and the finished IPS comes from
    ips_url: str
    #: the name the patched ROM downloads as
    filename: str
    #: what the form starts with: saved settings fitted to this seed
    state: DownloadState
    name_max: int
    #: every club the form can list: the putter is always carried and never listed
    club_labels: tuple[str, ...]
    banned: frozenset[str]
    clubs_max: int
    #: None when the seed has no required bag, and the form lists clubs
    required_bag: tuple[str, ...] | None
    required_roms: tuple[VanillaRom, ...]
    #: saved clubs this seed bans, taken out of the starting bag
    removed: tuple[str, ...]
    #: whether the starting bag is over this seed's max
    over_max: bool
    #: whether this seed's ROM can take swing, putt and spin defaults (finish ABI 2 on)
    option_defaults: bool

    @property
    def open(self) -> bool:
        """Whether the settings start shown: the form would be refused as it stands."""
        return self.over_max

    @property
    def bag_count(self) -> int:
        """The starting bag's size, putter included."""
        return len(self.state.clubs) + 1

    @property
    def rom_details(self) -> dict[str, dict[str, str]]:
        """What download.js needs to know of each required ROM: id -> title and SHA-1."""
        return {
            rom.id: {"title": rom.title, "sha1": rom.sha1} for rom in self.required_roms
        }


@dataclass(frozen=True)
class SettingsView:
    """The saved download settings on /me: the controls without a seed's rules."""

    state: DownloadState
    name_max: int
    club_labels: tuple[str, ...]
    #: whether there is anything to forget: an account row or this browser's cookie
    saved: bool


def settings_view(saved: SavedSettings, has_saved: bool) -> SettingsView:
    return SettingsView(
        state=fit(saved, ClubRules(), FINISH_ABI_VERSION).state,
        name_max=NAME_LENGTH,
        club_labels=tuple(club.label for club in RULE_CLUBS),
        saved=has_saved,
    )


@dataclass(frozen=True)
class SeedView:
    id: str
    magic_words: tuple[str, ...]
    created_at: str
    withdrawn_at: str | None
    holes: tuple[HoleView, ...]
    total_par: int
    total_distance: int
    music: MusicOption
    par_target: int
    sources: tuple[VanillaRom, ...]
    allow_family_repeats: bool
    #: the draw rule's name, as `golf.randomizer.manifest.DRAW_RULES` has it
    draw_rule: str
    #: the most expert holes on a nine under the expert cap, None under any other rule
    experts_per_nine: int | None
    wind_speed_profile: str
    wind_direction_profile: str
    mercy_point: int | None
    clubs_max: int
    banned: tuple[str, ...]
    #: None when the seed has no required bag
    required_bag: tuple[str, ...] | None
    required_roms: tuple[VanillaRom, ...]
    download: DownloadView


def _hole_view(hole: SeedHole, curation: CurationSnapshot) -> HoleView:
    """A hole as the seed stored it. Its display name is the one curation gives it now."""
    rom, course = hole.source_rom, hole.source_course
    return HoleView(
        number=hole.position,
        id=hole.hole_id,
        display_name=curation.for_hole(hole.hole_id).display_name,
        par=hole.par,
        distance=hole.distance,
        author=hole.author,
        rom_title=None if rom is None else vanilla_rom(rom).title,
        course=None
        if rom is None or course is None
        else COURSE_NAMES.get((rom, course), course),
        source_hole=hole.source_hole,
    )


def seed_view(
    row: SeedRow,
    seed_holes: list[SeedHole],
    curation: CurationSnapshot,
    saved: SavedSettings | None = None,
) -> SeedView:
    """What the seed page shows. The download form starts from `saved`, vanilla by default."""
    manifest = row.manifest
    course = manifest.course
    settings = manifest.settings
    holes = tuple(_hole_view(hole, curation) for hole in seed_holes)
    theme = TRACKS.get(course.music)
    rules = course.clubs
    required = tuple(vanilla_rom(rom) for rom in row.required_roms)
    required_bag = (
        None
        if rules.required_bag is None
        else tuple(club.label for club in sorted(rules.required_bag))
    )
    abi = manifest.finish_abi_version
    fitted = fit(SavedSettings() if saved is None else saved, rules, abi)
    download = DownloadView(
        ips_url=f"/h/{row.id}/patch.ips",
        filename=download_stem(row) + ".nes",
        state=fitted.state,
        name_max=NAME_LENGTH,
        club_labels=tuple(club.label for club in RULE_CLUBS),
        banned=frozenset(club.label for club in rules.banned),
        clubs_max=rules.max,
        required_bag=required_bag,
        required_roms=required,
        removed=tuple(
            club.label for club in RULE_CLUBS if club.label in fitted.removed
        ),
        over_max=fitted.over_max,
        option_defaults=writes_option_defaults(abi),
    )
    return SeedView(
        id=row.id,
        magic_words=course.magic_words,
        created_at=row.created_at,
        withdrawn_at=row.withdrawn_at,
        holes=holes,
        total_par=course.par,
        total_distance=sum(hole.distance for hole in holes),
        music=MusicOption(course.music, None, None)
        if theme is None
        else MusicOption(theme.slug, vanilla_rom(theme.rom).title, track_course(theme)),
        par_target=settings.par,
        sources=tuple(
            vanilla_rom(source) for source in SOURCES if source in settings.sources
        ),
        allow_family_repeats=settings.allow_family_repeats,
        draw_rule=settings.draw_rule.rule,
        experts_per_nine=settings.draw_rule.per_nine,
        wind_speed_profile=settings.wind_speed_profile,
        wind_direction_profile=settings.wind_direction_profile,
        mercy_point=course.mercy_point,
        clubs_max=course.clubs.max,
        banned=tuple(club.label for club in sorted(course.clubs.banned)),
        required_bag=required_bag,
        required_roms=required,
        download=download,
    )


#: What a hole's fairway cell shows, as its `data-fairway` value
FAIRWAY_HIT = "hit"
FAIRWAY_MISSED = "miss"
#: a par 3, which has no fairway to hit
FAIRWAY_NONE = "na"


def fairway_result(fairway_hit: bool | None, par: int) -> str | None:
    """
    A hole's fairway as the round page shows it, from what `server/rounds.py` stored, or
    None for a round that did not record fairways (protocol version 1).
    """
    if fairway_hit is None:
        return None
    if par < 4:
        return FAIRWAY_NONE
    return FAIRWAY_HIT if fairway_hit else FAIRWAY_MISSED


@dataclass(frozen=True)
class RoundHoleView:
    number: int
    par: int
    strokes: int
    putts: int
    #: FAIRWAY_HIT, FAIRWAY_MISSED or FAIRWAY_NONE; None when the round did not record it
    fairway: str | None = None


@dataclass(frozen=True)
class NineView:
    """One nine of a round, as a table of its own on the scan page, with its totals."""

    holes: tuple[RoundHoleView, ...]

    @property
    def par(self) -> int:
        return sum(hole.par for hole in self.holes)

    @property
    def strokes(self) -> int:
        return sum(hole.strokes for hole in self.holes)

    @property
    def putts(self) -> int:
        return sum(hole.putts for hole in self.holes)

    @property
    def fairways_hit(self) -> int:
        return sum(hole.fairway == FAIRWAY_HIT for hole in self.holes)

    @property
    def fairways_possible(self) -> int:
        return sum(hole.fairway in (FAIRWAY_HIT, FAIRWAY_MISSED) for hole in self.holes)


@dataclass(frozen=True)
class RoundView:
    """What a round's permalink shows of it."""

    #: the id in its `/r/<id>` URL
    public_id: str
    seed_id: str
    magic_words: tuple[str, ...]
    player_name: str
    slot: int
    #: whether the visitor arrived from the scan that recorded it, rather than by the permalink
    recorded: bool
    received_at: str
    #: holes 1-9 and 10-18, a table each
    front: NineView
    back: NineView
    total_par: int
    total_strokes: int
    total_putts: int
    #: water and out-of-bounds strokes; None when the round did not record them, which is
    #: also when its holes carry no fairways (protocol version 1)
    penalty_strokes: int | None = None

    @property
    def stats_recorded(self) -> bool:
        return self.penalty_strokes is not None

    @property
    def fairways_hit(self) -> int:
        return self.front.fairways_hit + self.back.fairways_hit

    @property
    def fairways_possible(self) -> int:
        return self.front.fairways_possible + self.back.fairways_possible


def round_view(
    row: SeedRow, scorecard: Round, player_name: str, recorded: bool = False
) -> RoundView:
    """A recorded round as its permalink shows it; `recorded` marks the redirect from its scan."""
    course = row.manifest.course
    holes = tuple(
        RoundHoleView(
            hole.position,
            slot.par,
            hole.strokes,
            hole.putts,
            fairway_result(hole.fairway_hit, slot.par),
        )
        for hole, slot in zip(scorecard.holes, course.holes, strict=True)
    )
    return RoundView(
        public_id=scorecard.public_id,
        seed_id=row.id,
        magic_words=course.magic_words,
        player_name=player_name,
        slot=scorecard.slot,
        recorded=recorded,
        received_at=scorecard.received_at,
        front=NineView(holes[:9]),
        back=NineView(holes[9:]),
        total_par=course.par,
        total_strokes=scorecard.total_strokes,
        total_putts=scorecard.total_putts,
        penalty_strokes=scorecard.penalty_strokes,
    )


@dataclass(frozen=True)
class VoidedRoundView:
    """What a voided round's permalink shows: that a round was here, and nothing of its scores."""

    public_id: str
    seed_id: str
    magic_words: tuple[str, ...]
    player_name: str
    slot: int
    received_at: str
    voided_at: str


def voided_round_view(
    row: SeedRow, voided: VoidedRound, player_name: str
) -> VoidedRoundView:
    return VoidedRoundView(
        public_id=voided.public_id,
        seed_id=row.id,
        magic_words=row.manifest.course.magic_words,
        player_name=player_name,
        slot=voided.slot,
        received_at=voided.received_at,
        voided_at=voided.voided_at,
    )
