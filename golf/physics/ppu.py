"""
The PPU's memory, as the game writes it, so `NesMachine` can show a screen.

`NesMachine` has no picture: the physics never needs one. A reference for what
the game draws does, so this keeps what a PPU would hold - pattern tables,
nametables and palettes (VRAM, written through $2006/$2007), sprites (OAM,
copied from a page of RAM by a $4014 DMA), the scroll and the control register
- and leaves turning that into pixels to `golf.rendering.nes_screen`.

It models the registers, not the timing: a frame's scroll is whatever was last
written, so a raster split mid-frame would not show. The game's views have none.
"""

from dataclasses import dataclass, field

#: MMC1 mirroring modes, the control register's low two bits.
ONE_SCREEN_LOWER, ONE_SCREEN_UPPER, VERTICAL, HORIZONTAL = range(4)


@dataclass
class PpuState:
    vram: bytearray = field(default_factory=lambda: bytearray(0x4000))
    """Pattern tables at $0000-$1FFF, nametables, palettes at $3F00."""
    oam: bytearray = field(default_factory=lambda: bytearray(256))
    ctrl: int = 0
    """$2000: base nametable, increment, pattern tables, sprite size."""
    mask: int = 0
    """$2001."""
    scroll_x: int = 0
    scroll_y: int = 0
    mirroring: int = HORIZONTAL

    address: int = 0
    latch: bool = False
    read_buffer: int = 0
    oam_address: int = 0

    def nametable_address(self, address: int) -> int:
        """Where a nametable address lands in `vram` under the cartridge's mirroring."""
        table = (address - 0x2000) >> 10 & 3
        offset = address & 0x3FF
        physical = {
            ONE_SCREEN_LOWER: 0,
            ONE_SCREEN_UPPER: 1,
            VERTICAL: table & 1,
            HORIZONTAL: table >> 1,
        }[self.mirroring]
        return 0x2000 + physical * 0x400 + offset

    def _resolve(self, address: int) -> int:
        address &= 0x3FFF
        if address >= 0x3F00:
            address = 0x3F00 | address & 0x1F
            if address & 0x13 == 0x10:
                # $3F10/$14/$18/$1C mirror the background's entries.
                address &= ~0x10
            return address
        if address >= 0x2000:
            return self.nametable_address(0x2000 | address & 0xFFF)
        return address

    def read_status(self, status: int) -> int:
        self.latch = False
        return status

    def write(self, register: int, value: int) -> None:
        """A write to $2000-$2007 (`register` 0-7)."""
        if register == 0:
            self.ctrl = value
        elif register == 1:
            self.mask = value
        elif register == 3:
            self.oam_address = value
        elif register == 4:
            self.oam[self.oam_address] = value
            self.oam_address = (self.oam_address + 1) & 0xFF
        elif register == 5:
            if self.latch:
                self.scroll_y = value
            else:
                self.scroll_x = value
            self.latch = not self.latch
        elif register == 6:
            if self.latch:
                self.address = (self.address & 0xFF00) | value
            else:
                self.address = (value & 0x3F) << 8 | (self.address & 0xFF)
            self.latch = not self.latch
        elif register == 7:
            self.vram[self._resolve(self.address)] = value
            self._advance()

    def read_data(self) -> int:
        """A read of $2007, buffered as on the console except for palettes."""
        address = self._resolve(self.address)
        if address >= 0x3F00:
            value = self.vram[address]
        else:
            value, self.read_buffer = self.read_buffer, self.vram[address]
        self._advance()
        return value

    def _advance(self) -> None:
        self.address = (self.address + (32 if self.ctrl & 0x04 else 1)) & 0x3FFF

    def dma(self, page: bytes) -> None:
        """$4014: 256 bytes of RAM into OAM, starting at the OAM address."""
        for i, value in enumerate(page):
            self.oam[(self.oam_address + i) & 0xFF] = value
