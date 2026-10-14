"""Hole transforms: the names a manifest slot uses for rewrites of its hole.

A transform's name carries its version (`mirror@1`), and a transform that takes a seed
writes it after a colon (`hazards@1:1234`). The rewrites themselves are plain hole
operations in `golf/algorithms/`; this module names them.

A golden test hashes every transform's output for the vanilla holes, as a regression
test: output that changes on purpose updates its digest, and needs a new transform
version only if what the transform means changed (ADR 0024, `docs/hole_transforms.md`).
A version stays in `TRANSFORMS` after a newer one ships, because the site still loads the
manifests that name it.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial

from golf.algorithms import hazards
from golf.algorithms.forest_fill import ForestFillError
from golf.algorithms.mirror import MirrorError, mirror_hole
from golf.formats.hole_data import HoleData

MAX_SEED = 0xFFFF_FFFF


class TransformError(ValueError):
    pass


@dataclass(frozen=True)
class Transform:
    #: what it does, for `golf-transform --list`
    summary: str
    #: (hole, seed) -> a new hole; the seed is None for a transform that takes none
    apply: Callable[[HoleData, int | None], HoleData]
    seeded: bool = False


def _unseeded(operation: Callable[[HoleData], HoleData]):
    return lambda hole, seed: operation(hole)


def _hazards(draw: hazards.Draw):
    def apply(hole: HoleData, seed: int | None) -> HoleData:
        assert seed is not None
        return hazards.redraw_hazards(hole, seed, draw)

    return apply


#: every transform a manifest may name. A hazard style is its own transform rather than
#: an argument to one, so each style's versions move independently
TRANSFORMS: dict[str, Transform] = {
    "mirror@1": Transform("flip the hole left to right", _unseeded(mirror_hole)),
    "hazards@1": Transform(
        "make each bunker or water hazard water with p = 0.35, otherwise sand",
        _hazards(hazards.uniform),
        seeded=True,
    ),
    "hazards-weighted@1": Transform(
        "flip each bunker or water hazard to the other kind, less likely the larger it is",
        _hazards(hazards.weighted),
        seeded=True,
    ),
}


def parse_transform(text: str) -> Callable[[HoleData], HoleData]:
    """The transform `text` names, with its seed bound."""
    name, colon, argument = text.partition(":")
    transform = TRANSFORMS.get(name)
    if transform is None:
        raise TransformError(f"unknown transform {text!r}")
    if not transform.seeded:
        if colon:
            raise TransformError(f"{name} takes no seed, got {text!r}")
        seed = None
    else:
        if not (
            argument.isdecimal()
            and str(int(argument)) == argument
            and int(argument) <= MAX_SEED
        ):
            raise TransformError(
                f"{name} takes a seed from 0 to {MAX_SEED} without leading zeros, "
                f"as {name}:<seed>; got {text!r}"
            )
        seed = int(argument)
    return partial(_apply, text, transform, seed)


def _apply(
    text: str, transform: Transform, seed: int | None, hole: HoleData
) -> HoleData:
    try:
        return transform.apply(hole, seed)
    except (MirrorError, ForestFillError) as problem:
        raise TransformError(f"{text}: {problem}") from None


def apply_transforms(hole: HoleData, names: Iterable[str]) -> HoleData:
    """`hole` with each named transform applied in order."""
    for name in names:
        hole = parse_transform(name)(hole)
    return hole
