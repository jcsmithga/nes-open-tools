"""
Just enough of an NES to run the game's own frame loop under py65.

The physics oracle needs more than one routine: a shot involves the swing
animation (bank 8), the behind-the-golfer scene (bank 9), the cup (bank 9), the
view switches and the physics (bank 13), all far-calling each other every frame.
Rather than stub each of those, this machine gives the game what it expects:

- **MMC1 PRG banking.** The game only changes banks by writing the mapper's
  serial port (`SetPrgBank`, $D35A), so writes to $8000-$FFFF are shifted into
  a 5-bit register and the fifth write to the PRG register swaps the bank.
  Nothing else about the mapper matters here.
- **The controller** at $4016, read one button a bit, strobed by writing 1 then 0.
- **Vblank.** At each `WaitForVblank` busy loop the machine runs the game's own
  NMI handler once. That is a frame: it flushes the PPU buffer, bumps the frame
  counter, reads the controllers and runs the sound engine.
- **The PPU**, only as far as `$2002` reads: vblank set, so the few polling
  loops that read it fall through.

Everything else is plain RAM. There is no picture, unless the machine is made
with `ppu=True`: then it keeps the PPU's memory (`golf.physics.ppu`), and
`golf.rendering.nes_screen` can draw the screen from it.
"""

from collections.abc import Callable

from py65.devices.mpu6502 import MPU

from golf.core.rom_reader import RomReader
from golf.physics.ppu import PpuState

PRG_BANK_SIZE = 0x4000
FIXED_BANK = 15

NMI_VECTOR = 0xFFFA
VBLANK_BUSY_LOOP = 0xCD7D  # LCD7D_VblankBusyLoop, inside WaitForVblank
VBLANK_FLAG = 0x13
CURRENT_BANK = 0x5C

PPU_STATUS = 0x2002
PPU_DATA = 0x2007
OAM_DMA = 0x4014
CONTROLLER_PORT = 0x4016
SECOND_CONTROLLER_PORT = 0x4017

#: Pad bits in the order the game shifts them in: A first, Right last.
BUTTON_A = 0x80
BUTTON_B = 0x40
BUTTON_SELECT = 0x20
BUTTON_START = 0x10
BUTTON_UP = 0x08
BUTTON_DOWN = 0x04
BUTTON_LEFT = 0x02
BUTTON_RIGHT = 0x01


class RunawayError(RuntimeError):
    """The machine ran past its instruction budget."""


class Bus:
    """The CPU's view of memory, with the few addresses that are not RAM."""

    def __init__(self, machine: "NesMachine"):
        self.machine = machine
        self.memory = machine.memory

    def __getitem__(self, address: int) -> int:
        if address == PPU_STATUS:
            # Alternate vblank on and off, so loops waiting for either edge end.
            self.machine.ppu_status ^= 0x80
            return self.machine.ppu_status
        if address == CONTROLLER_PORT:
            return self.machine.read_controller()
        if address == SECOND_CONTROLLER_PORT:
            return 0
        return self.memory[address]

    def __setitem__(self, address: int, value: int) -> None:
        if address >= 0x8000:
            self.machine.mmc1_write(address, value)
        elif address == CONTROLLER_PORT:
            self.machine.strobe_controller(value)
        else:
            self.memory[address] = value


class PpuBus(Bus):
    """`Bus`, with the PPU's registers and the sprite DMA wired to `PpuState`."""

    def __init__(self, machine: "NesMachine", ppu: PpuState):
        super().__init__(machine)
        self.ppu = ppu

    def __getitem__(self, address: int) -> int:
        if 0x2000 <= address < 0x4000:
            ppu = self.ppu
            register = address & 7
            if register == 2:
                return ppu.read_status(super().__getitem__(PPU_STATUS))
            if register == 7:
                return ppu.read_data()
            return 0
        return super().__getitem__(address)

    def __setitem__(self, address: int, value: int) -> None:
        if 0x2000 <= address < 0x4000:
            self.ppu.write(address & 7, value)
        elif address == OAM_DMA:
            self.ppu.dma(bytes(self.memory[value << 8 : (value << 8) + 0x100]))
        else:
            super().__setitem__(address, value)


class NesMachine:
    def __init__(self, rom: RomReader, ppu: bool = False):
        self.banks = [
            rom.read_prg(bank * PRG_BANK_SIZE, PRG_BANK_SIZE)
            for bank in range(rom.prg_banks)
        ]
        self.memory = bytearray(0x10000)
        self.memory[0xC000:0x10000] = self.banks[FIXED_BANK]
        self.bank = -1
        self.select_bank(0)
        #: The PPU's memory, kept only when asked for.
        self.ppu = PpuState() if ppu else None
        self.cpu = MPU(memory=Bus(self) if self.ppu is None else PpuBus(self, self.ppu))
        self.cpu.sp = 0xFF

        self._shift = 0
        self._shift_count = 0

        #: Buttons held this frame; set by `on_frame`.
        self.buttons = 0
        self._latched = 0
        self._strobe = False

        self.ppu_status = 0
        self.frames = 0
        #: Called at the start of every frame, before the NMI reads the pad.
        self.on_frame: Callable[[int], None] = lambda frame: None
        #: (bank or None for the fixed bank, address) -> called before that instruction.
        self.breakpoints: dict[tuple[int | None, int], Callable[[], None]] = {}
        self._breakpoint_pcs: set[int] = set()

    # --- mapper ---------------------------------------------------------

    def select_bank(self, bank: int) -> None:
        if bank != self.bank:
            self.memory[0x8000:0xC000] = self.banks[bank]
            self.bank = bank

    def mmc1_write(self, address: int, value: int) -> None:
        if value & 0x80:
            self._shift = self._shift_count = 0
            return
        self._shift |= (value & 1) << self._shift_count
        self._shift_count += 1
        if self._shift_count == 5:
            if address < 0xA000 and self.ppu is not None:
                self.ppu.mirroring = self._shift & 3
            if address >= 0xE000:
                self.select_bank(self._shift & 0x0F)
            self._shift = self._shift_count = 0

    # --- controller -----------------------------------------------------

    def strobe_controller(self, value: int) -> None:
        self._strobe = bool(value & 1)
        if self._strobe:
            self._latched = self.buttons

    def read_controller(self) -> int:
        if self._strobe:
            return self.buttons >> 7 & 1
        bit = self._latched >> 7 & 1
        self._latched = (self._latched << 1) & 0xFF | 1
        return bit

    # --- running --------------------------------------------------------

    def _nmi(self) -> None:
        self.frames += 1
        self.on_frame(self.frames)
        self.cpu.nmi()

    def add_breakpoint(
        self, address: int, callback: Callable[[], None], bank: int | None = None
    ) -> None:
        self.breakpoints[(None if address >= 0xC000 else bank, address)] = callback
        self._breakpoint_pcs.add(address)

    def step(self) -> None:
        cpu = self.cpu
        if cpu.pc in self._breakpoint_pcs:
            key = (None if cpu.pc >= 0xC000 else self.bank, cpu.pc)
            callback = self.breakpoints.get(key)
            if callback is not None:
                callback()
        if cpu.pc == VBLANK_BUSY_LOOP and not self.memory[VBLANK_FLAG]:
            # The game is waiting for vblank: this is where a frame ends.
            self._nmi()
        cpu.step()

    def call(
        self,
        address: int,
        bank: int | None = None,
        limit: int = 50_000_000,
        stop: Callable[[], bool] | None = None,
    ) -> None:
        """
        JSR to `address` (in `bank`, if given) and run until it returns, or
        until `stop` says so, checked once per frame.
        """
        if bank is not None:
            self.select_bank(bank)
            self.memory[CURRENT_BANK] = bank
        cpu = self.cpu
        sentinel = 0x0001
        cpu.stPushWord(sentinel - 1)
        cpu.pc = address
        frames = self.frames
        for _ in range(limit):
            if cpu.pc == sentinel:
                return
            self.step()
            if stop is not None and self.frames != frames:
                frames = self.frames
                if stop():
                    return
        raise RunawayError(f"${address:04X} still running at ${cpu.pc:04X}")
