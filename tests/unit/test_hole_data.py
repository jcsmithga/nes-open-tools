"""HoleData loading."""

import pytest

from tests.synthetic_holes import synthetic_hole


def test_a_dict_that_fails_to_parse_leaves_the_hole_unchanged():
    hole = synthetic_hole()
    before = hole.to_dict()
    broken = synthetic_hole(2).to_dict()
    broken["greens"]["rows"][3] = "zz"  # parsed after terrain and attributes

    with pytest.raises(ValueError):
        hole.load_dict(broken)

    assert hole.to_dict() == before


def test_a_dict_missing_a_field_leaves_the_hole_unchanged():
    hole = synthetic_hole()
    before = hole.to_dict()
    broken = synthetic_hole(2).to_dict()
    del broken["green"]

    with pytest.raises(KeyError):
        hole.load_dict(broken)

    assert hole.to_dict() == before
