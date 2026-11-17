"""
The reference holes: the 54 untransformed NES Open holes (US, UK and Japan), each hole's
four pins, with no wind. Index 0 is a player expected to shoot par over them, and their
mean spread is slope 1.0 (docs/competitive/design.md, The reference).

The Mario Open holes are left out, so their five courses' ratings are a check.
"""

from golf.randomizer.catalog import US_ROM, VANILLA, Catalog

from .model import Slot

PINS = range(4)


def course_slots(catalog: Catalog, rom: str, course: str | None = None) -> list[Slot]:
    """Every vanilla hole of a ROM (or one of its courses), untransformed, as one slot
    per pin with no wind."""
    slots = []
    for entry in catalog:
        if entry.kind != VANILLA or entry.rom != rom or not entry.live:
            continue
        if course is not None and getattr(entry.source, "course", None) != course:
            continue
        for pin in PINS:
            slots.append(
                Slot(
                    hole_id=str(entry.id),
                    terrain=entry.content_hash,
                    pin=pin,
                    par=entry.par,
                    distance=entry.distance,
                    wind=0,
                )
            )
    return slots


def reference_slots(catalog: Catalog | None = None) -> list[Slot]:
    slots = course_slots(catalog or Catalog.load(), US_ROM)
    if len(slots) != 54 * len(PINS):
        raise ValueError(f"expected 54 NES Open holes, found {len(slots) // len(PINS)}")
    return slots
