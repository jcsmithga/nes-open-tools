"""Unit tests for requests and `resolve`, which turns one into concrete settings (ADR 0016)."""

from collections import Counter

import pytest

from golf.randomizer.catalog import JP_ROM, US_ROM
from golf.randomizer.manifest import Settings, mode_rules
from golf.randomizer.request import (
    RequestError,
    check_request,
    is_weighted,
    load_request,
    resolve,
)


def one_of(*values, weights=None) -> dict:
    weights = weights or [1] * len(values)
    return {
        "one_of": [
            {"weight": weight, "value": value}
            for weight, value in zip(weights, values, strict=True)
        ]
    }


def test_an_empty_request_is_the_default_settings_with_the_seed_given():
    assert resolve({}, "week-6") == Settings(prng_seed="week-6")
    drawn = resolve({})
    assert drawn.prng_seed and drawn == Settings(prng_seed=drawn.prng_seed)


def test_a_request_sets_only_what_it_names():
    settings = resolve(
        {
            "sources": [US_ROM],
            "clubs": {"max": 10},
            "constraints": [{"rule": "par_3"}],
            "mirror_chance": 25,
        },
        "abc",
    )
    assert settings.sources == {US_ROM}
    assert settings.constraints == mode_rules("par_3", None)
    assert settings.clubs.max == 10 and settings.clubs.banned == frozenset()
    assert settings.mirror_chance == 25 and settings.hazard_chance == 0
    assert settings.mercy_point == Settings().mercy_point


def test_a_weighted_field_resolves_to_one_of_its_values_by_weight():
    request = {"music": one_of("nes_us", "random", "nes_uk", weights=[3, 1, 0])}
    seen = Counter(resolve(request, str(seed)).music for seed in range(400))
    assert set(seen) == {"nes_us", "random"}
    assert 0.65 < seen["nes_us"] / 400 < 0.85
    assert resolve(request, "same") == resolve(request, "same")
    assert is_weighted(request) and not is_weighted({"music": "nes_us"})


def test_a_weighted_object_field_is_partial_like_any_other():
    request = {"clubs": one_of({"max": 10}, {"banned": ["1W"]})}
    seen = {resolve(request, str(seed)).clubs for seed in range(40)}
    assert {(rules.max, len(rules.banned)) for rules in seen} == {(10, 0), (14, 1)}


def test_a_weighted_constraints_chooses_between_modes():
    request = {
        "constraints": one_of(
            [{"rule": "natural_par"}],
            [{"rule": "par_3"}, {"rule": "expert_cap", "per_nine": 0}],
        ),
        "include": ["derived"],
    }
    modes = {resolve(request, str(seed)).mode.name for seed in range(40)}
    assert modes == {"natural_par", "par_3"}


def test_a_sets_request_picks_a_set_then_its_weighted_fields():
    request = {
        "sets": [
            {"weight": 7, "settings": {"sources": [US_ROM]}},
            {
                "weight": 3,
                "settings": {
                    "sources": [US_ROM, JP_ROM],
                    "music": one_of("nes_us", "random"),
                },
            },
        ]
    }
    resolved = [resolve(request, str(seed)) for seed in range(400)]
    us_only = [settings for settings in resolved if settings.sources == {US_ROM}]
    assert 0.6 < len(us_only) / 400 < 0.8
    assert {settings.music for settings in us_only} == {"random"}
    both = [settings for settings in resolved if settings.sources != {US_ROM}]
    assert {settings.music for settings in both} == {"nes_us", "random"}
    assert is_weighted(request)


@pytest.mark.parametrize(
    "data, message",
    [
        ([], "an object of settings"),
        ({"pars": 72}, "unknown settings"),
        ({"about": "a default seed"}, "unknown settings"),
        ({"set": []}, "did you mean 'sets'"),
        ({"Sets": []}, "did you mean 'sets'"),
        ({"prng_seed": "abc"}, "holds no prng_seed"),
        ({"sets": [{"weight": 1, "settings": {}}], "music": "nes_us"}, "only 'sets'"),
        ({"sets": []}, "non-empty list"),
        ({"sets": [{"weight": 0, "settings": {}}]}, "every weight is 0"),
        ({"sets": [{"weight": 1, "settings": []}]}, "expected an object"),
        ({"sets": [{"weight": 1}]}, '"settings"'),
        ({"mirror_chance": {"one_of": []}}, "non-empty list"),
        ({"mirror_chance": one_of(9, weights=[-1])}, "weight must be"),
        ({"mirror_chance": one_of(9, weights=[1.5])}, "weight must be"),
        ({"mirror_chance": {"one_of": [{"weight": 1, "value": 9}], "x": 1}}, "only"),
        ({"clubs": {"max": one_of(10)}}, "whole field"),
        ({"mirror_chance": one_of(9, 300)}, r"mirror_chance\.one_of\[1\]"),
        ({"mercy_point": 5}, "can't set mercy_point"),
        ({"mercy_point": one_of(9, None)}, "can't set mercy_point"),
        (
            {"sets": [{"weight": 1, "settings": {"mercy_point": None}}]},
            r"sets\[0\] settings: a request can't set mercy_point",
        ),
        ({"constraints": [{"rule": "shuffle"}]}, "unknown rule"),
        (
            {"constraints": [{"rule": "count", "where": {"par": 3}, "max": 1}]},
            "not a rule settings may hold",
        ),
        ({"constraints": {"rule": "standard"}}, "constraints must be a list"),
        ({"constraints": None}, "constraints must be a list"),
        ({"par": 72}, "unknown settings"),
        ({"mirror_chance": 30.5}, "mirror_chance must be a whole percent"),
        (
            {"sets": [{"weight": 1, "settings": {"music": "bogus"}}]},
            r"sets\[0\] settings: music must be",
        ),
    ],
)
def test_a_malformed_request_is_refused(data, message):
    with pytest.raises(RequestError, match=message):
        check_request(data)
    with pytest.raises(RequestError, match=message):
        resolve(data, "abc")


def test_a_request_file_is_read_and_checked(tmp_path):
    path = tmp_path / "request.json"
    path.write_text("{")
    with pytest.raises(RequestError, match="not JSON"):
        load_request(path)
    path.write_text('{"music": "bogus"}')
    with pytest.raises(RequestError, match="music must be"):
        load_request(path)
    path.write_text('{"music": "nes_us"}')
    assert load_request(path) == {"music": "nes_us"}
