"""Every vanilla hole passes the checks a published hole must: their thresholds are the
fewest any vanilla hole has (`golf/formats/hole_check.py`)."""

from golf.formats.hole_check import rom_problems
from golf.randomizer.catalog import Catalog, HoleStore, RomSource


def test_every_vanilla_hole_passes_check(vanilla_courses, vanilla_jp_courses):
    store = HoleStore(vanilla_courses)
    entries = [
        entry
        for entry in Catalog.load().newest().values()
        if isinstance(entry.source, RomSource)
    ]
    assert entries
    problems = {
        str(entry.id): found
        for entry in entries
        if (found := rom_problems(store.load(entry)))
    }
    assert problems == {}
