"""Unit tests for surveys: rolling a set of rules many times and counting what comes out."""

import pytest

from golf.randomizer.catalog import Catalog
from golf.randomizer.compose import Problem
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import course_rules
from golf.randomizer.pool import build_pool
from golf.randomizer.request import resolve
from golf.randomizer.survey import TooRareError, report, survey, uniform

PAR_3 = {"include": ["derived"], "constraints": [{"rule": "par_3"}]}
UNCAPPED = {"constraints": [{"rule": "standard"}]}


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return Catalog.load()


@pytest.fixture(scope="module")
def curation() -> CurationSnapshot:
    return CurationSnapshot.load()


def problem(request: dict, catalog, curation) -> Problem:
    settings = resolve(request)
    return Problem(build_pool(catalog, curation, settings), course_rules(settings))


def test_a_survey_counts_what_the_rules_give(catalog, curation):
    par_3 = problem(PAR_3, catalog, curation)
    result = survey(par_3, 25)
    assert (result.draws, result.failures) == (25, 0)
    assert result.course_pars == {54: 25}
    assert sum(result.appearances.values()) == 25 * 18
    assert all(sum(pars.values()) == 25 for pars in result.slot_pars)
    assert all(pars[4] == pars[5] == 0 for pars in result.slot_pars)
    assert len(result.over_par) == 25 and len(result.yards) == 25
    assert sum(result.tagged["expert"].values()) == 25
    assert survey(par_3, 25).appearances == result.appearances
    assert survey(par_3, 25, "another").appearances != result.appearances

    mixed = survey(par_3, 25, mix=100)
    assert (mixed.draws, mixed.course_pars) == (25, {54: 25})
    assert mixed.appearances != result.appearances

    lines = report(result, par_3.pool)
    assert lines[0].startswith("25 courses, 0 failures")
    assert "course par: 54 (100%)" in lines
    assert any(line.startswith("holes tagged expert a course:") for line in lines)
    assert len([line for line in lines if line.startswith("  ")]) == 18


def test_a_uniform_survey_keeps_only_random_courses_that_pass(catalog, curation):
    result, dealt = uniform(problem(UNCAPPED, catalog, curation), 10)
    assert result.draws == 10 and dealt > 10
    assert result.course_pars == {72: 10}
    # eighteen par 3s among random courses are far too rare to wait for
    with pytest.raises(TooRareError, match="0 of 2000 random courses"):
        uniform(problem(PAR_3, catalog, curation), 1, tries=2000)


def test_a_survey_with_no_course_says_so(catalog, curation):
    us_only = resolve(PAR_3 | {"sources": ["nes_open_us"]})
    pool = build_pool(catalog, curation, us_only)
    result = survey(Problem(pool, course_rules(us_only)), 3)
    assert (result.draws, result.failures) == (0, 3)
    assert report(result, pool) == ["no course in 3 tries"]
