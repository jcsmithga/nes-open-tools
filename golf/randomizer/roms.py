"""The vanilla ROMs a randomizer seed can be built from, with the hash that identifies each.

The ids are the catalog's source ROM ids (`US_ROM`, `JP_ROM`), which `required_roms`
returns for a manifest. A hash covers the whole file, iNES header included. The file name
is what the server's ROM directory (`GOLF_ROM_DIR`) and the repository root call each ROM.
The site's ROM setup page checks a player's files against these in the browser
(docs/randomizer_devplan.md). A dump with a different header, or none, holds the same
game, so the page retries a mismatched file of the right size with `header` in place, or a
file one header short with `header` prepended, before refusing it.
"""

from dataclasses import dataclass

from golf.core.jp_rom_utils import JP_ROM_SHA1
from golf.core.rom_utils import US_ROM_SHA1

from .catalog import JP_ROM, US_ROM

#: The iNES header both vanilla ROMs' hashes cover: NES 2.0, 256 KiB of PRG ROM, CHR RAM,
#: mapper 1 (MMC1) with battery-backed PRG RAM
INES_HEADER = bytes.fromhex("4e45531a100012080000700700000001")
#: The size of either vanilla ROM file, header included
ROM_SIZE = len(INES_HEADER) + 256 * 1024


@dataclass(frozen=True)
class VanillaRom:
    id: str
    title: str
    #: the file's name in the server's ROM directory
    filename: str
    #: lowercase hex SHA-1 of the whole file
    sha1: str
    #: the file's 16-byte iNES header
    header: bytes = INES_HEADER
    #: the file's size in bytes, header included
    size: int = ROM_SIZE


VANILLA_ROMS: tuple[VanillaRom, ...] = (
    VanillaRom(
        US_ROM, "NES Open Tournament Golf (USA)", "nes_open_us.nes", US_ROM_SHA1
    ),
    VanillaRom(JP_ROM, "Mario Open Golf (Japan)", "mario_open_jp.nes", JP_ROM_SHA1),
)


def vanilla_rom(rom_id: str) -> VanillaRom:
    """The vanilla ROM with this id. Raises KeyError for an unknown id."""
    for rom in VANILLA_ROMS:
        if rom.id == rom_id:
            return rom
    raise KeyError(rom_id)
