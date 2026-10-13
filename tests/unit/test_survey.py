"""Unit tests for surveys: rolling a set of rules many times and counting what comes out."""

import pytest

from golf.randomizer.catalog import Catalog
from golf.randomizer.compose import Problem
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import course_rules
from golf.randomizer.manifest import Settings
from golf.randomizer.pool import build_pool
from golf.randomizer.request import load_request, presets
from golf.randomizer.survey import TooRareError, legacy, report, survey, uniform


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load()


@pytest.fixture(scope="module")
def curation() -> CurationSnapshot:
    return CurationSnapshot.load()


def problem(name: str, catalog, curation) -> Problem:
    settings = load_request(presets()[name])
    return Problem(build_pool(catalog, curation, settings), course_rules(settings))


def test_a_survey_counts_what_the_rules_give(catalog, curation):
    executive = problem("executive", catalog, curation)
    result = survey(executive, 25)
    assert (result.draws, result.failures) == (25, 0)
    assert result.course_pars == {58: 25}
    assert sum(result.appearances.values()) == 25 * 18
    assert all(sum(pars.values()) == 25 for pars in result.slot_pars)
    assert all(pars[5] == 0 for pars in result.slot_pars)
    assert len(result.over_par) == 25 and len(result.yards) == 25
    assert sum(result.tagged["expert"].values()) == 25
    assert survey(executive, 25).appearances == result.appearances
    assert survey(executive, 25, "another").appearances != result.appearances

    mixed = survey(executive, 25, mix=100)
    assert (mixed.draws, mixed.course_pars) == (25, {58: 25})
    assert mixed.appearances != result.appearances

    lines = report(result, executive.pool)
    assert lines[0].startswith("25 courses, 0 failures")
    assert "course par: 58 (100%)" in lines
    assert any(line.startswith("holes tagged expert a course:") for line in lines)
    assert len([line for line in lines if line.startswith("  ")]) == 18


def test_the_layout_first_draw_can_be_surveyed_beside_it(catalog, curation):
    settings = Settings()
    pool = build_pool(catalog, curation, settings)
    old = legacy(pool, settings, 30)
    new = survey(Problem(pool, course_rules(settings)), 30)
    for result in (old, new):
        assert result.course_pars == {72: 30}
        assert max(result.tagged["expert"]) <= 2


def test_a_uniform_survey_keeps_only_random_courses_that_pass(catalog, curation):
    natural = problem("natural_par", catalog, curation)
    result, dealt = uniform(natural, 10)
    assert result.draws == 10 and dealt > 10
    assert all(68 <= par <= 76 for par in result.course_pars)
    # eighteen par 3s among random courses are far too rare to wait for
    with pytest.raises(TooRareError, match="0 of 2000 random courses"):
        uniform(problem("par_3_course", catalog, curation), 1, tries=2000)


def test_a_survey_with_no_course_says_so(catalog, curation):
    settings = load_request(presets()["par_3_course"])
    us_only = Settings(
        sources=frozenset({"nes_open_us"}), constraints=settings.constraints
    )
    pool = build_pool(catalog, curation, us_only)
    result = survey(Problem(pool, course_rules(us_only)), 3)
    assert (result.draws, result.failures) == (0, 3)
    assert report(result, pool) == ["no course in 3 tries"]
