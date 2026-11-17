"""
Estimating the hole model's own numbers offline (docs/competitive/design.md, Fitting):
the luck on a hole, how much each group really varies, and gamma, together, by
expectation-maximization run to convergence. It takes what CPU it needs; the daily refit
only reads the result.

Each EM step solves every effect with the spreads and penalties held, then sets

    sigma^2 = (|r|^2 + sigma^2 tr(C X'X)) / n
    tau_g^2 = (|u_g|^2 + sigma^2 tr(C_gg)) / n_g
    gamma   = least squares of the residual on (rating - reference par) * skill

where C is the inverse of the penalized normal matrix under the reference constraint
(the effects' covariance over sigma^2). The trace terms add back the spread that
shrinkage hid.

Also here: how certain gamma is, by refitting it on players resampled with replacement;
whether the mirrored term can be told from zero; and how far course ratings move when
each player has a skill per month.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import numpy as np

from .model import Model, Observation, Slot, System, fit, iterate, model_from
from .params import BASE_GROUPS, MONTH, ModelParams

#: where EM starts: the spikes' estimates
START = ModelParams(
    sigma2=1.12,
    tau2={"skill": 0.81, "layout": 0.09, "terrain": 0.07, "pin": 0.01},
    gamma=0.4,
)


#: past this a par 3 rated near 3 has a spread near zero or below, and players stop
#: being separated by skill at all
MAX_GAMMA = 0.95


@dataclass(frozen=True)
class Estimate:
    params: ModelParams
    model: Model
    iterations: int
    converged: bool
    #: the mirrored term and its standard error, if the fit has one
    mirrored: tuple[float, float] | None


def _gamma_step(system: System, beta: np.ndarray) -> float:
    """Least squares gamma with every effect held."""
    a = system.rating(beta)
    s = system.skill(beta)
    z = (a - system.ref_par) * s
    r0 = system.y - a - s
    return float(z @ r0 / (z @ z))


def _theta(params: ModelParams, groups: Sequence[str]) -> np.ndarray:
    return np.array(
        [
            math.log(params.sigma2),
            *(math.log(params.tau2[g]) for g in groups),
            params.gamma,
        ]
    )


def _params(
    theta: np.ndarray, template: ModelParams, groups: Sequence[str]
) -> ModelParams:
    return replace(
        template,
        sigma2=math.exp(theta[0]),
        tau2={g: math.exp(t) for g, t in zip(groups, theta[1:-1], strict=True)},
        gamma=float(theta[-1]),
    )


class _EMMap:
    """One EM step as a map on the parameters: the effects at the spreads' fixed point
    under the current parameters, then the updates."""

    def __init__(self, system: System):
        self.system = system
        self.spread: np.ndarray | None = None
        self.calls = 0

    def __call__(self, params: ModelParams) -> ModelParams:
        self.calls += 1
        system = self.system
        system.set_params(params)
        beta, _ = iterate(system, params.gamma, spread=self.spread)
        spread = system.spread(beta, params.gamma)
        self.spread = spread
        xtx, xty = system.normal(spread)
        inv = np.linalg.inv(system.kkt(xtx + np.diag(system.penalty)))
        beta = inv[:-1] @ np.append(xty, system.ref_par)
        cov = inv[:-1, :-1]
        resid = system.residual(beta, spread)
        n = len(system.y)
        sigma2 = params.sigma2
        tau2 = {}
        for g, (lo, hi) in system.blocks.items():
            u = beta[lo:hi]
            tau2[g] = float(u @ u + sigma2 * np.trace(cov[lo:hi, lo:hi])) / (hi - lo)
        return replace(
            params,
            sigma2=float(resid @ resid + sigma2 * np.sum(cov * xtx)) / n,
            tau2=tau2,
            gamma=_gamma_step(system, beta),
        )


def em(
    observations: Sequence[Observation],
    reference: Sequence[Slot],
    start: ModelParams = START,
    month: bool = False,
    max_iter: int = 2000,
    tol: float = 1e-6,
    progress: Callable[[int, ModelParams], None] | None = None,
) -> Estimate:
    """Every parameter together, to convergence: an EM step moving every log variance
    and gamma by less than `tol`. Accelerated by SQUAREM (Varadhan and Roland, 2008),
    which extrapolates along two EM steps and falls back on plain EM if the jump
    fails."""
    tau2 = dict(start.tau2)
    tau2.pop(MONTH, None)
    if month:
        tau2[MONTH] = start.tau2.get(MONTH, tau2["skill"] / 4)
    params = replace(start, tau2=tau2)
    groups = params.groups
    system = System(observations, params, reference)
    step = _EMMap(system)
    converged = False
    while step.calls < max_iter and not converged:
        p1 = step(params)
        p2 = step(p1)
        t0, t1, t2 = (_theta(p, groups) for p in (params, p1, p2))
        r = t1 - t0
        v = t2 - t1 - r
        nxt = p2
        if np.linalg.norm(v) > 0:
            alpha = min(-1.0, -float(np.linalg.norm(r) / np.linalg.norm(v)))
            jump = t0 - 2 * alpha * r + alpha**2 * v
            try:
                nxt = step(_params(jump, params, groups))
            except (RuntimeError, ValueError, np.linalg.LinAlgError, OverflowError):
                step.spread = None
                nxt = p2
        moved = step(nxt)
        converged = bool(
            np.max(np.abs(_theta(moved, groups) - _theta(nxt, groups))) < tol
        )
        params = moved
        if progress:
            progress(step.calls, params)
    system.set_params(params)
    beta, inner = iterate(system, params.gamma)
    model = model_from(system, beta, params, inner)
    mirrored = _mirrored(system, beta) if params.mirror_term else None
    return Estimate(params, model, step.calls, converged, mirrored)


def _mirrored(system: System, beta: np.ndarray) -> tuple[float, float]:
    col = system.fixed_names.index("mirrored")
    xtx, _ = system.normal(system.spread(beta, system.params.gamma))
    inv = np.linalg.inv(system.kkt(xtx + np.diag(system.penalty)))
    return float(beta[col]), math.sqrt(system.params.sigma2 * inv[col, col])


def mirrored_term(
    observations: Sequence[Observation], reference: Sequence[Slot], params: ModelParams
) -> tuple[float, float]:
    """The mirrored term and its standard error, with the parameters held. The error
    treats every hole as independent, so it is too small when a player's form varies
    from round to round; `mirrored_paired` is the test to trust."""
    system = System(observations, replace(params, mirror_term=True), reference)
    beta, _ = iterate(system, params.gamma)
    return _mirrored(system, beta)


@dataclass(frozen=True)
class Paired:
    rounds: int
    #: mean of (mirrored less unmirrored residual) per round, per hole, and its error
    mean: float
    se: float
    #: two-sided p from flipping each round's sign
    p: float


def mirrored_paired(
    rounds: Sequence[Sequence[Observation]],
    reference: Sequence[Slot],
    params: ModelParams,
    flips: int = 20000,
    seed: int = 0,
) -> Paired:
    """Whether a mirror-only hole plays differently from its twin, compared within
    rounds: in each round with both kinds of hole on twinned terrain, the mirrored
    holes' mean residual less the unmirrored holes', against a fit without the mirrored
    term. Pairing within a round cancels the player's form that day and when they
    played, which the fit's one skill per player cannot."""
    flat = [o for r in rounds for o in r]
    model = fit(flat, replace(params, mirror_term=False), reference)
    twinned = {o.slot.terrain for o in flat if o.slot.mirrored}
    diffs = []
    for r in rounds:
        sides: tuple[list[float], list[float]] = ([], [])
        for o in r:
            if o.slot.terrain in twinned:
                index = model.index(o.player)
                sides[o.slot.mirrored].append(o.strokes - model.expected(o.slot, index))
        if sides[0] and sides[1]:
            diffs.append(float(np.mean(sides[1]) - np.mean(sides[0])))
    d = np.array(diffs)
    mean = float(d.mean())
    rng = np.random.default_rng(seed)
    null = np.array([(d * rng.choice([-1, 1], len(d))).mean() for _ in range(flips)])
    return Paired(
        rounds=len(d),
        mean=mean,
        se=float(d.std(ddof=1) / math.sqrt(len(d))),
        p=float(np.mean(np.abs(null) >= abs(mean))),
    )


def fit_gamma(
    observations: Sequence[Observation],
    reference: Sequence[Slot],
    params: ModelParams,
    width: float = 1.0,
    tol: float = 1e-3,
) -> float:
    """Gamma alone, with the variances held: the gamma that minimizes the penalized
    fit at its spreads' fixed point, by golden-section search within `width` of the
    estimate, below `MAX_GAMMA`. A gamma whose spreads do not settle scores as
    infinitely bad. Alternating single gamma steps with solves crawls, since a change in
    gamma rescales every skill."""
    system = System(observations, params, reference)
    state: dict[str, np.ndarray | None] = {"spread": None}

    def objective(gamma: float) -> float:
        try:
            beta, _ = iterate(system, gamma, spread=state["spread"])
        except RuntimeError:
            state["spread"] = None
            return math.inf
        spread = system.spread(beta, gamma)
        state["spread"] = spread
        resid = system.residual(beta, spread)
        return float(resid @ resid + np.sum(system.penalty * beta * beta))

    ratio = (math.sqrt(5) - 1) / 2
    lo, hi = max(0.0, params.gamma - width), min(MAX_GAMMA, params.gamma + width)
    a, b = hi - ratio * (hi - lo), lo + ratio * (hi - lo)
    fa, fb = objective(a), objective(b)
    while hi - lo > tol:
        if fa < fb:
            hi, b, fb = b, a, fa
            a = hi - ratio * (hi - lo)
            fa = objective(a)
        else:
            lo, a, fa = a, b, fb
            b = lo + ratio * (hi - lo)
            fb = objective(b)
    return (lo + hi) / 2


def resample_players(
    observations: Sequence[Observation], rng: np.random.Generator
) -> list[Observation]:
    """A roster drawn from the players with replacement; a player drawn twice plays
    twice, under two names."""
    by_player: dict[str, list[Observation]] = {}
    for o in observations:
        by_player.setdefault(o.player, []).append(o)
    players = sorted(by_player)
    drawn = rng.choice(len(players), len(players))
    out = []
    for copy, i in enumerate(drawn):
        for o in by_player[players[i]]:
            out.append(replace(o, player=f"{o.player}#{copy}"))
    return out


def gamma_interval(
    observations: Sequence[Observation],
    reference: Sequence[Slot],
    params: ModelParams,
    replicates: int,
    seed: int = 1,
    progress: Callable[[int], None] | None = None,
) -> np.ndarray:
    """Gamma refitted on `replicates` resampled rosters; NaN where a fit fails."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(replicates):
        roster = resample_players(observations, rng)
        try:
            out.append(fit_gamma(roster, reference, params))
        except (RuntimeError, ValueError, np.linalg.LinAlgError):
            out.append(float("nan"))
        if progress:
            progress(i + 1)
    return np.array(out)


def course_rating(model: Model, slots: Sequence[Slot]) -> float:
    return sum(model.rating(s)[0] for s in slots)


def without_group(params: ModelParams, group: str) -> ModelParams:
    tau2 = {g: v for g, v in params.tau2.items() if g != group}
    return replace(params, tau2=tau2)


__all__ = [
    "BASE_GROUPS",
    "START",
    "Estimate",
    "course_rating",
    "em",
    "fit_gamma",
    "gamma_interval",
    "resample_players",
    "without_group",
]
