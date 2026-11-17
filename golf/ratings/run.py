"""
A fitted hole model as a ratings run keeps it, and the model rebuilt from it
(docs/competitive/data_model.md, Ratings runs).

A run keeps the model's parts, not each hole's finished rating: a hole's rating depends
on its slot's wind, and a level the run never saw falls back on its parent (an unseen
terrain on its layout, an unseen pin on its terrain), so a rebuilt model rates a new seed
exactly as the fitted one would. Players' skills are not kept: rating a seed does not
use them, and an index comes from a player's rounds, not from the fit.
"""

from dataclasses import dataclass

from .model import HOLE_GROUPS, Model
from .params import ModelParams

#: bumped when the model's equations change, so a run says which ones it was fitted by
MODEL_VERSION = 1


@dataclass(frozen=True)
class StoredModel:
    """What a ratings run keeps of a fitted model."""

    fixed: dict[str, float]
    #: the reference holes' mean distance for each par
    centers: dict[int, float]
    reference_par: float
    field: float
    sigma: float
    iterations: int
    n_scores: int
    #: every fitted effect, by group (layout, terrain, pin) and level
    effects: dict[str, dict[str, float]]

    def header(self) -> dict:
        """The run's own numbers, as JSON-ready values; the effects are rows apart."""
        return {
            "fixed": dict(self.fixed),
            "centers": {str(par): c for par, c in sorted(self.centers.items())},
            "reference_par": self.reference_par,
            "field": self.field,
            "sigma": self.sigma,
            "iterations": self.iterations,
            "n_scores": self.n_scores,
        }

    def effect_rows(self) -> list[tuple[str, str, float]]:
        return [
            (group, level, value)
            for group in HOLE_GROUPS
            for level, value in sorted(self.effects.get(group, {}).items())
        ]

    @classmethod
    def from_parts(
        cls, header: dict, rows: list[tuple[str, str, float]]
    ) -> "StoredModel":
        effects: dict[str, dict[str, float]] = {g: {} for g in HOLE_GROUPS}
        for group, level, value in rows:
            effects[group][level] = value
        return cls(
            fixed=dict(header["fixed"]),
            centers={int(par): c for par, c in header["centers"].items()},
            reference_par=header["reference_par"],
            field=header["field"],
            sigma=header["sigma"],
            iterations=header["iterations"],
            n_scores=header["n_scores"],
            effects=effects,
        )


def stored(model: Model) -> StoredModel:
    return StoredModel(
        fixed=dict(model.fixed),
        centers=dict(model.centers),
        reference_par=model.reference_par,
        field=model.field,
        sigma=model.sigma,
        iterations=model.iterations,
        n_scores=model.n_scores,
        effects={g: dict(model.effects.get(g, {})) for g in HOLE_GROUPS},
    )


def rebuilt(kept: StoredModel, params: ModelParams) -> Model:
    """The model a run was fitted as, without its players' skills."""
    return Model(
        params=params,
        centers=dict(kept.centers),
        fixed=dict(kept.fixed),
        effects={g: dict(kept.effects.get(g, {})) for g in HOLE_GROUPS},
        field=kept.field,
        skills={},
        reference_par=kept.reference_par,
        sigma=kept.sigma,
        iterations=kept.iterations,
        n_scores=kept.n_scores,
    )
