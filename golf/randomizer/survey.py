"""
Surveys: what a set of rules gives over many seeds.

The composed draw (`golf/randomizer/compose.py`) has no closed form for how often a hole
appears. Two holes no rule tells apart are equally likely, and everything past that is
measured. `survey` rolls a problem many times and counts; `golf-randomize survey` prints
it.

`uniform` is the yardstick. It deals eighteen families at random, a hole of each, and
throws the course away unless it keeps every rule, so every course that keeps them is
exactly as likely as any other. That is far too wasteful to generate seeds with (a
standard course is about one deal in 330, a par 3 course one in millions), but where it
finishes it shows which way the composed draw leans, and whether `Problem.mix` has
taken the lean out.
"""

import json
import random
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from statistics import mean, pstdev

from .catalog import REPO_ROOT, CatalogEntry, HoleId
from .compose import GenerationError, Problem
from .pool import Pool
from .rules import HOLES, NINE

DEFAULT_DIFFICULTY = REPO_ROOT / "data" / "difficulty" / "holes.json"

Draw = Callable[[int], Sequence[CatalogEntry]]


@cache
def load_over_par(path: Path = DEFAULT_DIFFICULTY) -> Mapping[str, float]:
    """Each lineage's predicted strokes over par, from the difficulty report's table."""
    if not path.exists():
        return {}
    rows = json.loads(path.read_text())["holes"]
    return {row["lineage"]: row["over_par"] for row in rows}


@dataclass
class Survey:
    draws: int = 0
    failures: int = 0
    seconds: float = 0.0
    #: courses each hole was on
    appearances: Counter[HoleId] = field(default_factory=Counter)
    #: the pars drawn at each slot
    slot_pars: list[Counter[int]] = field(
        default_factory=lambda: [Counter() for _ in range(HOLES)]
    )
    layouts: Counter[tuple[int, ...]] = field(default_factory=Counter)
    course_pars: Counter[int] = field(default_factory=Counter)
    yards: list[int] = field(default_factory=list)
    #: predicted strokes over par a course, and the gap between its nines
    over_par: list[float] = field(default_factory=list)
    nine_gap: list[float] = field(default_factory=list)
    #: courses with exactly this many holes carrying a tag, by tag
    tagged: dict[str, Counter[int]] = field(default_factory=dict)

    def add(self, holes: Sequence[CatalogEntry], pool: Pool) -> None:
        scores = load_over_par()
        self.draws += 1
        pars = tuple(hole.par for hole in holes)
        self.layouts[pars] += 1
        self.course_pars[sum(pars)] += 1
        self.yards.append(sum(hole.distance for hole in holes))
        for slot, hole in enumerate(holes):
            self.appearances[hole.id] += 1
            self.slot_pars[slot][hole.par] += 1
        over = [scores[hole.id.lineage] for hole in holes if hole.id.lineage in scores]
        if len(over) == len(holes):
            self.over_par.append(sum(over))
            self.nine_gap.append(abs(sum(over[:NINE]) - sum(over[NINE:])))
        counts: Counter[str] = Counter()
        for hole in holes:
            counts.update(pool.tags.get(hole.id, ()))
        for tag in {tag for tags in pool.tags.values() for tag in tags}:
            self.tagged.setdefault(tag, Counter())[counts[tag]] += 1

    def rate(self, hole_id: HoleId) -> float:
        return self.appearances[hole_id] / self.draws if self.draws else 0.0


def run(draw: Draw, pool: Pool, draws: int) -> Survey:
    result = Survey()
    started = time.perf_counter()
    for number in range(draws):
        try:
            result.add(draw(number), pool)
        except GenerationError:
            result.failures += 1
    result.seconds = time.perf_counter() - started
    return result


def survey(problem: Problem, draws: int, seed: str = "survey", mix: int = 0) -> Survey:
    """`draws` courses from a compiled problem, each put through `mix` steps of
    `Problem.mix`."""
    return run(
        lambda number: problem.draw(random.Random(f"{seed}\0{number}"), mix),
        problem.pool,
        draws,
    )


class TooRareError(GenerationError):
    """Too few random courses keep the rules for dealing them blindly to finish."""


def uniform(
    problem: Problem, draws: int, seed: str = "survey", tries: int = 400_000
) -> tuple[Survey, int]:
    """`draws` courses, each equally likely among those that keep the rules, and how
    many were dealt to get them. Raises `TooRareError` after `tries` deals."""
    by_family: dict[str, list[int]] = {}
    for value, candidate in enumerate(problem.candidates):
        by_family.setdefault(candidate.family, []).append(value)
    families = list(by_family.values())
    rng = random.Random(f"uniform\0{seed}")
    result = Survey()
    dealt = 0
    started = time.perf_counter()
    while result.draws < draws:
        if dealt >= tries or len(families) < HOLES:
            raise TooRareError(
                f"{result.draws} of {dealt} random courses kept the rules: too rare "
                "to survey this way"
            )
        dealt += 1
        picks = [rng.choice(members) for members in rng.sample(families, HOLES)]
        if problem.allows(picks):
            result.add(
                [problem.candidates[value].entry for value in picks], problem.pool
            )
    result.seconds = time.perf_counter() - started
    return result, dealt


def _spread(numbers: Sequence[float]) -> str:
    if not numbers:
        return "n/a"
    return (
        f"mean {mean(numbers):.1f}, sd {pstdev(numbers):.1f}, "
        f"{min(numbers):.1f} to {max(numbers):.1f}"
    )


def report(result: Survey, pool: Pool, top: int = 5) -> list[str]:
    """A survey as lines of text."""
    if not result.draws:
        return [f"no course in {result.failures} tries"]
    drawn = result.draws
    lines = [
        f"{drawn} courses, {result.failures} failures, "
        f"{result.seconds / (drawn + result.failures) * 1000:.1f} ms each",
        "course par: "
        + ", ".join(
            f"{par} ({count / drawn:.0%})"
            for par, count in sorted(result.course_pars.items())
        ),
        f"yards: {_spread(result.yards)}",
        f"predicted over par: {_spread(result.over_par)}",
        f"gap between the nines' predicted over par: {_spread(result.nine_gap)}",
        f"distinct par layouts: {len(result.layouts)}",
    ]
    for tag, counts in sorted(result.tagged.items()):
        lines.append(
            f"holes tagged {tag} a course: "
            + ", ".join(
                f"{n} ({count / drawn:.0%})" for n, count in sorted(counts.items())
            )
        )
    holes = [member for family in pool.families for member in family.members]
    rates = sorted((result.rate(hole.id), str(hole.id)) for hole in holes)
    never = [name for rate, name in rates if rate == 0]
    lines.append(
        f"holes in the pool: {len(holes)}; on no course: {len(never)}; "
        f"a hole's share of courses: {_spread([rate * 100 for rate, _ in rates])} (%)"
    )
    lines.append(
        "most common: "
        + ", ".join(f"{name} {rate:.0%}" for rate, name in reversed(rates[-top:]))
    )
    lines.append(
        "least common: " + ", ".join(f"{name} {rate:.0%}" for rate, name in rates[:top])
    )
    lines.append("par by slot (share of courses with a par 3 / 4 / 5 there):")
    for slot, pars in enumerate(result.slot_pars):
        lines.append(
            f"  {slot + 1:>2}  "
            + "  ".join(f"{pars[par] / drawn:>4.0%}" for par in (3, 4, 5))
        )
    return lines
