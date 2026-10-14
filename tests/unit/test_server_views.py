"""The helpers in server/views.py that templates call directly."""

from markupsafe import Markup, escape

from golf.randomizer.catalog import US_ROM
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.roms import vanilla_rom
from server.seeds import SeedHole
from server.views import _hole_view, timestamp


def test_timestamp_is_a_time_element_holding_the_utc_minute():
    assert timestamp("2026-09-23T15:51:09Z") == Markup(
        '<time datetime="2026-09-23T15:51:09Z">2026-09-23 15:51 UTC</time>'
    )


def test_timestamp_survives_being_passed_into_a_string_as_a_value():
    stamp = timestamp("2026-09-23T15:51:09Z")
    assert Markup("created {}").format(stamp) == Markup("created ") + stamp
    assert escape(stamp) == stamp


def seed_hole(**changes) -> SeedHole:
    fields = {
        "position": 1,
        "hole_id": "nes_us/12",
        "par": 5,
        "pin_index": 0,
        "data_hash": "0" * 64,
        "distance": 642,
        "author": "Nintendo",
        "source_rom": US_ROM,
        "source_course": "us",
        "source_hole": 12,
    }
    return SeedHole(**{**fields, **changes})


def test_a_hole_view_names_a_source_rom_for_a_vanilla_hole_only():
    vanilla = _hole_view(seed_hole(), CurationSnapshot())
    assert (vanilla.rom_title, vanilla.source_hole) == (vanilla_rom(US_ROM).title, 12)
    assert vanilla.course

    derived = _hole_view(
        seed_hole(
            position=2,
            hole_id="jdharms/nes_us_12_short",
            par=3,
            distance=190,
            author="jdharms",
            source_rom=None,
            source_course=None,
            source_hole=None,
        ),
        CurationSnapshot(),
    )
    assert (derived.rom_title, derived.course, derived.source_hole) == (None,) * 3
    assert (derived.author, derived.par, derived.distance) == ("jdharms", 3, 190)
    assert (derived.number, derived.id) == (2, "jdharms/nes_us_12_short")
