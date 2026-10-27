"""`golf.physics.ppu`: the PPU's memory as the game's register writes leave it."""

from golf.physics.ppu import HORIZONTAL, ONE_SCREEN_UPPER, VERTICAL, PpuState

ADDRESS, DATA = 6, 7


def point(ppu: PpuState, address: int) -> None:
    ppu.write(ADDRESS, address >> 8)
    ppu.write(ADDRESS, address & 0xFF)


def test_data_writes_advance_by_one_or_by_a_row():
    ppu = PpuState()
    point(ppu, 0x0010)
    ppu.write(DATA, 0xAA)
    ppu.write(DATA, 0xBB)
    assert ppu.vram[0x10:0x12] == b"\xaa\xbb"
    ppu.write(0, 0x04)
    point(ppu, 0x2000)
    ppu.write(DATA, 1)
    ppu.write(DATA, 2)
    assert (ppu.vram[0x2000], ppu.vram[0x2020]) == (1, 2)


def test_reading_status_resets_the_address_latch():
    ppu = PpuState()
    ppu.write(ADDRESS, 0x21)
    ppu.read_status(0x80)
    point(ppu, 0x0123)
    ppu.write(DATA, 7)
    assert ppu.vram[0x0123] == 7


def test_scroll_takes_x_then_y():
    ppu = PpuState()
    ppu.write(5, 12)
    ppu.write(5, 34)
    assert (ppu.scroll_x, ppu.scroll_y) == (12, 34)


def test_nametables_follow_the_mirroring():
    ppu = PpuState(mirroring=HORIZONTAL)
    assert ppu.nametable_address(0x2400) == 0x2000
    assert ppu.nametable_address(0x2800) == 0x2400
    ppu.mirroring = VERTICAL
    assert ppu.nametable_address(0x2800) == 0x2000
    assert ppu.nametable_address(0x2C05) == 0x2405
    ppu.mirroring = ONE_SCREEN_UPPER
    assert ppu.nametable_address(0x2000) == 0x2400


def test_sprite_palettes_backdrop_mirrors_the_background():
    ppu = PpuState()
    point(ppu, 0x3F10)
    ppu.write(DATA, 0x0F)
    assert ppu.vram[0x3F00] == 0x0F
    point(ppu, 0x3F25)
    ppu.write(DATA, 0x16)
    assert ppu.vram[0x3F05] == 0x16


def test_reads_are_buffered_except_palettes():
    ppu = PpuState()
    ppu.vram[0x0100:0x0102] = b"\x11\x22"
    ppu.vram[0x3F01] = 0x30
    point(ppu, 0x0100)
    assert ppu.read_data() == 0
    assert ppu.read_data() == 0x11
    point(ppu, 0x3F01)
    assert ppu.read_data() == 0x30


def test_dma_copies_a_page_from_the_oam_address():
    ppu = PpuState()
    ppu.write(3, 0x10)
    ppu.dma(bytes(range(256)))
    assert ppu.oam[0x10] == 0
    assert ppu.oam[0x0F] == 0xFF
