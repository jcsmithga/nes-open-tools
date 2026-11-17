"""
Reading net scores (docs/competitive/design.md, Formats): Stableford points and match
play. Skins, the standing and the field's results come with the daily's results.
"""

from collections.abc import Sequence


def stableford(
    strokes: Sequence[int], pars: Sequence[int], received: Sequence[int]
) -> list[int]:
    """2 for a net par, 1 more for each stroke under, 1 fewer for each over, never
    below 0."""
    return [
        max(0, 2 + par - (s - r))
        for s, par, r in zip(strokes, pars, received, strict=True)
    ]


def match(net_a: Sequence[int], net_b: Sequence[int]) -> tuple[int, int]:
    """Holes won by each player."""
    won_a = sum(a < b for a, b in zip(net_a, net_b, strict=True))
    won_b = sum(b < a for a, b in zip(net_a, net_b, strict=True))
    return won_a, won_b


def net(strokes: Sequence[int], received: Sequence[int]) -> list[int]:
    return [s - r for s, r in zip(strokes, received, strict=True)]
