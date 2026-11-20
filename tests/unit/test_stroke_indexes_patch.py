"""The stroke indexes patch: what it accepts, the bytes it checks and the bytes it writes."""

from pathlib import Path

import pytest

from golf.core import rom_utils
from golf.core.patches import PatchError, RecipeStep, parse_step_arg
from golf.core.patches.recipe import get_spec
from golf.core.patches.registry import BuildContext
from golf.core.patches.stroke_indexes import (
    LENGTH,
    PRG_OFFSET,
    StrokeIndexesPatch,
    looks_like_handicaps,
)
from tests.prg_writer import PrgImageWriter
from tests.stored_seeds import STROKE_INDEXES

#: a randomized course's handicaps as `CoursePatch` writes them: each hole's own, from
#: three courses, so two holes share 1 and none has 18
COURSE_HANDICAPS = bytes([1, 9, 3, 1, 12, 7, 5, 16, 11, 2, 14, 4, 8, 10, 6, 13, 15, 17])


def rom_with(table: bytes) -> PrgImageWriter:
    rom = PrgImageWriter()
    rom.write_prg(PRG_OFFSET, table)
    return rom


def test_it_writes_course_ones_entries_of_the_handicap_table():
    assert rom_utils.cpu_to_prg_fixed(rom_utils.TABLE_HANDICAP) == PRG_OFFSET
    assert LENGTH == 18
    rom = rom_with(COURSE_HANDICAPS)
    StrokeIndexesPatch(STROKE_INDEXES).apply(rom)
    assert rom.read_prg(PRG_OFFSET, LENGTH) == bytes(STROKE_INDEXES)
    # the US and UK blocks after it are left alone
    assert rom.read_prg(PRG_OFFSET + LENGTH, 36) == bytes(36)


@pytest.mark.parametrize(
    "indexes",
    [
        STROKE_INDEXES[:17],
        (*STROKE_INDEXES[:17], 7),
        tuple(range(18)),
        tuple(range(2, 20)),
    ],
    ids=["seventeen", "a repeat", "from zero", "to nineteen"],
)
def test_it_writes_only_a_ranking_of_the_18_holes(indexes):
    with pytest.raises(ValueError, match="each once"):
        StrokeIndexesPatch(indexes)


def test_the_bytes_it_replaces_need_only_look_like_handicaps():
    """A course's own handicaps repeat; they are each 1-18, which is all it checks."""
    assert looks_like_handicaps(COURSE_HANDICAPS)
    assert StrokeIndexesPatch(STROKE_INDEXES).can_apply(rom_with(COURSE_HANDICAPS))


@pytest.mark.parametrize(
    "table",
    [bytes(18), bytes([19, *COURSE_HANDICAPS[1:]]), b"\xff" * 18],
    ids=["zeros", "a nineteen", "fill"],
)
def test_it_refuses_a_table_that_is_not_handicaps(table):
    patch = StrokeIndexesPatch(STROKE_INDEXES)
    rom = rom_with(table)
    assert not patch.can_apply(rom)
    with pytest.raises(PatchError, match="not 18 stroke indexes"):
        patch.apply(rom)


def test_finishing_twice_writes_nothing_and_other_indexes_replace_them():
    rom = rom_with(COURSE_HANDICAPS)
    patch = StrokeIndexesPatch(STROKE_INDEXES)
    patch.apply(rom)
    assert patch.is_applied(rom)
    patch.apply(rom)
    reversed_indexes = tuple(19 - index for index in STROKE_INDEXES)
    StrokeIndexesPatch(reversed_indexes).apply(rom)
    assert rom.read_prg(PRG_OFFSET, LENGTH) == bytes(reversed_indexes)


def test_a_recipe_step_takes_the_indexes_as_a_list_or_space_separated():
    from_recipe = RecipeStep.from_dict(
        {"patch": "stroke_indexes", "indexes": list(STROKE_INDEXES)}, Path(".")
    )
    text = " ".join(map(str, STROKE_INDEXES))
    from_cli = parse_step_arg(f"stroke_indexes:indexes={text}", Path("."))
    assert from_recipe == from_cli
    patch = get_spec("stroke_indexes").build(BuildContext(b""), from_cli.params)
    assert isinstance(patch, StrokeIndexesPatch)
    assert patch.patched == bytes(STROKE_INDEXES)
