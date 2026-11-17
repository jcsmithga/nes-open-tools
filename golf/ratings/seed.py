"""
Rating a seed from a fitted hole model: each hole's rating and spread factor, the course
rating and slope, and the stroke indexes (docs/competitive/design.md, Rating holes and
courses, and The daily handicap).

A hole nobody has played is rated from the model's baseline: its par, distance and wind.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from .model import Model, Slot


@dataclass(frozen=True)
class HoleRating:
    par: int
    #: strokes an index-0 player is expected to take
    rating: float
    spread: float
    #: 1 for the hole that receives the first handicap stroke, to 18
    stroke_index: int


@dataclass(frozen=True)
class SeedRating:
    holes: tuple[HoleRating, ...]

    @property
    def par(self) -> int:
        return sum(h.par for h in self.holes)

    @property
    def course_rating(self) -> float:
        """What an index-0 player is expected to shoot."""
        return sum(h.rating for h in self.holes)

    @property
    def slope(self) -> float:
        """The mean spread factor: 1.0 separates players as the reference holes do."""
        return sum(h.spread for h in self.holes) / len(self.holes)

    def expected(self, index: float) -> float:
        """What a player of this index is expected to shoot."""
        return self.course_rating + self.slope * index


def stroke_indexes(
    ratings: Sequence[tuple[int, float, float]], by: str = "par"
) -> list[int]:
    """Stroke index for each hole of (par, rating, spread): hardest for its par first,
    ties by spread, then by position. `by="spread"` ranks by spread factor alone, the
    order the backtest compares against."""
    if by == "par":
        key = [
            (-(rating - par), -spread, i)
            for i, (par, rating, spread) in enumerate(ratings)
        ]
    elif by == "spread":
        key = [
            (-spread, -(rating - par), i)
            for i, (par, rating, spread) in enumerate(ratings)
        ]
    else:
        raise ValueError(f"unknown stroke index order {by!r}")
    order = sorted(range(len(ratings)), key=lambda i: key[i])
    out = [0] * len(ratings)
    for rank, i in enumerate(order, start=1):
        out[i] = rank
    return out


def rate_seed(model: Model, slots: Sequence[Slot], by: str = "par") -> SeedRating:
    rated = [(s.par, *model.rating(s)) for s in slots]
    indexes = stroke_indexes(rated, by)
    return SeedRating(
        tuple(
            HoleRating(par, rating, spread, si)
            for (par, rating, spread), si in zip(rated, indexes, strict=True)
        )
    )
