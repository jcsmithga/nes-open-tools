"""Hole transform names (golf/randomizer/transforms.py)."""

import hashlib

import pytest

from golf.randomizer.catalog import HoleId, content_hash
from golf.randomizer.manifest import Slot
from golf.randomizer.transforms import (
    MAX_SEED,
    TRANSFORMS,
    TransformError,
    apply_transforms,
    parse_transform,
)
from tests.synthetic_holes import synthetic_hole
from tests.vanilla_holes import vanilla_holes

SEEDED = [name for name, transform in TRANSFORMS.items() if transform.seeded]


def test_unknown_transform_is_an_error():
    with pytest.raises(TransformError, match="unknown transform 'spin@1'"):
        parse_transform("spin@1")
    with pytest.raises(TransformError, match="unknown transform 'mirror'"):
        parse_transform("mirror")


@pytest.mark.parametrize("name", SEEDED)
@pytest.mark.parametrize(
    "argument", ["", ":", ":-1", ":x", ":07", ":+7", f":{MAX_SEED + 1}"]
)
def test_a_seeded_transform_needs_a_canonical_seed(name, argument):
    with pytest.raises(TransformError, match=f"{name} takes a seed"):
        parse_transform(name + argument)


@pytest.mark.parametrize("name", SEEDED)
def test_a_seeded_transform_takes_any_32_bit_seed(name):
    parse_transform(f"{name}:0")
    parse_transform(f"{name}:{MAX_SEED}")


def test_mirror_takes_no_seed():
    with pytest.raises(TransformError, match="mirror@1 takes no seed"):
        parse_transform("mirror@1:3")


def test_a_failed_transform_is_a_transform_error():
    # synthetic_hole marks its first tile with the hole number, which has no mirror
    with pytest.raises(TransformError, match=r"mirror@1: terrain tile \$01"):
        apply_transforms(synthetic_hole(), ["mirror@1"])


def test_no_transforms_is_the_hole_itself():
    hole = synthetic_hole()
    assert apply_transforms(hole, []) is hole


def test_slot_round_trips_its_transforms():
    slot = Slot(
        HoleId("nes_us/01"), 4, 0, ("hazards@1:99", "hazards-weighted@1:5", "mirror@1")
    )
    assert Slot.from_json(slot.to_json()) == slot


#: transform -> SHA-256 over every vanilla hole's id and transformed content hash
GOLDEN = {
    "mirror@1": "5b4c7ac756486bb195bde65d593141bb376515dd888527c32781d03f98810e0f",
    "hazards@1:0": "627171607676eb437ab0b9c67c2be87f5eceecea28a39e9eedc6e8c71c9eaa04",
    "hazards@1:1": "da49a90474f794088ea026d8e6c886665db0b33426e2a62b54e99df92b1270e1",
    "hazards-weighted@1:0": "b1a84d805d0095b0abfa02fbfc7b94a2cce8c79992916a89d3533593b69f9c0a",
    "hazards-weighted@1:1": "dbd13c308429e32f456525e53e7e524ef6b7bd263584220a7293f03154eab37f",
}


def test_every_transform_has_golden_output():
    assert {text.partition(":")[0] for text in GOLDEN} == TRANSFORMS.keys()


@pytest.mark.parametrize("text", GOLDEN)
def test_transform_output_is_unchanged(vanilla_courses, vanilla_jp_courses, text):
    """A failure here means a transform's output changed. If that was meant, update the
    hash, and bump the transform's version too if what it means changed (ADR 0024,
    `docs/hole_transforms.md`). Stored seeds keep the holes they were built with."""
    holes = list(vanilla_holes())
    lines = (
        f"{hole_id} {content_hash(apply_transforms(hole, [text]))}\n"
        for hole_id, hole in holes
    )
    actual = hashlib.sha256("".join(lines).encode()).hexdigest()
    assert actual == GOLDEN[text]
