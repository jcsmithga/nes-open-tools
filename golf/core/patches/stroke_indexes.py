"""
Stroke indexes patch: write a course's stroke indexes into the handicap table.

`HandicapIndexTable` ($DDDD) holds a stroke index for every hole: 54 bytes, 18 for each
course in the order Japan, US, UK. Only the scorecard reads it, at bank 2 $AF08, for its
handicap column. `CoursePatch` writes each hole's own `handicap` into the first 18,
course 1's, which every course slot plays once `COURSE_MIRRORS_PATCH` is applied. Holes
drawn from different courses can share a value, so those 18 bytes are each 1-18 but not
always a ranking.

The randomizer site rates a seed when it is stored and writes the stroke indexes it
rated over those 18 bytes when it finishes a download (ADR 0036). Their preimage is
whatever the course's holes carried, so this patch checks that the bytes it replaces
look like a handicap table, each 1-18, rather than that they are particular values. A
ROM finished twice with the same indexes writes nothing the second time; finished with
other indexes, it takes the new ones, since the table means nothing else.
"""

from collections.abc import Sequence
from typing import TYPE_CHECKING

from golf.core import rom_utils

from .base import PatchError, ROMPatch

if TYPE_CHECKING:
    from golf.core.rom_writer import RomWriter

#: the first 18 entries of `HandicapIndexTable`, course 1's
PRG_OFFSET = rom_utils.cpu_to_prg_fixed(rom_utils.TABLE_HANDICAP)
LENGTH = rom_utils.HOLES_PER_COURSE
#: the values every entry holds, before and after
STROKE_INDEXES = range(1, rom_utils.HOLES_PER_COURSE + 1)


def looks_like_handicaps(data: bytes) -> bool:
    """Whether the table's 18 bytes are each a stroke index, 1-18."""
    return len(data) == LENGTH and all(byte in STROKE_INDEXES for byte in data)


class StrokeIndexesPatch(ROMPatch):
    """Write 18 stroke indexes, in hole order, over course 1's handicap table."""

    name = "stroke_indexes"
    description = (
        "Write the course's stroke indexes into the scorecard's handicap column"
    )

    def __init__(self, indexes: Sequence[int]):
        if sorted(indexes) != list(STROKE_INDEXES):
            raise ValueError(
                f"stroke indexes rank the {LENGTH} holes 1-{LENGTH}, each once; "
                f"got {list(indexes)}"
            )
        self.prg_offset = PRG_OFFSET
        self.patched = bytes(indexes)

    def can_apply(self, rom_writer: "RomWriter") -> bool:
        return looks_like_handicaps(rom_writer.read_prg(self.prg_offset, LENGTH))

    def is_applied(self, rom_writer: "RomWriter") -> bool:
        return rom_writer.read_prg(self.prg_offset, LENGTH) == self.patched

    def apply(self, rom_writer: "RomWriter") -> None:
        if self.is_applied(rom_writer):
            return
        if not self.can_apply(rom_writer):
            current = rom_writer.read_prg(self.prg_offset, LENGTH)
            raise PatchError(
                f"Cannot apply patch '{self.name}': the handicap table at PRG "
                f"0x{self.prg_offset:05X} holds {current.hex(' ')}, not 18 stroke "
                "indexes"
            )
        rom_writer.annotate(self.name).write_prg(self.prg_offset, self.patched)

    def __repr__(self) -> str:
        return f"StrokeIndexesPatch({list(self.patched)})"
