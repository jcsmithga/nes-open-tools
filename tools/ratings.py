"""
golf-ratings: the competitive ratings from a copy of the site database.

    golf-ratings fit DB         every player's index and the vanilla courses' ratings
    golf-ratings estimate DB    estimate the model's parameters offline (--write saves them)
    golf-ratings backtest DB    replay the database day by day and weigh the rules

The design is docs/competitive/design.md, Handicaps; the logic is in golf/ratings/.
"""

import argparse
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np

from golf.randomizer.catalog import JP_ROM, US_ROM, Catalog
from golf.ratings import backtest, estimate, sitedb
from golf.ratings.index import ScoredRound, compute
from golf.ratings.model import fit
from golf.ratings.params import DEFAULT_PATH, Parameters
from golf.ratings.reference import course_slots, reference_slots
from golf.ratings.seed import rate_seed

VANILLA_COURSES = [
    (US_ROM, "us"),
    (US_ROM, "uk"),
    (US_ROM, "japan"),
    (JP_ROM, "jp_japan"),
    (JP_ROM, "jp_uk"),
    (JP_ROM, "jp_australia"),
    (JP_ROM, "jp_france"),
    (JP_ROM, "jp_hawaii"),
]


def _load(args) -> tuple[Catalog, sitedb.SiteData, Parameters, list]:
    catalog = Catalog.load()
    data = sitedb.load(args.db, catalog)
    params = Parameters.load(args.params)
    return catalog, data, params, reference_slots(catalog)


def courses(catalog: Catalog, model) -> None:
    print("vanilla courses, untransformed, pins averaged, no wind:")
    for rom, course in VANILLA_COURSES:
        slots = course_slots(catalog, rom, course)
        if not slots:
            continue
        rated = [model.rating(s) for s in slots]
        pins = len(slots) / 18
        cr = sum(r for r, _ in rated) / pins
        slope = sum(b for _, b in rated) / len(rated)
        par = sum(s.par for s in slots) / pins
        print(
            f"  {course:13} par {par:.0f}  course rating {cr:5.1f}  slope {slope:.2f}"
        )


def cmd_fit(args) -> None:
    catalog, data, params, reference = _load(args)
    model = fit(sitedb.observations(data.rounds), params.model, reference)
    print(
        f"{len(data.rounds)} rounds, {model.n_scores} hole scores; luck {model.sigma:.2f}"
    )
    print("fixed: " + ", ".join(f"{k} {v:+.3f}" for k, v in model.fixed.items()))
    courses(catalog, model)
    names = {r.player: r.name for r in data.rounds}
    by_player: dict[str, list[ScoredRound]] = {}
    for r in data.rounds:
        by_player.setdefault(r.player, []).append(
            ScoredRound(
                backtest.played_at(r.received_at), rate_seed(model, r.slots), r.strokes
            )
        )
    print(f"\n{'player':26}{'rounds':>7}{'model':>8}{'index':>8}{'low':>7}")
    for p in sorted(model.skills, key=model.index):
        state = compute(by_player[p], params.index)
        low = "" if state.low_mark is None else f"{state.low_mark:+.1f}"
        print(
            f"{names[p]:26}{state.rounds_counted:>7}{model.index(p):>+8.1f}"
            f"{state.index:>+8.1f}{low:>7}"
        )


def cmd_estimate(args) -> None:
    catalog, data, params, reference = _load(args)
    obs = sitedb.observations(data.rounds)

    def progress(calls, p):
        print(f"  EM step {calls}: gamma {p.gamma:.4f}", file=sys.stderr, flush=True)

    found = estimate.em(obs, reference, params.model, progress=progress)
    p = found.params
    print(
        f"EM: {found.iterations} steps, {'converged' if found.converged else 'NOT converged'}"
    )
    print(f"  luck per hole {p.sigma2**0.5:.3f}; gamma {p.gamma:.3f}")
    for g, t in p.tau2.items():
        print(f"  {g:8} {t**0.5:.3f} per hole")
    if found.mirrored:
        m, se = found.mirrored
        print(f"  mirrored term {m:+.3f} +/- {se:.3f} per hole (holes as independent)")
    paired = estimate.mirrored_paired(
        [r.observations() for r in data.rounds], reference, p
    )
    print(
        f"  mirrored, paired within {paired.rounds} rounds with both kinds: "
        f"{paired.mean:+.3f} +/- {paired.se:.3f} per hole, p = {paired.p:.2f} "
        "(the test that decides mirror_term)"
    )

    print("\nskill by month:")
    monthly = estimate.em(obs, reference, p, month=True)
    base_fit, month_fit = found.model, monthly.model
    diffs = np.array(
        [
            rate_seed(month_fit, data.seeds[s]).course_rating
            - rate_seed(base_fit, data.seeds[s]).course_rating
            for s in sorted(data.live_seeds)
        ]
    )
    print(
        f"  month spread {monthly.params.tau2['month'] ** 0.5:.3f} per hole; live seeds' "
        f"course ratings move {abs(diffs).mean():.2f} on average, {abs(diffs).max():.2f} at "
        "most (one stroke or more adopts it)"
    )

    courses(catalog, base_fit)
    if args.write:
        out = replace(
            params,
            model=p,
            provenance={
                "database": Path(args.db).name,
                "rounds": len(data.rounds),
                "latest_round": max(r.received_at for r in data.rounds),
                "estimated": date.today().isoformat(),
                "em_steps": found.iterations,
            },
        )
        out.save(args.params)
        print(f"\nwrote {args.params}")

    if args.replicates:
        gammas = estimate.gamma_interval(
            obs,
            reference,
            p,
            args.replicates,
            progress=lambda i: print(f"  roster {i}", file=sys.stderr, flush=True),
        )
        ok = gammas[~np.isnan(gammas)]
        lo, mid, hi = np.quantile(ok, [0.05, 0.5, 0.95])
        print(
            f"\ngamma over {len(ok)} resampled rosters ({len(gammas) - len(ok)} failed): "
            f"median {mid:.3f}, 90% between {lo:.3f} and {hi:.3f}"
        )


def _share(pairs) -> str:
    return f"{backtest.stronger_share(pairs):.0%} (n={len(pairs)})"


def cmd_backtest(args) -> None:
    _catalog, data, params, reference = _load(args)
    print("replaying...", file=sys.stderr)
    replayed = backtest.replay(
        data.rounds,
        params,
        reference,
        data.created,
        progress=lambda d: print(f"  {d}", file=sys.stderr, flush=True),
    )
    full = fit(sitedb.observations(data.rounds), params.model, reference)
    strength = {p: full.index(p) for p in full.skills}
    players = sorted(strength)
    draws = backtest.rosters(players, args.rosters)
    print(
        f"{len(replayed)} rounds replayed of {len(data.rounds)}; {len(players)} players"
    )

    all_schemes = backtest.schemes(params)
    cards = {s.name: backtest.score(replayed, s) for s in all_schemes}
    default = cards["default"]

    print(
        f"\n== Fairness: the stronger player's net match score (target "
        f"{backtest.TARGET:.0%}); the steady state, both players with "
        f"{args.steady}+ earlier rounds, and all pairs"
    )
    print(f"  {'scheme':28}{'steady state':>18}{'all pairs':>18}{'clearly nearer':>16}")
    base_est = backtest.pairs(default, strength, args.steady)
    for s in all_schemes:
        est = backtest.pairs(cards[s.name], strength, args.steady)
        everyone = backtest.pairs(cards[s.name], strength, 0)
        clear = ""
        if s.name != "default":
            share = backtest.clearly_fairer(base_est, est, draws)
            clear = f"{share:.0%}" + (" *" if share >= backtest.CLEAR else "")
        print(f"  {s.name:28}{_share(est):>18}{_share(everyone):>18}{clear:>16}")
    lo, hi = backtest.share_range(base_est, draws)
    drift = "within" if lo <= backtest.TARGET <= hi else "DRIFTED: outside"
    print(
        f"  the default's share across rosters: 90% between {lo:.0%} and {hi:.0%}, "
        f"{drift} the target"
    )
    gross = [replace(c, handicap=0) for c in default]
    print(
        f"  {'no handicap':28}{_share(backtest.pairs(gross, strength, args.steady)):>18}"
    )

    print("\n== New players: round total less expected, rounds with under 8 earlier")
    base_err = backtest.prediction_errors(default, 8)
    print(f"  {'scheme':28}{'rmse':>8}{'bias':>8}{'clearly better':>16}")
    for s in all_schemes:
        if not (s.name.startswith("start") or s.name == "default"):
            continue
        err = backtest.prediction_errors(cards[s.name], 8)
        flat = [x for v in err.values() for x in v]
        clear = ""
        if s.name != "default":
            wins = sum(
                backtest.rmse(err, w) < backtest.rmse(base_err, w) for w in draws
            )
            clear = f"{wins / len(draws):.0%}" + (
                " *" if wins / len(draws) >= backtest.CLEAR else ""
            )
        print(
            f"  {s.name:28}{backtest.rmse(err):>8.2f}{np.mean(flat):>+8.2f}{clear:>16}"
        )

    print("\n== Caps")
    bound, with_low = backtest.cap_binding(replayed, params.index)
    print(f"  the low-mark caps changed {bound} of {with_low} indexes with a low mark")
    eight = replace(params.index, low_min_rounds=8)
    bound, with_low = backtest.cap_binding(replayed, eight)
    print(f"  with a low mark after 8 rounds: {bound} of {with_low}")

    print("\n== Sandbagging: one round thrown by 15 strokes; index gained, summed over")
    print("   the next 8 rounds (about the handicap strokes it buys)")
    variants = {
        "default": params.index,
        "no caps": replace(params.index, soft_cap=None, hard_cap=None),
        "low mark after 8": eight,
        "no starting index": replace(params.index, start_weight=0.0),
    }
    for at in (0, 3, 24):
        for name, rules in variants.items():
            throws = backtest.sandbag(replayed, rules, at, 15, 8)
            if throws:
                gain = np.mean([t.index_gained for t in throws])
                print(
                    f"  thrown at round {at + 1:2}, {name:18} {gain:6.1f}  (players {len(throws)})"
                )

    print(
        f"\n== Integrity: round total less expected, players with {args.min_prior}+ earlier rounds"
    )
    err = backtest.errors_by(default, args.min_prior)
    sd = err.std()
    z = (err - err.mean()) / sd
    print(f"  {len(err)} rounds: bias {err.mean():+.2f}, sd {sd:.2f} strokes")
    for t in (2.0, 2.5, 3.0, 3.5):
        print(f"  better than {t} sd: {(z < -t).sum()} rounds; worse: {(z > t).sum()}")

    print("\n== Reference holes as played: strokes less expected, per hole, by index")
    for band, v in sorted(
        backtest.reference_residuals(replayed, default, reference).items()
    ):
        print(f"  {band:12} {np.mean(v):+.3f}  (holes {len(v)})")

    print("\n== Round totals less expected, by the seed's par")
    for band, v in sorted(backtest.by_course(default, args.min_prior).items()):
        print(f"  {band:16} {np.mean(v):+.2f}  sd {np.std(v):.2f}  (rounds {len(v)})")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Competitive ratings from a copy of the site database."
    )
    parser.add_argument(
        "--params", type=Path, default=DEFAULT_PATH, help="the parameters file"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "fit", help="every player's index and the vanilla courses' ratings"
    )
    p.add_argument("db", help="a copy of the site database")
    p.set_defaults(func=cmd_fit)
    p = sub.add_parser("estimate", help="estimate the model's parameters offline")
    p.add_argument("db", help="a copy of the site database")
    p.add_argument(
        "--write", action="store_true", help="save them to the parameters file"
    )
    p.add_argument(
        "--replicates",
        type=int,
        default=100,
        help="resampled rosters for gamma (0 skips)",
    )
    p.set_defaults(func=cmd_estimate)
    p = sub.add_parser("backtest", help="replay the database and weigh the rules")
    p.add_argument("db", help="a copy of the site database")
    p.add_argument("--rosters", type=int, default=1000, help="resampled rosters")
    p.add_argument(
        "--steady",
        type=int,
        default=backtest.STEADY,
        help="earlier rounds each player of a pair needs for the steady state",
    )
    p.add_argument(
        "--min-prior",
        type=int,
        default=3,
        help="earlier rounds for a round to count in the integrity and par checks",
    )
    p.set_defaults(func=cmd_backtest)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
