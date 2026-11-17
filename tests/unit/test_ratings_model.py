"""The hole model on synthetic rounds: it recovers what generated them, is anchored to
the reference holes, and rates a hole nobody played from its baseline."""

import numpy as np
import pytest

from golf.ratings.model import Observation, Slot, fit
from golf.ratings.params import ModelParams
from golf.ratings.seed import rate_seed
from golf.ratings.sitedb import terrain_key

PARAMS = ModelParams(
    sigma2=0.25,
    tau2={"skill": 1.0, "layout": 0.1, "terrain": 0.05, "pin": 0.01},
    gamma=0.3,
    mirror_term=False,
)
PARS = [4, 3, 5, 4, 4, 3, 4, 5, 4] * 2
DISTANCE = {3: 180, 4: 400, 5: 550}


def course(name: str, extra: float = 0.0) -> list[Slot]:
    return [
        Slot(
            f"{name}/{i:02}", f"{name}-{i}", 0, par, DISTANCE[par] + int(100 * extra), 0
        )
        for i, par in enumerate(PARS)
    ]


REFERENCE = course("ref")
HARD = course("hard", extra=1.0)


def world(indexes: dict[str, float], rounds: int, seed: int = 0) -> list[Observation]:
    """Rounds on the reference and a hard course: a hole's true rating is its par plus
    an offset, the reference's offsets averaging zero."""
    rng = np.random.default_rng(seed)
    offset = {
        s.hole_id: v for s, v in zip(REFERENCE, rng.normal(0, 0.3, 18), strict=True)
    }
    mean = np.mean(list(offset.values()))
    offset = {k: v - mean for k, v in offset.items()}
    offset |= {s.hole_id: 0.8 for s in HARD}
    out = []
    for player, index in indexes.items():
        for _ in range(rounds):
            for slot in REFERENCE + HARD:
                rating = slot.par + offset[slot.hole_id]
                spread = 1 + PARAMS.gamma * (rating - 4)
                mean_strokes = rating + spread * index / 18
                out.append(
                    Observation(
                        player,
                        "2026-11",
                        slot,
                        round(mean_strokes + rng.normal(0, 0.5)),
                    )
                )
    return out


TRUE = {"a": -6.0, "b": 0.0, "c": 9.0, "d": 18.0}


@pytest.fixture(scope="module")
def model():
    return fit(world(TRUE, rounds=12), PARAMS, REFERENCE)


def test_recovers_indexes(model):
    for player, index in TRUE.items():
        assert model.index(player) == pytest.approx(index, abs=1.5)


def test_reference_holes_average_par_and_spread_one(model):
    rated = rate_seed(model, REFERENCE)
    assert rated.course_rating == pytest.approx(rated.par, abs=1e-6)
    assert rated.slope == pytest.approx(1.0, abs=1e-6)


def test_hard_course_rates_harder_and_wider(model):
    rated = rate_seed(model, HARD)
    assert rated.course_rating - rated.par == pytest.approx(18 * 0.8, abs=2.0)
    assert rated.slope > 1.1


def test_expected_score_is_course_rating_plus_slope_times_index(model):
    rated = rate_seed(model, HARD)
    direct = sum(model.expected(s, 9.0) for s in HARD)
    assert rated.expected(9.0) == pytest.approx(direct)


def test_a_new_player_leaves_the_others_alone():
    before = fit(world(TRUE, rounds=12), PARAMS, REFERENCE)
    joined = world(TRUE | {"weak": 40.0}, rounds=12)
    after = fit(joined, PARAMS, REFERENCE)
    for player in TRUE:
        assert after.index(player) == pytest.approx(before.index(player), abs=0.5)


def test_unplayed_hole_is_rated_from_its_baseline(model):
    new = Slot("new/01", "never-seen", 2, 4, 400, 0)
    rating, _ = model.rating(new)
    baseline = model.fixed["intercept"] + model.fixed["par4"]
    centered = (400 - model.centers[4]) / 100 * model.fixed["distance"]
    assert rating == pytest.approx(baseline + centered)


def test_mirror_only_hole_takes_its_twins_terrain():
    assert terrain_key(["mirror@1"], "flipped", "twin") == ("twin", True)
    assert terrain_key([], "plain", "plain") == ("plain", False)
    assert terrain_key(["mirror@1", "hazards@1:5"], "redrawn", "twin") == (
        "redrawn",
        False,
    )
    assert terrain_key(["mirror@1"], "flipped", None) == ("flipped", False)
