"""The vanilla ROM list the site checks players' files against."""

import hashlib
from pathlib import Path

import pytest

from golf.randomizer.catalog import JP_ROM, US_ROM
from golf.randomizer.manifest import SOURCES
from golf.randomizer.roms import VANILLA_ROMS, vanilla_rom

ROOT = Path(__file__).resolve().parents[2]


def test_the_roms_are_the_manifest_sources_in_order():
    assert tuple(rom.id for rom in VANILLA_ROMS) == SOURCES


def test_hashes_are_lowercase_sha1_hex():
    for rom in VANILLA_ROMS:
        assert len(rom.sha1) == 40
        assert rom.sha1 == rom.sha1.lower()
        int(rom.sha1, 16)


def test_file_names_are_the_ones_the_repository_uses():
    assert vanilla_rom(US_ROM).filename == "nes_open_us.nes"
    assert vanilla_rom(JP_ROM).filename == "mario_open_jp.nes"


def test_lookup_by_id():
    assert vanilla_rom(JP_ROM).id == JP_ROM
    with pytest.raises(KeyError):
        vanilla_rom("nes_open_jp")


def test_headers_are_ines_headers_the_size_accounts_for():
    for rom in VANILLA_ROMS:
        assert len(rom.header) == 16
        assert rom.header.startswith(b"NES\x1a")
        assert rom.size == 16 + rom.header[4] * 16 * 1024


@pytest.mark.parametrize("rom", VANILLA_ROMS, ids=lambda rom: rom.id)
def test_the_hash_matches_a_local_rom(rom):
    path = ROOT / rom.filename
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    assert hashlib.sha1(path.read_bytes()).hexdigest() == rom.sha1


@pytest.mark.parametrize("rom", VANILLA_ROMS, ids=lambda rom: rom.id)
def test_the_header_and_size_match_a_local_rom(rom):
    path = ROOT / rom.filename
    if not path.exists():
        pytest.skip(f"{path.name} not present")
    data = path.read_bytes()
    assert len(data) == rom.size
    assert data[:16] == rom.header
