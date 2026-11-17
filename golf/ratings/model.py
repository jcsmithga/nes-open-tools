"""
The hole model: one equation per hole score.

    strokes = rating(hole) + spread(hole) * (field + skill(player)) + luck
    rating(hole) = fixed terms (par, distance, wind, mirrored) + layout + terrain + pin
    spread(hole) = 1 + gamma * (rating(hole) - mean par of the reference holes)

`rating` is what an index-0 player is expected to take on the hole, and `field + skill`
is a player's skill in strokes per hole against index 0, so their index is 18 times it.
Index 0 is pinned by a constraint: the ratings average par over the reference holes
(`golf.ratings.reference`), so their mean spread is exactly 1. See
docs/competitive/design.md, The reference.

Skill, layout, terrain and pin (and, optionally, a player's month) are random effects,
ridge-shrunk toward zero with penalties sigma^2 / tau^2 from `ModelParams`; `field`, the
field's mean skill, is not, so a thin rating is pulled toward the field, not toward
scratch. Pin is nested in terrain, terrain in layout, so a thin level falls back on its
parent. A level the fit never saw contributes nothing, so a hole nobody has played is
rated from its baseline.

With the parameters fixed, the fit alternates two steps until the spreads settle: solve
every effect jointly, under the constraint, with the spreads held; then recompute the
spreads from the ratings. `golf.ratings.estimate` wraps the same system in EM to
estimate the parameters.
"""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .params import MONTH, ModelParams

FIXED = ("intercept", "par4", "par5", "distance", "wind", "mirrored")
HOLE_GROUPS = ("layout", "terrain", "pin")
SKILL_GROUPS = ("skill", MONTH)
#: the tiny ridge on the unpenalized terms, for a well-posed solve
FIXED_PENALTY = 1e-8


@dataclass(frozen=True)
class Slot:
    """One hole as played: what the hole terms are keyed on."""

    hole_id: str
    #: the data hash of the hole as built; a mirror-only hole has its unmirrored
    #: twin's, so the two are rated as one hole (`golf.ratings.sitedb.terrain_key`)
    terrain: str
    pin: int
    par: int
    distance: int
    wind: int
    #: mirror-only, pooled with its twin; the mirrored term measures the flip
    mirrored: bool = False

    def level(self, group: str) -> str:
        if group == "layout":
            return self.hole_id
        if group == "terrain":
            return self.terrain
        return f"{self.terrain}:{self.pin}"


@dataclass(frozen=True)
class Observation:
    """One hole score."""

    player: str
    #: the calendar month it was played, `YYYY-MM`, for the month group
    month: str
    slot: Slot
    strokes: int


def distance_centers(reference: Sequence[Slot]) -> dict[int, float]:
    """The mean distance of the reference holes of each par: distance is centered
    within its par, so the par terms read as a hole of typical length."""
    by_par: dict[int, list[int]] = {}
    for slot in reference:
        by_par.setdefault(slot.par, []).append(slot.distance)
    return {par: float(np.mean(d)) for par, d in by_par.items()}


def fixed_matrix(
    slots: Sequence[Slot], centers: dict[int, float], mirror_term: bool
) -> np.ndarray:
    par = np.array([s.par for s in slots])
    overall = float(np.mean(list(centers.values())))
    center = np.array([centers.get(s.par, overall) for s in slots])
    dist = (np.array([s.distance for s in slots], float) - center) / 100
    cols = [
        np.ones(len(slots)),
        par == 4,
        par == 5,
        dist,
        np.array([s.wind for s in slots], float),
    ]
    if mirror_term:
        cols.append(np.array([s.mirrored for s in slots], float))
    return np.column_stack(cols).astype(float).reshape(len(slots), len(cols))


class System:
    """The fit's design, kept sparse. Columns: the fixed terms, the field's skill, then
    each group's levels. A row has its fixed values and one level in each group; the
    field and skill groups' value in a row is that hole's spread."""

    def __init__(
        self,
        observations: Sequence[Observation],
        params: ModelParams,
        reference: Sequence[Slot],
    ):
        self.params = params
        self.groups = params.groups
        self.centers = distance_centers(reference)
        slots = [o.slot for o in observations]
        self.y = np.array([o.strokes for o in observations], float)
        self.fixed = fixed_matrix(slots, self.centers, params.mirror_term)
        self.n_fixed = self.fixed.shape[1]
        self.fixed_names = FIXED[: self.n_fixed]
        self.field = self.n_fixed

        keys = {
            "skill": [o.player for o in observations],
            MONTH: [f"{o.player}@{o.month}" for o in observations],
        } | {g: [s.level(g) for s in slots] for g in HOLE_GROUPS}
        self.levels: dict[str, list[str]] = {}
        self.blocks: dict[str, tuple[int, int]] = {}
        self.lookups: dict[str, dict[str, int]] = {}
        index_cols = []
        offset = self.n_fixed + 1
        for g in self.groups:
            levels = sorted(set(keys[g]))
            self.levels[g] = levels
            self.blocks[g] = (offset, offset + len(levels))
            self.lookups[g] = {v: offset + i for i, v in enumerate(levels)}
            index_cols.append(np.array([self.lookups[g][k] for k in keys[g]], int))
            offset += len(levels)
        self.size = offset
        n = len(slots)
        self.cols = np.hstack(
            [
                np.tile(np.arange(self.n_fixed + 1), (n, 1)),
                np.column_stack(index_cols),
            ]
        )
        first = self.n_fixed + 1
        self.skill_positions = [self.field] + [
            first + i for i, g in enumerate(self.groups) if g in SKILL_GROUPS
        ]
        self.hole_positions = [
            first + i for i, g in enumerate(self.groups) if g in HOLE_GROUPS
        ]
        self.set_params(params)

        # The constraint: the ratings average par over the reference holes.
        self.ref_par = float(np.mean([s.par for s in reference]))
        self.constraint = np.zeros(self.size)
        ref_fixed = fixed_matrix(reference, self.centers, params.mirror_term)
        self.constraint[: self.n_fixed] = ref_fixed.mean(axis=0)
        for g in HOLE_GROUPS:
            for slot in reference:
                col = self.lookups[g].get(slot.level(g))
                if col is not None:
                    self.constraint[col] += 1 / len(reference)

    def set_params(self, params: ModelParams) -> None:
        """New penalties for the same groups."""
        self.params = params
        self.penalty = np.full(self.size, FIXED_PENALTY)
        for g, (lo, hi) in self.blocks.items():
            self.penalty[lo:hi] = params.penalty(g)

    def values(self, spread: np.ndarray) -> np.ndarray:
        vals = np.hstack([self.fixed, np.ones((len(self.y), 1 + len(self.groups)))])
        for p in self.skill_positions:
            vals[:, p] = spread
        return vals

    def normal(self, spread: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """X'X and X'y for the given spreads, without the penalty."""
        vals = self.values(spread)
        xtx = np.zeros((self.size, self.size))
        width = self.cols.shape[1]
        for j in range(width):
            for k in range(j, width):
                np.add.at(
                    xtx, (self.cols[:, j], self.cols[:, k]), vals[:, j] * vals[:, k]
                )
        xtx = xtx + np.triu(xtx, 1).T
        xty = np.zeros(self.size)
        for j in range(width):
            np.add.at(xty, self.cols[:, j], vals[:, j] * self.y)
        return xtx, xty

    def kkt(self, matrix: np.ndarray) -> np.ndarray:
        """The penalized normal matrix bordered by the constraint."""
        out = np.zeros((self.size + 1, self.size + 1))
        out[: self.size, : self.size] = matrix
        out[: self.size, -1] = self.constraint
        out[-1, : self.size] = self.constraint
        return out

    def solve(self, spread: np.ndarray) -> np.ndarray:
        xtx, xty = self.normal(spread)
        rhs = np.append(xty, self.ref_par)
        return np.linalg.solve(self.kkt(xtx + np.diag(self.penalty)), rhs)[:-1]

    def rating(self, beta: np.ndarray) -> np.ndarray:
        a = self.fixed @ beta[: self.n_fixed]
        for p in self.hole_positions:
            a = a + beta[self.cols[:, p]]
        return a

    def skill(self, beta: np.ndarray) -> np.ndarray:
        s = np.zeros(len(self.y))
        for p in self.skill_positions:
            s = s + beta[self.cols[:, p]]
        return s

    def spread(self, beta: np.ndarray, gamma: float) -> np.ndarray:
        return 1 + gamma * (self.rating(beta) - self.ref_par)

    def residual(self, beta: np.ndarray, spread: np.ndarray) -> np.ndarray:
        return self.y - self.rating(beta) - spread * self.skill(beta)


@dataclass(frozen=True)
class Model:
    """A fitted hole model, with the parameters it was fitted under."""

    params: ModelParams
    centers: dict[int, float]
    fixed: dict[str, float]
    effects: dict[str, dict[str, float]]
    #: the field's mean skill, and each player's skill around it, in strokes per hole
    #: against index 0; with the month group, before their month's effect
    field: float
    skills: dict[str, float]
    #: the mean par of the reference holes, where the spread is 1
    reference_par: float
    sigma: float
    iterations: int
    n_scores: int

    def rating(self, slot: Slot) -> tuple[float, float]:
        """The hole's rating, the strokes an index-0 player is expected to take, and
        its spread factor."""
        row = fixed_matrix([slot], self.centers, self.params.mirror_term)[0]
        names = FIXED[: len(row)]
        a = float(sum(self.fixed[n] * v for n, v in zip(names, row, strict=True)))
        for g in HOLE_GROUPS:
            a += self.effects[g].get(slot.level(g), 0.0)
        return a, 1 + self.params.gamma * (a - self.reference_par)

    def expected(self, slot: Slot, index: float) -> float:
        """Strokes a player of this index is expected to take."""
        rating, spread = self.rating(slot)
        return rating + spread * index / 18

    def index(self, player: str) -> float:
        """The player's index from every round the fit saw (not a handicap index)."""
        return 18 * (self.field + self.skills[player])


def model_from(
    system: System, beta: np.ndarray, params: ModelParams, iterations: int
) -> Model:
    effects = {}
    for g in system.groups:
        a, b = system.blocks[g]
        effects[g] = dict(zip(system.levels[g], beta[a:b].tolist(), strict=True))
    resid = system.residual(beta, system.spread(beta, params.gamma))
    return Model(
        params=params,
        centers=system.centers,
        fixed=dict(
            zip(system.fixed_names, beta[: system.n_fixed].tolist(), strict=True)
        ),
        effects={g: effects[g] for g in HOLE_GROUPS},
        field=float(beta[system.field]),
        skills=effects["skill"],
        reference_par=system.ref_par,
        sigma=float(resid.std()),
        iterations=iterations,
        n_scores=len(system.y),
    )


def iterate(
    system: System,
    gamma: float,
    max_iter: int = 200,
    tol: float = 1e-9,
    spread: np.ndarray | None = None,
) -> tuple[np.ndarray, int]:
    """The fixed point of the spreads under `gamma`, from `spread` (default 1)."""
    spread = np.ones(len(system.y)) if spread is None else spread
    beta = system.solve(spread)
    for iterations in range(1, max_iter + 1):
        new = system.spread(beta, gamma)
        if np.max(np.abs(new - spread)) < tol:
            return beta, iterations
        spread = new
        beta = system.solve(spread)
    raise RuntimeError(f"the spreads did not settle in {max_iter} iterations")


def fit(
    observations: Sequence[Observation],
    params: ModelParams,
    reference: Sequence[Slot],
    max_iter: int = 200,
) -> Model:
    """Fit the hole model with its parameters fixed."""
    if not observations:
        raise ValueError("no hole scores to fit")
    system = System(observations, params, reference)
    beta, iterations = iterate(system, params.gamma, max_iter)
    return model_from(system, beta, params, iterations)
