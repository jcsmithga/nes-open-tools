"""
Reading net scores (docs/competitive/design.md, Formats): Stableford points, match play,
skins and the standing, and a daily's results from its field's cards.

Each format is a function of the field's scores, so one can be added without touching
the others. Players are any hashable keys.
"""

from collections.abc import Hashable, Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from .handicap import match_strokes
from .rules import MATCHES, SKINS, STANDING, DailyRules


def stableford(
    strokes: Sequence[int],
    pars: Sequence[int],
    received: Sequence[int],
    par_points: int = 2,
) -> list[int]:
    """`par_points` for a net par, 1 more for each stroke under, 1 fewer for each over,
    never below 0."""
    return [
        max(0, par_points + par - (s - r))
        for s, par, r in zip(strokes, pars, received, strict=True)
    ]


def match(net_a: Sequence[int], net_b: Sequence[int]) -> tuple[int, int]:
    """Holes won by each player."""
    won_a = sum(a < b for a, b in zip(net_a, net_b, strict=True))
    won_b = sum(b < a for a, b in zip(net_a, net_b, strict=True))
    return won_a, won_b


def net(strokes: Sequence[int], received: Sequence[int]) -> list[int]:
    return [s - r for s, r in zip(strokes, received, strict=True)]


def countback_key(points: Sequence[int], stretches: Sequence[int]) -> tuple[int, ...]:
    """What a standing sorts on, best first when larger: the total points, then the
    points over each of the last `stretches` holes in turn."""
    return (sum(points), *(sum(points[len(points) - n :]) for n in stretches))


def standing[P: Hashable](
    points: Mapping[P, Sequence[int]], countback: Sequence[int]
) -> dict[P, int]:
    """Each player's position by Stableford points, ties broken by countback; players
    still tied share the position, and the next takes the place after them."""
    keys = {p: countback_key(pts, countback) for p, pts in points.items()}
    return {
        p: 1 + sum(other > key for other in keys.values()) for p, key in keys.items()
    }


def skin_holes[P: Hashable](
    nets: Mapping[P, Sequence[int]], carryover: bool = True
) -> list[tuple[P | None, int]]:
    """Each hole's skins: who won it, or None, and how many skins it was worth to them,
    1 plus any carried to it. A hole goes to the one player with the lowest net score
    on it. A shared lowest score wins nobody the skin; with `carryover`, it rides on the
    next hole, and skins still carried after the last hole are won by nobody."""
    out: list[tuple[P | None, int]] = []
    carried = 0
    holes = len(next(iter(nets.values()), ()))
    for hole in range(holes):
        scores = {p: n[hole] for p, n in nets.items()}
        low = min(scores.values())
        leaders = [p for p, s in scores.items() if s == low]
        if len(leaders) == 1:
            out.append((leaders[0], 1 + carried))
            carried = 0
        else:
            out.append((None, 0))
            carried = carried + 1 if carryover else 0
    return out


def skins[P: Hashable](
    nets: Mapping[P, Sequence[int]], carryover: bool = True
) -> dict[P, int]:
    """Skins won by each player (`skin_holes`)."""
    won = dict.fromkeys(nets, 0)
    for winner, value in skin_holes(nets, carryover):
        if winner is not None:
            won[winner] += value
    return won


@dataclass(frozen=True)
class FieldCard:
    """One player's round on a daily, as the results read it."""

    player: Hashable
    strokes: tuple[int, ...]
    handicap: int
    #: strokes received and Stableford points on each hole, from the daily handicap
    nets: tuple[int, ...]
    points: tuple[int, ...]


@dataclass(frozen=True)
class PlayerResult:
    points: int
    position: int
    skins: int
    won: bool


@dataclass(frozen=True)
class MatchResult:
    a: Hashable
    b: Hashable
    holes_a: int
    holes_b: int

    @property
    def winner(self) -> Hashable | None:
        if self.holes_a == self.holes_b:
            return None
        return self.a if self.holes_a > self.holes_b else self.b

    @property
    def margin(self) -> int:
        return abs(self.holes_a - self.holes_b)


@dataclass(frozen=True)
class DayResults:
    players: dict[Hashable, PlayerResult]
    matches: list[MatchResult]


def day_results(
    cards: Sequence[FieldCard], stroke_indexes: Sequence[int], rules: DailyRules
) -> DayResults:
    """A daily's results under each of its formats. The day is won, and skins are won,
    only in a field of at least `rules.min_field`. A match plays all 18 holes, the weaker
    player receiving the difference of the two daily handicaps by stroke index."""
    big_enough = len(cards) >= rules.min_field
    positions = (
        standing({c.player: c.points for c in cards}, rules.countback)
        if STANDING in rules.formats
        else {}
    )
    won_skins = (
        skins({c.player: c.nets for c in cards}, rules.skins_carryover)
        if SKINS in rules.formats and big_enough
        else {}
    )
    players = {
        c.player: PlayerResult(
            points=sum(c.points),
            position=positions.get(c.player, 0),
            skins=won_skins.get(c.player, 0),
            won=big_enough and positions.get(c.player) == 1,
        )
        for c in cards
    }
    matches = []
    if MATCHES in rules.formats:
        for a, b in combinations(cards, 2):
            given_a, given_b = match_strokes(a.handicap, b.handicap, stroke_indexes)
            holes_a, holes_b = match(net(a.strokes, given_a), net(b.strokes, given_b))
            matches.append(MatchResult(a.player, b.player, holes_a, holes_b))
    return DayResults(players, matches)
