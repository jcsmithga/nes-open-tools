"""
A daily handicap and its strokes (docs/competitive/design.md, The daily handicap):

    course handicap = index * slope + course rating - par
    daily handicap  = round(allowance * course handicap)

Strokes go by stroke index: one on each hole from stroke index 1 until they run out, a
second lap past 18. A player better than scratch gives strokes back from stroke index 18.
Rounding is to the nearest whole stroke, a half up, as golf rounds.
"""

import math
from collections.abc import Sequence

from .seed import SeedRating


def round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def course_handicap(index: float, rating: SeedRating) -> float:
    """The strokes over par a player of this index is expected to take."""
    return index * rating.slope + rating.course_rating - rating.par


def daily_handicap(index: float, rating: SeedRating, allowance: float) -> int:
    return round_half_up(allowance * course_handicap(index, rating))


def strokes_received(handicap: int, stroke_indexes: Sequence[int]) -> list[int]:
    """Strokes on each hole for a whole-number handicap; negative gives them back."""
    holes = len(stroke_indexes)
    laps, rest = divmod(abs(handicap), holes)
    sign = 1 if handicap >= 0 else -1
    out = []
    for si in stroke_indexes:
        extra = si <= rest if handicap >= 0 else si > holes - rest
        out.append(sign * (laps + int(extra)))
    return out


def match_strokes(
    handicap_a: int, handicap_b: int, stroke_indexes: Sequence[int]
) -> tuple[list[int], list[int]]:
    """In a match the weaker player receives the difference, by stroke index."""
    diff = handicap_a - handicap_b
    none = [0] * len(stroke_indexes)
    if diff > 0:
        return strokes_received(diff, stroke_indexes), none
    return none, strokes_received(-diff, stroke_indexes)
