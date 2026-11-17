"""
The backtest (docs/competitive/design.md, Setting the numbers): replays a site
database day by day through the ratings, using only what was known each day, and
scores every pair of players on every shared seed under a set of schemes.

Each day (from the daily rollover, 09:00 UTC) the hole model is fitted on the rounds of
earlier days, with the parameters held. A seed is rated once, by the model of the day it
was generated, and keeps that rating, as the site stores it. A round is scored with the
index its player holds when it is scanned: from every earlier round of theirs, that
day's included, under their seeds' stored ratings. Fairness is measured at the steady
state, both players of a pair with `STEADY` earlier rounds. Every round on any seed counts, since the site has no dailies to speak of yet;
a pair on a seed plays as if each player's round were that day's daily.

A scheme is a choice of index rules, allowance and stroke index order. Two schemes are
compared across rosters resampled from the players with replacement: an alternative
wins clearly when it is better in at least `CLEAR` of them. The fairness measure is the
stronger player's share of their net matches, the stronger by the full fit's index,
which knows every round, against `TARGET`: a slight edge for skill
(docs/competitive/design.md, The allowance).
"""

import itertools
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

import numpy as np

from . import formats
from .handicap import daily_handicap, match_strokes, strokes_received
from .index import ScoredRound, compute
from .model import Model, Slot, fit
from .params import HandicapRules, IndexRules, Parameters
from .seed import SeedRating, rate_seed
from .sitedb import PlayedRound, observations

ROLLOVER = timedelta(hours=9)
CLEAR = 0.95
#: earlier rounds each player of a pair needs for the steady state: a full record
STEADY = 10
#: the stronger player's share of net matches the allowance aims at
TARGET = 0.55


def day_of(received_at: str) -> date:
    when = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
    return (when - ROLLOVER).date()


def played_at(received_at: str) -> datetime:
    return datetime.fromisoformat(received_at.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Replayed:
    """One round in the replay, rated as of its day."""

    round: PlayedRound
    day: date
    #: the seed under the day's model, by each stroke index order
    rating: dict[str, SeedRating]
    #: the player's earlier rounds, rated under the day's model
    prior: tuple[ScoredRound, ...]
    #: the day's model, kept for the per-hole checks
    model: Model = field(repr=False)


def replay(
    rounds: Sequence[PlayedRound],
    params: Parameters,
    reference: Sequence[Slot],
    created: dict[str, str],
    min_train: int = 20,
    progress: Callable[[date], None] | None = None,
) -> list[Replayed]:
    """Every round from the day the earlier days hold `min_train` rounds. A seed is
    rated once, by the model of the day it was generated (`created`), and keeps that
    rating, as the site stores it (D47); a seed generated before the replay's first
    model takes that model's."""
    ordered = sorted(rounds, key=lambda r: r.received_at)
    days = sorted({day_of(r.received_at) for r in ordered})
    out = []
    ratings = _StoredRatings(created)
    for d in days:
        train = [r for r in ordered if day_of(r.received_at) < d]
        today = [r for r in ordered if day_of(r.received_at) == d]
        if len(train) < min_train:
            continue
        model = fit(observations(train), params.model, reference)
        ratings.add(d, model)
        for r in today:
            # the index a scan scores with: every earlier round of the player's,
            # that day's included
            prior = tuple(
                ScoredRound(
                    played_at(p.received_at), ratings(p.seed, p.slots, "par"), p.strokes
                )
                for p in ordered
                if p.player == r.player and p.received_at < r.received_at
            )
            out.append(
                Replayed(
                    round=r,
                    day=d,
                    rating={
                        by: ratings(r.seed, r.slots, by) for by in ("par", "spread")
                    },
                    prior=prior,
                    model=model,
                )
            )
        if progress:
            progress(d)
    return out


class _StoredRatings:
    """Each seed rated once, by the model of the day it was generated: the model fitted
    on the rounds before that day, which is the next replayed day's."""

    def __init__(self, created: dict[str, str]):
        self.created = created
        self.models: list[tuple[date, Model]] = []
        self.ratings: dict[tuple[str, str], SeedRating] = {}

    def add(self, day: date, model: Model) -> None:
        self.models.append((day, model))

    def model_for(self, seed: str) -> Model:
        made = self.created.get(seed)
        if made is not None:
            when = day_of(made)
            for day, model in self.models:
                if day >= when:
                    return model
        return self.models[-1][1]

    def __call__(self, seed: str, slots: Sequence[Slot], by: str) -> SeedRating:
        key = (seed, by)
        if key not in self.ratings:
            self.ratings[key] = rate_seed(self.model_for(seed), slots, by)
        return self.ratings[key]


@dataclass(frozen=True)
class Scheme:
    name: str
    index: IndexRules
    handicap: HandicapRules
    order: str = "par"


@dataclass(frozen=True)
class Card:
    """A round as one scheme scores it."""

    player: str
    seed: str
    prior_rounds: int
    index: float
    handicap: int
    strokes: tuple[int, ...]
    pars: tuple[int, ...]
    stroke_indexes: tuple[int, ...]
    expected: float

    @property
    def net(self) -> list[int]:
        return formats.net(
            self.strokes, strokes_received(self.handicap, self.stroke_indexes)
        )

    @property
    def stableford(self) -> int:
        received = strokes_received(self.handicap, self.stroke_indexes)
        return sum(formats.stableford(self.strokes, self.pars, received))


def score(replayed: Sequence[Replayed], scheme: Scheme) -> list[Card]:
    cards = []
    for r in replayed:
        rating = r.rating[scheme.order]
        state = compute(r.prior, scheme.index)
        cards.append(
            Card(
                player=r.round.player,
                seed=r.round.seed,
                prior_rounds=len(r.prior),
                index=state.index,
                handicap=daily_handicap(state.index, rating, scheme.handicap.allowance),
                strokes=r.round.strokes,
                pars=tuple(h.par for h in rating.holes),
                stroke_indexes=tuple(h.stroke_index for h in rating.holes),
                expected=rating.expected(state.index),
            )
        )
    return cards


@dataclass(frozen=True)
class Pair:
    stronger: str
    weaker: str
    #: 1 a win for the stronger player, 0.5 a half, 0 a loss
    match: float
    stroke: float


def pairs(
    cards: Sequence[Card], strength: dict[str, float], min_prior: int
) -> list[Pair]:
    """Every pair of rounds on a shared seed, both players with `min_prior` earlier
    rounds."""
    by_seed: dict[str, list[Card]] = {}
    for c in cards:
        if c.prior_rounds >= min_prior:
            by_seed.setdefault(c.seed, []).append(c)
    out = []
    for group in by_seed.values():
        for a, b in itertools.combinations(group, 2):
            if a.player == b.player:
                continue
            if strength[a.player] > strength[b.player]:
                a, b = b, a
            give_a, give_b = match_strokes(a.handicap, b.handicap, a.stroke_indexes)
            won_a, won_b = formats.match(
                formats.net(a.strokes, give_a), formats.net(b.strokes, give_b)
            )
            net_a, net_b = sum(a.net), sum(b.net)
            out.append(
                Pair(
                    a.player,
                    b.player,
                    1.0 if won_a > won_b else 0.5 if won_a == won_b else 0.0,
                    1.0 if net_a < net_b else 0.5 if net_a == net_b else 0.0,
                )
            )
    return out


def stronger_share(
    pairs_: Sequence[Pair], weights: dict[str, float] | None = None
) -> float:
    w = np.array(
        [
            1.0
            if weights is None
            else weights.get(p.stronger, 0) * weights.get(p.weaker, 0)
            for p in pairs_
        ]
    )
    m = np.array([p.match for p in pairs_])
    return float((w * m).sum() / w.sum()) if w.sum() else float("nan")


def rosters(
    players: Sequence[str], count: int, seed: int = 1
) -> list[dict[str, float]]:
    """Player weights for `count` rosters drawn with replacement."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(count):
        drawn = rng.choice(len(players), len(players))
        weights: dict[str, float] = {}
        for i in drawn:
            name = players[int(i)]
            weights[name] = weights.get(name, 0) + 1
        out.append(weights)
    return out


def clearly_fairer(
    default: Sequence[Pair], other: Sequence[Pair], draws: Sequence[dict[str, float]]
) -> float:
    """The share of rosters in which `other` is nearer `TARGET` than `default`."""
    wins = 0
    counted = 0
    for w in draws:
        a = stronger_share(default, w)
        b = stronger_share(other, w)
        if np.isnan(a) or np.isnan(b):
            continue
        counted += 1
        wins += abs(b - TARGET) < abs(a - TARGET)
    return wins / counted if counted else float("nan")


def share_range(
    pairs_: Sequence[Pair], draws: Sequence[dict[str, float]], coverage: float = 0.9
) -> tuple[float, float]:
    """The middle `coverage` of the stronger player's share across rosters. The
    allowance has drifted from its target when this no longer holds `TARGET`."""
    shares = [stronger_share(pairs_, w) for w in draws]
    shares = [x for x in shares if not np.isnan(x)]
    tail = (1 - coverage) / 2
    lo, hi = np.quantile(shares, [tail, 1 - tail])
    return float(lo), float(hi)


def prediction_errors(cards: Sequence[Card], below: int) -> dict[str, list[float]]:
    """Actual less expected round total, by player, for rounds with fewer than `below`
    earlier rounds: how well a new player's index predicts."""
    out: dict[str, list[float]] = {}
    for c in cards:
        if c.prior_rounds < below:
            out.setdefault(c.player, []).append(sum(c.strokes) - c.expected)
    return out


def rmse(
    errors: dict[str, list[float]], weights: dict[str, float] | None = None
) -> float:
    num = den = 0.0
    for p, e in errors.items():
        w = 1.0 if weights is None else weights.get(p, 0)
        num += w * sum(x * x for x in e)
        den += w * len(e)
    return (num / den) ** 0.5 if den else float("nan")


def schemes(base: Parameters) -> list[Scheme]:
    """The default (the parameters file's rules) and each alternative the backtest
    weighs against it."""
    idx, hcp = base.index, base.handicap
    out = [Scheme("default", idx, hcp)]
    for allowance in (1.0, 0.95, 0.9, 0.85, 0.8):
        if allowance != hcp.allowance:
            out.append(
                Scheme(
                    f"allowance {allowance:.0%}", idx, replace(hcp, allowance=allowance)
                )
            )
    out.append(Scheme("stroke index by spread", idx, hcp, "spread"))
    for best_of, name in ((None, "last 8"), (20, "best 8 of 20"), (10, "best 8 of 10")):
        if best_of != idx.best_of:
            out.append(Scheme(name, replace(idx, best_of=best_of), hcp))
    if idx.exceptional is not None:
        out.append(
            Scheme("no exceptional reduction", replace(idx, exceptional=None), hcp)
        )
    out.append(Scheme("no caps", replace(idx, soft_cap=None, hard_cap=None), hcp))
    out.append(Scheme("low mark after 8 rounds", replace(idx, low_min_rounds=8), hcp))
    for start in (-5.0, 0.0, 4.5, 9.0):
        if start != idx.start_index:
            out.append(
                Scheme(f"start {start:+g}", replace(idx, start_index=start), hcp)
            )
    for weight in (0.0, 2.0, 6.0):
        if weight != idx.start_weight:
            out.append(
                Scheme(
                    f"start worth {weight:g}", replace(idx, start_weight=weight), hcp
                )
            )
    return out


def cap_binding(replayed: Sequence[Replayed], rules: IndexRules) -> tuple[int, int]:
    """Rounds whose index the low-mark caps changed, of rounds with a low mark."""
    free = replace(rules, soft_cap=None, hard_cap=None)
    bound = with_low = 0
    for r in replayed:
        state = compute(r.prior, rules)
        if state.low_mark is None:
            continue
        with_low += 1
        bound += abs(state.index - compute(r.prior, free).index) > 0.05
    return bound, with_low


@dataclass(frozen=True)
class Throw:
    player: str
    at: int
    #: strokes of index gained over the rounds after the throw, summed
    index_gained: float


def sandbag(
    replayed: Sequence[Replayed], rules: IndexRules, at: int, throw: int, horizon: int
) -> list[Throw]:
    """For each player with `at + horizon + 1` rounds: add `throw` strokes to round
    `at` (one on each of the first holes) and sum how much higher their index is before
    each of the next `horizon` rounds."""
    last: dict[str, Replayed] = {}
    for r in replayed:
        last[r.round.player] = r
    out = []
    for player, r in last.items():
        rounds = sorted(r.prior, key=lambda p: p.played_at)
        if len(rounds) < at + horizon + 1:
            continue
        thrown = list(rounds)
        bad = list(thrown[at].strokes)
        for h in range(throw):
            bad[h % len(bad)] += 1
        thrown[at] = replace(thrown[at], strokes=tuple(bad))
        gained = 0.0
        for j in range(at + 1, at + 1 + horizon):
            gained += (
                compute(thrown[:j], rules).index - compute(rounds[:j], rules).index
            )
        out.append(Throw(player, at, gained))
    return out


def errors_by(cards: Sequence[Card], min_prior: int) -> np.ndarray:
    return np.array(
        [sum(c.strokes) - c.expected for c in cards if c.prior_rounds >= min_prior]
    )


def reference_residuals(
    replayed: Sequence[Replayed], cards: Sequence[Card], reference: Sequence[Slot]
) -> dict[str, list[float]]:
    """Per hole on the reference holes as played (untransformed, any pin and wind):
    strokes less the day's model's expectation at the round's index, by index band."""
    terrains = {s.terrain for s in reference}
    out: dict[str, list[float]] = {}
    for r, c in zip(replayed, cards, strict=True):
        band = (
            "under 0"
            if c.index < 0
            else "0 to 10"
            if c.index < 10
            else "10 to 20"
            if c.index < 20
            else "20 and over"
        )
        for slot, strokes in zip(r.round.slots, r.round.strokes, strict=True):
            if slot.terrain in terrains and not slot.mirrored:
                out.setdefault(band, []).append(
                    strokes - r.model.expected(slot, c.index)
                )
    return out


def by_course(cards: Sequence[Card], min_prior: int) -> dict[str, list[float]]:
    """Round errors by the seed's par: where a slope is an extrapolation."""
    out: dict[str, list[float]] = {}
    for c in cards:
        if c.prior_rounds < min_prior:
            continue
        par = sum(c.pars)
        band = (
            "par 54 to 60"
            if par <= 60
            else "par 61 to 69"
            if par <= 69
            else "par 70 and over"
        )
        out.setdefault(band, []).append(sum(c.strokes) - c.expected)
    return out
