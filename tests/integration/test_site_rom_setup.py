"""The ROM setup page in headless Chromium: a dump that differs only in its iNES header is
stored as the vanilla file, and anything else is refused.

This is the only test of rom.js's header retry. Skipped without a Playwright browser or the
vanilla US ROM.
"""

from pathlib import Path

import pytest

from golf.randomizer.catalog import US_ROM
from golf.randomizer.roms import vanilla_rom
from server.app import create_app
from server.config import Config
from server.live import LiveServer

ROOT = Path(__file__).resolve().parents[2]
US_ROM_PATH = ROOT / "nes_open_us.nes"
TIMEOUT_MS = 15_000


def _chromium_launches() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not US_ROM_PATH.exists(), reason="the vanilla US ROM is absent"),
    pytest.mark.skipif(not _chromium_launches(), reason="no Playwright Chromium"),
]

# an iNES 1.0 header for the same cartridge, as older dumps carry
INES_1_HEADER = bytes.fromhex("4e45531a100010000000000000000000")


def _variants() -> dict[str, tuple[bytes, str]]:
    """Each file to choose, and the card state it should end in."""
    vanilla = US_ROM_PATH.read_bytes()
    corrupt = bytearray(vanilla)
    corrupt[0x8000] ^= 0xFF
    return {
        "headerless": (vanilla[16:], "stored"),
        "ines_1_header": (INES_1_HEADER + vanilla[16:], "stored"),
        "different_game": (bytes(corrupt), "error"),
        "headerless_different_game": (bytes(corrupt[16:]), "error"),
        "wrong_size": (vanilla + bytes(512), "error"),
    }


def test_header_variants_are_stored_as_the_vanilla_rom(tmp_path):
    from playwright.sync_api import sync_playwright

    rom = vanilla_rom(US_ROM)
    card = f'article.rom[data-rom-id="{US_ROM}"]'
    app = create_app(Config(database=":memory:"))
    errors: list[str] = []
    outcomes: dict[str, tuple[str, str | None]] = {}
    with LiveServer(app) as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for name, (data, _) in _variants().items():
                path = tmp_path / f"{name}.nes"
                path.write_bytes(data)
                page = browser.new_context().new_page()
                page.set_default_timeout(TIMEOUT_MS)
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(base + "/rom")
                page.wait_for_selector(f'{card}[data-state="empty"]')
                page.set_input_files(f"{card} input[type=file]", str(path))
                page.wait_for_selector(
                    f'{card}:is([data-state="stored"], [data-state="error"])'
                )
                state = page.get_attribute(card, "data-state") or ""
                stored = page.evaluate(
                    """async (id) => {
                        const record = await getRom(id);
                        return record ? await sha1Hex(record.bytes) : null;
                    }""",
                    US_ROM,
                )
                outcomes[name] = (state, stored)
                page.context.close()
        finally:
            browser.close()

    assert not errors
    for name, (_, expected_state) in _variants().items():
        state, stored = outcomes[name]
        assert state == expected_state, name
        assert stored == (rom.sha1 if expected_state == "stored" else None), name
