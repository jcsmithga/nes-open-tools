"""Placeholder data for looking at the site: a rated ordinary seed with a few rounds, and an
open daily with a field of a dozen players, for `golf-site-screenshot --populate`.

Everything goes in through the modules that own it, as a real site's data would: seeds
built by the app's builder and stored by `insert_seed`, players by `sign_in`, entries by
`upsert_entry`, rounds by `submit_scan` with payloads signed by each entry's keys, the
ratings by `refit` and the day by `ensure_daily`. A daily's results need its seed rated
when the day is made, and a refit needs rounds on other seeds, so every player first
plays history seeds two days before.

The players sign in as dev users (`dev:<username>`), so the development login bypass
signs a browser in as one of them. Doing so clears their display name, which the bypass
does not carry, so the field's viewer is named so its username reads well.
"""

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from golf.core.clubs import Club
from golf.qr.payload import HoleRecord, RoundPayload, base64url_encode
from golf.randomizer.build import PlayerOptions
from golf.randomizer.daily import closes_at, opens_at
from golf.randomizer.manifest import Settings
from golf.ratings.rules import DailyRules

from .auth import DEV_DISCORD_PREFIX
from .builder import SeedBuilder
from .dailies import ensure_daily, utc_text
from .db import Database
from .entries import upsert_entry
from .ratings import Ratings, refit
from .seeds import insert_seed
from .submissions import submit_scan
from .users import sign_in


@dataclass(frozen=True)
class FieldPlayer:
    username: str
    #: the Discord display name a page shows in place of the username
    display: str
    #: strokes over par per hole on average
    skill: float


#: varied lengths and characters, the way production display names run
FIELD = (
    FieldPlayer("birdiequeen", "BirdieQueen", -0.9),
    FieldPlayer("ace", "AceInTheHole", -1.1),
    FieldPlayer("pinseeker", "PinSeeker", -0.4),
    FieldPlayer("tsumugi", "Tsumugi", -0.2),
    FieldPlayer("mulligan_mike", "Mulligan Mike", -0.6),
    FieldPlayer("fringe", "Fringe Benefits", 0.3),
    FieldPlayer("greg_norman_fan", "greg_norman_fan_1986", 0.4),
    FieldPlayer("luigi", "Luigi", 0.0),
    FieldPlayer("sandy", "Sandy Lies", 0.6),
    FieldPlayer("caddy", "Caddyshack Carl", 0.8),
    FieldPlayer("slicey", "Slicey McSliceface", 1.2),
    FieldPlayer("bogeyman", "The Bogeyman", 0.9),
)

#: the field player a browser signs in as to see a played daily
VIEWER = "luigi"
#: the field player whose match against the viewer the match card shows
RIVAL = "birdiequeen"
#: a dev user who is in no field, to see an open daily unplayed
OUTSIDER = "outsider"
#: how many of the field play the ordinary seed
SEED_ROUNDS = 3
HISTORY_SEEDS = 2
#: the daily's secret when the config has none
SECRET = "dev-fixtures"


@dataclass(frozen=True)
class Populated:
    #: an ordinary seed's id
    seed: str
    #: the daily's seed id
    daily: str
    #: the viewer's daily round, its public id
    round: str
    #: the rival's daily round, its public id
    rival_round: str


def open_moment(day: date) -> datetime:
    """A moment late in the day's window, with most of the field in."""
    return opens_at(day) + (closes_at(day) - opens_at(day)) * 3 / 4


def closed_moment(day: date) -> datetime:
    return closes_at(day) + timedelta(hours=1)


def _holes(rng: random.Random, skill: float) -> tuple[HoleRecord, ...]:
    holes = []
    for _ in range(18):
        strokes = max(2, round(4 + skill + rng.gauss(0, 1)))
        holes.append(HoleRecord(strokes, min(strokes - 1, rng.randint(1, 3))))
    return tuple(holes)


def _options(player: FieldPlayer) -> PlayerOptions:
    name = "".join(c for c in player.display.upper() if c.isalpha())[:10]
    return PlayerOptions(name, frozenset({Club.W1, Club.I5, Club.PW}))


def _play(
    db: Database,
    seed_id: str,
    player: FieldPlayer,
    holes: tuple[HoleRecord, ...],
    at: datetime,
    ratings: Ratings,
) -> str:
    """`player` downloads the seed and scans a round of `holes` at `at`; the round's
    public id."""
    user = sign_in(
        db,
        DEV_DISCORD_PREFIX + player.username,
        player.username,
        player.display,
        None,
        now=utc_text(at),
    )
    entry = upsert_entry(db, seed_id, user.id, _options(player), now=utc_text(at))
    with db.transaction() as conn:
        qr_seed_id = conn.execute(
            "SELECT qr_seed_id FROM seeds WHERE id = ?", (seed_id,)
        ).fetchone()[0]
    payload = RoundPayload(
        seed_id=qr_seed_id.to_bytes(8, "big"),
        player_id=user.player_id.to_bytes(4, "big"),
        holes=holes,
    )
    scanned = submit_scan(
        db,
        base64url_encode(payload.to_bytes(entry.keys[0])),
        now=utc_text(at),
        index_rules=ratings.parameters.index,
    )
    return scanned.round.public_id


def _store(
    db: Database,
    builder: SeedBuilder,
    prng_seed: str,
    at: datetime,
    ratings: Ratings | None = None,
) -> str:
    manifest = builder.generate(Settings(prng_seed=prng_seed))
    built = builder.build(manifest)
    return insert_seed(
        db,
        manifest,
        built.unfinished_ips,
        built.holes,
        builder.catalog,
        now=utc_text(at),
        rater=ratings,
    )


def populate(state, day: date) -> Populated:
    """Fill a started app's database (its `app.state`) for `day`'s daily, which must not be
    stored yet: the app runs no rollover. Builds four seeds, so it takes a while."""
    db: Database = state.db
    builder: SeedBuilder = state.builder
    ratings: Ratings = state.ratings
    rng = random.Random(day.isoformat())

    before = opens_at(day) - timedelta(days=2)
    for number in range(HISTORY_SEEDS):
        history = _store(db, builder, f"dev-history-{number}", before)
        for player in FIELD:
            _play(db, history, player, _holes(rng, player.skill), before, ratings)
    made = opens_at(day)
    if refit(db, ratings, day, utc_text(made)) is None and ratings.latest is None:
        raise RuntimeError(f"no ratings run for {day}: the daily could not be rated")

    seed = _store(db, builder, "dev-seed", made, ratings)
    for player in FIELD[-SEED_ROUNDS:]:
        _play(db, seed, player, _holes(rng, player.skill), made, ratings)

    daily = ensure_daily(
        db,
        builder,
        state.schedule,
        state.config.daily_secret or SECRET,
        day,
        now=utc_text(made),
        rater=ratings,
        rules=DailyRules.from_parameters(ratings.parameters),
    )
    # the field's rounds come in through the open window, before the open moment
    step = (open_moment(day) - made) / (len(FIELD) + 1)
    viewer_round = rival_round = ""
    for number, player in enumerate(FIELD, start=1):
        public_id = _play(
            db,
            daily.seed_id,
            player,
            _holes(rng, player.skill),
            made + step * number,
            ratings,
        )
        if player.username == VIEWER:
            viewer_round = public_id
        if player.username == RIVAL:
            rival_round = public_id
    return Populated(seed, daily.seed_id, viewer_round, rival_round)
