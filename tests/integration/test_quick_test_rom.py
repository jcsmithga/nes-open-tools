"""Integration: a quick-test ROM boots onto the hole's tee, under emulation."""

from pathlib import Path

import pytest

from golf.core.patches.quick_test import build_quick_test_rom
from golf.core.rom_reader import RomReader
from golf.formats.hole_data import HoleData
from golf.physics.nes import BUTTON_START, NesMachine

ROM_PATH = Path(__file__).resolve().parents[2] / "nes_open_us.nes"

pytestmark = [
    pytest.mark.skipif(not ROM_PATH.exists(), reason=f"{ROM_PATH.name} not present"),
    pytest.mark.vanilla_data,
]

RESET_VECTOR = 0xFFFC
OUTER_LOOP = 0x8000  # bank 13, the quick-test loop
SHOT_SETUP_SEQUENCE = 0x877A  # bank 13
#: The signpost's exit spins here until the PPU buffer drains, without
#: `WaitForVblank`, so the machine never runs the NMI that drains it
SIGNPOST_BUFFER_WAIT = 0xABE1  # bank 12
PPU_BUFFER_READ, PPU_BUFFER_WRITE = 0x3C, 0x3B

HOLE_NUMBER = 0x94
WIND_DIRECTION, WIND_SPEED = 0x96, 0x97
TEE_BLOCKS_X, TEE_BLOCKS_Y, TEE_BLOCKS_Y_HIGH = 0x010E, 0x010F, 0x0110
BALL_X, BALL_Y, BALL_Y_HIGH = 0x0115, 0x0119, 0x011B  # player 0
MANUAL_WIND_MODE_FLAG = 0x04F6

FRAME_LIMIT = 600
STEP_LIMIT = 20_000_000


class QuickTestMachine:
    """A quick-test ROM booted from reset, stopping each time a shot is set up."""

    def __init__(self, rom: bytes):
        self.machine = NesMachine(RomReader.from_bytes(rom))
        memory = self.machine.memory
        self.machine.cpu.pc = memory[RESET_VECTOR] | memory[RESET_VECTOR + 1] << 8
        self.shots = 0
        self.loops = 0
        self.machine.add_breakpoint(SHOT_SETUP_SEQUENCE, self._shot, bank=13)
        self.machine.add_breakpoint(OUTER_LOOP, self._loop, bank=13)
        self.machine.add_breakpoint(SIGNPOST_BUFFER_WAIT, self._drain, bank=12)

    def _shot(self) -> None:
        self.shots += 1

    def _loop(self) -> None:
        self.loops += 1

    def _drain(self) -> None:
        memory = self.machine.memory
        if memory[PPU_BUFFER_READ] != memory[PPU_BUFFER_WRITE]:
            self.machine._nmi()  # noqa: SLF001 - the wait a real vblank would end

    def run_to_next_shot(self) -> None:
        shots = self.shots
        for _ in range(STEP_LIMIT):
            if self.shots != shots:
                return
            if self.machine.frames > FRAME_LIMIT:
                break
            self.machine.step()
        raise AssertionError(
            f"no shot set up by frame {self.machine.frames}, "
            f"at ${self.machine.cpu.pc:04X} in bank {self.machine.bank}"
        )

    def ram(self, address: int) -> int:
        return self.machine.memory[address]


@pytest.fixture(scope="module")
def us_hole_1(vanilla_courses) -> HoleData:
    hole = HoleData()
    hole.load(vanilla_courses / "us" / "hole_01.json")
    return hole


@pytest.fixture(scope="module")
def booted(us_hole_1) -> QuickTestMachine:
    machine = QuickTestMachine(build_quick_test_rom(ROM_PATH.read_bytes(), us_hole_1))
    machine.run_to_next_shot()
    return machine


def ball_and_tee(machine: QuickTestMachine) -> tuple[tuple[int, int], tuple[int, int]]:
    ball = (
        machine.ram(BALL_X),
        machine.ram(BALL_Y) | machine.ram(BALL_Y_HIGH) << 8,
    )
    tee = (
        machine.ram(TEE_BLOCKS_X),
        machine.ram(TEE_BLOCKS_Y) | machine.ram(TEE_BLOCKS_Y_HIGH) << 8,
    )
    return ball, tee


def test_boots_onto_the_tee_in_practice_mode(booted, us_hole_1):
    tee = us_hole_1.metadata["tee"]
    assert ball_and_tee(booted) == ((tee["x"], tee["y"]),) * 2
    assert booted.ram(HOLE_NUMBER) == 0
    assert booted.loops == 1
    assert booted.ram(MANUAL_WIND_MODE_FLAG) == 0x80
    assert (booted.ram(WIND_DIRECTION), booted.ram(WIND_SPEED)) == (0, 0)


def test_start_before_a_shot_returns_to_the_tee(us_hole_1):
    machine = QuickTestMachine(build_quick_test_rom(ROM_PATH.read_bytes(), us_hole_1))
    machine.run_to_next_shot()
    # Tap Start every few frames, as `rom_game` taps A, until the hole restarts
    first = machine.machine.frames
    machine.machine.on_frame = lambda frame: setattr(
        machine.machine, "buttons", BUTTON_START if frame % 6 == 0 else 0
    )
    machine.run_to_next_shot()
    tee = us_hole_1.metadata["tee"]
    assert ball_and_tee(machine) == ((tee["x"], tee["y"]),) * 2
    # Back through the loop, so the whole hole setup ran again, signpost and all
    assert machine.loops == 2
    assert machine.machine.frames > first + 60
