"""
The two-stage build: a manifest into an unfinished ROM, and an unfinished ROM into a finished one.

- **Unfinished**, once per seed: the base patches, the course with each slot's hole
  transforms applied, seeded wind, each hole's wind anchors, the wind fix, the course theme,
  mercy tap-in, the green detail view and scorecard shortcuts, the round stats the QR code
  sends (fairways hit and penalty strokes), the scorecard QR image with
  its credential placeholders at the fill, the signpost banner, the magic words on the
  menus and scorecard, and the new-save options routine with its table at the vanilla
  values. A seed with club rules leaves CHOOSE CLUBS out of the club house, so the bag
  the finisher writes is the bag the save keeps. Everything it reads is
  the manifest's `course`, the catalog and the hole store. The site stores the result as an
  IPS against the vanilla ROM.
- **Finished**, per download: the player's new-save defaults under the seed's SRAM magic
  (name, clubs, and under finish ABI 2 the BGM, swing, putt and spin table), then either
  `qr_credentials` (signed in) or `qr_disable` (guest). It runs as a second
  `PatchStack` on the unfinished ROM, since both QR finishing patches rewrite bytes
  `scorecard_qr` wrote.

See docs/randomizer_devplan.md.

`BUILD_VERSION` identifies the unfinished recipe. `FINISH_ABI_VERSION` identifies the
interface its artifact exposes to the per-download finisher. Building requires both
current versions; finishing dispatches only on the ABI, so several historical build
versions may share one finisher without retaining their unfinished buildchains.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from golf.core import ips, rom_utils
from golf.core.patches import (
    COURSE_MIRRORS_PATCH,
    MULTI_BANK_CODE_PATCH,
    QR_DISABLE_PATCH,
    ROUND_STATS_PATCH,
    SCORECARD_QR_PATCH,
    WIND_FIX_PATCH,
    WRAM_EXPANSION_PATCH,
    CompositePatch,
    CoursePatch,
    CourseWriteStats,
    PatchStack,
    QrCredentials,
    ROMPatch,
    course_theme_patch,
    green_shortcut_patch,
    menu_trim_patch,
    mercy_tap_in_patches,
    music_import_patch,
    qr_credentials_patch,
    scorecard_course_name_patch,
    seeded_wind_patch,
    sram_defaults_patch,
    wind_anchors_patch,
)
from golf.core.patches.extended_sram_defaults import (
    EXTENDED_SRAM_DEFAULTS_PATCH,
    BallSpin,
    SwingSpeed,
    option_values,
)
from golf.core.patches.signpost_random_banner import signpost_banner_patch
from golf.core.patches.sram_defaults import (
    Club,
    club_bag_bytes,
    parse_club,
    player_name_bytes,
)
from golf.core.rom_reader import RomReader
from golf.formats.hole_data import HoleData
from golf.qr import payload

from .catalog import JP_ROM, REPO_ROOT, US_ROM, Catalog, HoleStore
from .manifest import ClubRules, Manifest, StoredManifest
from .music import MUSIC_DUMPS, track
from .transforms import apply_transforms
from .words import scorecard_title

SIGNPOST_ART = (
    REPO_ROOT / "golf" / "core" / "patches" / "data" / "signpost_random.aseprite"
)

#: The largest seed ID the 8-byte field holds. The site draws below 62**10.
MAX_SEED_ID = (1 << (8 * payload.SEED_ID_LEN)) - 1
MAX_PLAYER_ID = (1 << (8 * payload.PLAYER_ID_LEN)) - 1
#: the unfinished-ROM recipe this release implements
BUILD_VERSION = 6
#: the interface current unfinished ROMs expose to the per-download finisher
FINISH_ABI_VERSION = 2


class BuildError(ValueError):
    """Inputs a build refuses: player options the seed's rules forbid, or the wrong base ROM."""


# -- Player options -------------------------------------------------------------------------


@dataclass(frozen=True)
class PlayerOptions:
    """What a player chooses at download time. The putter is added to the bag if missing.

    `swing`, `putt` and `spin` are the new-save defaults for the swing speed, putt speed
    and ball spin; off leaves each as the player last chose it. Only finish ABI 2 can
    write them.
    """

    player_name: str
    clubs: frozenset[Club]
    bgm: bool = True
    swing: SwingSpeed = SwingSpeed.OFF
    putt: SwingSpeed = SwingSpeed.OFF
    spin: BallSpin = BallSpin.OFF

    def __post_init__(self):
        clubs = frozenset(self.clubs) | {Club.PT}
        object.__setattr__(self, "clubs", clubs)
        try:
            player_name_bytes(self.player_name)
            club_bag_bytes(clubs)
        except ValueError as problem:
            raise BuildError(str(problem)) from None
        if not isinstance(self.bgm, bool):
            raise BuildError("bgm must be true or false")
        for name, kind in (
            ("swing", SwingSpeed),
            ("putt", SwingSpeed),
            ("spin", BallSpin),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise BuildError(f"{name} must be a {kind.__name__}, got {value!r}")
            try:
                object.__setattr__(self, name, kind(value))
            except ValueError:
                raise BuildError(
                    f"{name} must be a {kind.__name__}, got {value!r}"
                ) from None

    @property
    def has_option_defaults(self) -> bool:
        """Whether swing, putt or spin differs from off, which finish ABI 1 cannot write."""
        return option_values(True, self.swing, self.putt, self.spin) != option_values()

    def check(self, rules: ClubRules) -> None:
        """Raise BuildError if the bag breaks the seed's club rules."""
        labels = lambda clubs: " ".join(club.label for club in sorted(clubs))  # noqa: E731
        if rules.required_bag is not None and self.clubs != rules.required_bag:
            raise BuildError(
                f"this seed requires the bag {labels(rules.required_bag)}, got {labels(self.clubs)}"
            )
        if len(self.clubs) > rules.max:
            raise BuildError(
                f"this seed allows at most {rules.max} clubs, got {len(self.clubs)}"
            )
        banned = self.clubs & rules.banned
        if banned:
            raise BuildError(f"this seed bans {labels(banned)}")


# -- Credentials ----------------------------------------------------------------------------


def seed_id_bytes(qr_seed_id: int) -> bytes:
    """A seed's `qr_seed_id` as the QR payload's seed ID field: big-endian."""
    if (
        isinstance(qr_seed_id, bool)
        or not isinstance(qr_seed_id, int)
        or not 1 <= qr_seed_id <= MAX_SEED_ID
    ):
        raise BuildError(f"qr_seed_id must be 1-{MAX_SEED_ID}, got {qr_seed_id!r}")
    return qr_seed_id.to_bytes(payload.SEED_ID_LEN, "big")


def player_id_bytes(player_id: int) -> bytes:
    """A user's `player_id` as the QR payload's player ID field: big-endian."""
    if (
        isinstance(player_id, bool)
        or not isinstance(player_id, int)
        or not 1 <= player_id <= MAX_PLAYER_ID
    ):
        raise BuildError(f"player_id must be 1-{MAX_PLAYER_ID}, got {player_id!r}")
    return player_id.to_bytes(payload.PLAYER_ID_LEN, "big")


def credentials_for_slots(
    qr_seed_id: int, slot0: tuple[int, bytes], slot1: tuple[int, bytes]
) -> QrCredentials:
    """Credentials naming a (player ID, MAC key) pair for each ROM slot."""
    try:
        return QrCredentials(
            seed_id=seed_id_bytes(qr_seed_id),
            player_ids=(player_id_bytes(slot0[0]), player_id_bytes(slot1[0])),
            keys=(slot0[1], slot1[1]),
        )
    except ValueError as problem:
        raise BuildError(str(problem)) from None


def credentials_for(
    qr_seed_id: int, player_id: int, keys: tuple[bytes, bytes]
) -> QrCredentials:
    """A signed-in player's credentials: their player ID in both slots, one key per slot."""
    return credentials_for_slots(qr_seed_id, (player_id, keys[0]), (player_id, keys[1]))


# -- Unfinished -----------------------------------------------------------------------------


def music_step(slug: str) -> ROMPatch:
    """The patch that makes a music slug the course theme.

    A NES Open theme is already in the ROM and only needs `CourseBgmTable`; a Mario Open
    theme is imported from its dump.
    """
    theme = track(slug)
    if theme.rom == US_ROM:
        return course_theme_patch(theme.music_id)
    if theme.rom == JP_ROM:
        dump = json.loads(MUSIC_DUMPS[JP_ROM].read_text())
        return music_import_patch(dump, track=theme.music_id)
    raise BuildError(
        f"music {slug!r} comes from {theme.rom!r}, which no build knows"
    )  # pragma: no cover


@lru_cache(maxsize=1)
def signpost_step(vanilla: bytes) -> ROMPatch:
    """The signpost banner patch, the same for every seed, so built once per base ROM."""
    return signpost_banner_patch(RomReader.from_bytes(vanilla), SIGNPOST_ART)


def unfinished_steps(
    manifest: Manifest, catalog: Catalog, store: HoleStore, vanilla: bytes
) -> list[ROMPatch]:
    """The unfinished stack's steps, in order. `vanilla` is read for the signpost art."""
    course = manifest.course
    holes = [
        apply_transforms(store.load(catalog[slot.id]), slot.transforms)
        for slot in course.holes
    ]
    steps: list[ROMPatch] = [
        WRAM_EXPANSION_PATCH,
        MULTI_BANK_CODE_PATCH,
        COURSE_MIRRORS_PATCH,
        CoursePatch(holes),
        seeded_wind_patch(seeds=[slot.wind_seed for slot in course.holes]),
        wind_anchors_patch([slot.wind for slot in course.holes]),
        WIND_FIX_PATCH,
        music_step(course.music),
    ]
    if course.mercy_point is not None:
        steps.append(
            CompositePatch(
                "mercy_tap_in",
                f"End a hole at stroke {course.mercy_point} with a tap-in",
                mercy_tap_in_patches(course.mercy_point),
            )
        )
    steps += [
        green_shortcut_patch(),
        ROUND_STATS_PATCH,
        SCORECARD_QR_PATCH,
        signpost_step(vanilla),
        scorecard_course_name_patch(title=scorecard_title(course.magic_words)),
        menu_trim_patch(
            list(course.magic_words), choose_clubs=course.clubs == ClubRules()
        ),
        EXTENDED_SRAM_DEFAULTS_PATCH,
    ]
    return steps


@dataclass(frozen=True)
class UnfinishedBuild:
    rom: bytes
    #: the IPS from the vanilla ROM to `rom`, what the site stores on the seed row
    ips: bytes
    #: step name -> the [start, end) PRG offset ranges it wrote
    regions: dict[str, list[tuple[int, int]]]
    course_stats: CourseWriteStats
    #: the 18 holes as built, transforms applied; the site keeps the transformed ones
    holes: tuple[HoleData, ...]


def _check_vanilla(vanilla: bytes) -> None:
    actual = hashlib.sha1(vanilla).hexdigest()
    if actual != rom_utils.US_ROM_SHA1:
        raise BuildError(
            f"the base ROM has SHA-1 {actual}, not the vanilla US ROM's {rom_utils.US_ROM_SHA1}"
        )


def build_unfinished(
    manifest: Manifest, catalog: Catalog, store: HoleStore, vanilla: bytes
) -> UnfinishedBuild:
    if manifest.build_version != BUILD_VERSION:
        raise BuildError(
            f"manifest requires unfinished build version {manifest.build_version}; "
            f"this release builds version {BUILD_VERSION}"
        )
    if manifest.finish_abi_version != FINISH_ABI_VERSION:
        raise BuildError(
            f"manifest requires finish ABI {manifest.finish_abi_version}; "
            f"unfinished build version {BUILD_VERSION} produces ABI "
            f"{FINISH_ABI_VERSION}"
        )
    _check_vanilla(vanilla)
    steps = unfinished_steps(manifest, catalog, store, vanilla)
    build = PatchStack(steps).build(vanilla)
    course = next(step for step in steps if isinstance(step, CoursePatch))
    return UnfinishedBuild(
        rom=build.rom,
        ips=ips.diff(vanilla, build.rom),
        regions=build.regions,
        course_stats=course.stats,
        holes=tuple(course.holes),
    )


# -- Finished -------------------------------------------------------------------------------


def finishing_steps(
    options: PlayerOptions,
    sram_magic: int,
    credentials: QrCredentials | None,
    abi: int = FINISH_ABI_VERSION,
) -> list[ROMPatch]:
    """New-save defaults, then credentials when signed in or the QR screen disabled for a guest.

    Under ABI 1 `sram_defaults` writes the BGM option with its fill-loop edit, and swing,
    putt and spin stay off. Under ABI 2 the new-save options table holds all four.
    """
    if abi == 1:
        if options.has_option_defaults:
            raise BuildError(
                "this seed's ROM cannot set swing, putt or spin defaults; leave them off"
            )
        steps: list[ROMPatch] = [
            sram_defaults_patch(
                options.player_name, sorted(options.clubs), options.bgm, sram_magic
            ),
        ]
    elif abi == 2:
        steps = [
            sram_defaults_patch(
                options.player_name,
                sorted(options.clubs),
                options.bgm,
                sram_magic,
                swing=options.swing,
                putt=options.putt,
                spin=options.spin,
            ),
        ]
    else:
        raise BuildError(f"this release cannot finish artifact ABI {abi}")
    steps.append(
        QR_DISABLE_PATCH if credentials is None else qr_credentials_patch(credentials)
    )
    return steps


@dataclass(frozen=True)
class FinishedBuild:
    rom: bytes
    #: the IPS from the vanilla ROM to `rom`, what a download sends
    ips: bytes
    #: step name -> the [start, end) PRG offset ranges the finishing stack wrote
    regions: dict[str, list[tuple[int, int]]]


def _finish_through(
    abi: int,
    manifest: Manifest | StoredManifest,
    vanilla: bytes,
    unfinished_ips: bytes,
    options: PlayerOptions,
    credentials: QrCredentials | None,
) -> FinishedBuild:
    _check_vanilla(vanilla)
    options.check(manifest.course.clubs)
    steps = finishing_steps(options, manifest.course.sram_magic, credentials, abi)
    unfinished = ips.apply(vanilla, unfinished_ips)
    build = PatchStack(steps, base_sha1=None).build(unfinished)
    return FinishedBuild(
        rom=build.rom, ips=ips.diff(vanilla, build.rom), regions=build.regions
    )


def _finish_abi_1(
    manifest: Manifest | StoredManifest,
    vanilla: bytes,
    unfinished_ips: bytes,
    options: PlayerOptions,
    credentials: QrCredentials | None = None,
) -> FinishedBuild:
    """Name, clubs and BGM through `sram_defaults`' vanilla locations."""
    return _finish_through(1, manifest, vanilla, unfinished_ips, options, credentials)


def _finish_abi_2(
    manifest: Manifest | StoredManifest,
    vanilla: bytes,
    unfinished_ips: bytes,
    options: PlayerOptions,
    credentials: QrCredentials | None = None,
) -> FinishedBuild:
    """Name and clubs as ABI 1; BGM, swing, putt and spin into the new-save options table."""
    return _finish_through(2, manifest, vanilla, unfinished_ips, options, credentials)


_FINISHERS = {1: _finish_abi_1, 2: _finish_abi_2}


def finish(
    manifest: Manifest | StoredManifest,
    vanilla: bytes,
    unfinished_ips: bytes,
    options: PlayerOptions,
    credentials: QrCredentials | None = None,
) -> FinishedBuild:
    """Finish a stored artifact through the ABI it declares.

    `manifest` is a stored seed's, or a manifest just built: finishing reads only its
    finish ABI, SRAM magic and club rules.

    No credentials finishes a guest ROM. Compatible implementation changes do not bump
    the ABI; a change to the locations, preimages or meanings the finisher consumes does.
    """
    try:
        finisher = _FINISHERS[manifest.finish_abi_version]
    except KeyError:
        raise BuildError(
            f"this release cannot finish artifact ABI {manifest.finish_abi_version}"
        ) from None
    return finisher(manifest, vanilla, unfinished_ips, options, credentials)


def clubs_from_labels(labels: Iterable[str]) -> frozenset[Club]:
    """A bag from choose-clubs labels such as `1W` and `PW`, for callers holding strings."""
    try:
        return frozenset(parse_club(label) for label in labels)
    except ValueError as problem:
        raise BuildError(str(problem)) from None
