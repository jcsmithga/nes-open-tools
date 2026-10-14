"""Unit tests for requests, the settings files a seed is rolled from."""

import pytest

from golf.randomizer.catalog import US_ROM
from golf.randomizer.manifest import ManifestError, Settings
from golf.randomizer.request import (
    RequestError,
    load_request,
    settings_from_request,
)
from golf.randomizer.rules import Rule


def test_an_empty_request_is_the_default_settings():
    assert settings_from_request({}) == Settings()
    assert settings_from_request({}, "week-6") == Settings(prng_seed="week-6")


def test_a_request_sets_only_what_it_names():
    settings = settings_from_request(
        {
            "sources": [US_ROM],
            "clubs": {"max": 10},
            "constraints": [{"rule": "par_3"}],
        }
    )
    assert settings.sources == {US_ROM}
    assert settings.experts_per_nine is None
    assert settings.clubs.max == 10 and settings.clubs.banned == frozenset()
    assert settings.constraints == (Rule.parse({"rule": "par_3"}),)
    assert settings.mercy_point == Settings().mercy_point


@pytest.mark.parametrize(
    "data, message",
    [
        ([], "an object of settings"),
        ({"pars": 72}, "unknown settings"),
        ({"about": "a default seed"}, "unknown settings"),
        ({"prng_seed": "abc"}, "holds no prng_seed"),
        ({"sets": [{"weight": 1, "settings": {}}]}, "weighted request"),
        ({"par": {"one_of": [{"weight": 1, "value": 72}]}}, "weighted request"),
        ({"constraints": [{"rule": "shuffle"}]}, "unknown rule"),
        (
            {"constraints": [{"rule": "count", "where": {"par": 3}, "max": 1}]},
            "not a rule settings may hold",
        ),
        ({"constraints": {"rule": "standard"}}, "constraints must be null or a list"),
        ({"par": 54}, "par must be one of"),
    ],
)
def test_a_malformed_request_is_refused(data, message):
    with pytest.raises(ManifestError, match=message):
        settings_from_request(data)


def test_a_request_file_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "request.json"
    path.write_text("{")
    with pytest.raises(RequestError, match="not JSON"):
        load_request(path)
