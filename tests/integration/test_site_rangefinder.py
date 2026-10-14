"""The rangefinder's browser interactions against the real site."""

from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest

from golf.core.rng import predict_hole
from golf.randomizer.catalog import Catalog, HoleStore
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.manifest import Settings
from server.app import create_app
from server.config import Config
from server.live import LiveServer
from server.seeds import insert_seed
from server.strings import Entry, Strings
from server.yardage_book import tee_wind
from tests.synthetic_holes import synthetic_hole
from tests.unit.server_app.helpers import IPS, FakeBuilder

TIMEOUT_MS = 15_000


def _unwritten() -> Strings:
    """The real catalog with no text, so every string renders as the placeholder naming its key."""
    real = Strings.load()
    return Strings({key: Entry(real.entry(key).note, "") for key in real.keys()})  # noqa: SIM118 (Strings, not a dict)


pytestmark = pytest.mark.usefixtures("chromium_available")


def test_rangefinder_measures_zooms_switches_holes_and_opens_green(rangefinder_assets):
    from playwright.sync_api import sync_playwright

    app = create_app(Config(database=":memory:"), strings=_unwritten())
    errors: list[str] = []
    with LiveServer(app) as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.set_default_timeout(TIMEOUT_MS)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/rangefinder")
            page.wait_for_selector('.rangefinder[data-state="ready"]')

            image = page.locator("#hole-image")
            viewer = page.locator(".rangefinder-viewer")
            viewer_height = viewer.evaluate("element => element.clientHeight")
            assert image.evaluate("image => image.clientWidth") == 352
            image.click(position={"x": 40, "y": 40})
            image.click(position={"x": 40, "y": 140})
            assert (
                page.locator("#distance-display").inner_text()
                == "⟦rangefinder.script.distance distance=100.0⟧"
            )

            page.click("#zoom-in")
            assert image.evaluate("image => image.clientWidth") == 528

            page.click("#zoom-reset")
            page.select_option("#hole-select", "7")
            page.wait_for_function(
                "element => element.scrollTop > 0 && element.scrollTop + element.clientHeight === element.scrollHeight",
                arg=viewer.element_handle(),
            )
            assert viewer.evaluate("element => element.clientHeight") == viewer_height

            page.select_option("#hole-select", "2")
            assert "/images/japan/hole_02.png" in (image.get_attribute("src") or "")
            assert viewer.evaluate("element => element.clientHeight") == viewer_height
            assert (
                page.locator("#distance-display").inner_text()
                == "⟦rangefinder.script.distance_empty⟧"
            )

            page.click("#green-view")
            page.wait_for_selector("#green-modal[open]")
            assert (
                page.locator("#flag-indicator").inner_text()
                == "⟦rangefinder.script.flag current=1 total=4⟧"
            )
            page.keyboard.press("ArrowRight")
            assert (
                page.locator("#flag-indicator").inner_text()
                == "⟦rangefinder.script.flag current=2 total=4⟧"
            )
            page.keyboard.press("Escape")
            assert not page.locator("#green-modal").evaluate("dialog => dialog.open")
        finally:
            browser.close()

    assert not errors


def test_rangefinder_permalink_tracks_location_copies_and_does_not_add_history(
    rangefinder_assets,
):
    from playwright.sync_api import sync_playwright

    app = create_app(Config(database=":memory:"), strings=_unwritten())
    errors: list[str] = []
    with LiveServer(app) as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(
                permissions=["clipboard-read", "clipboard-write"]
            )
            page = context.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/")
            page.goto(base + "/rangefinder?course=us&hole=3")
            page.wait_for_selector('.rangefinder[data-state="ready"]')

            assert page.locator("#course-select").input_value() == "us"
            assert page.locator("#hole-select").input_value() == "3"
            history_length = page.evaluate("history.length")

            page.select_option("#hole-select", "4")
            assert parse_qs(urlsplit(page.url).query) == {
                "course": ["us"],
                "hole": ["4"],
            }
            assert page.evaluate("history.length") == history_length

            page.click("#copy-permalink")
            assert await_text(page, "navigator.clipboard.readText()") == page.url
            assert (
                page.locator("#permalink-status").inner_text()
                == "⟦rangefinder.script.permalink_copied⟧"
            )

            page.go_back()
            assert page.url == base + "/"

            page.goto(base + "/rangefinder?course=not-a-course&hole=999")
            page.wait_for_selector('.rangefinder[data-state="ready"]')
            assert parse_qs(urlsplit(page.url).query) == {
                "course": ["japan"],
                "hole": ["1"],
            }
        finally:
            browser.close()

    assert not errors


def await_text(page, expression: str) -> str:
    page.wait_for_function(f"async () => Boolean(await {expression})")
    return page.evaluate(f"async () => await {expression}")


def test_a_yardage_book_shows_one_course_at_the_seeds_pins_with_tee_wind(tmp_path):
    from playwright.sync_api import sync_playwright

    builder = FakeBuilder(
        Catalog.load(),
        CurationSnapshot.load(),
        HoleStore(tmp_path / "no-holes"),
        tmp_path / "unused.nes",
    )
    manifest = builder.generate(Settings(prng_seed="yardage-browser"))
    # Every hole transformed, as the book shows a hole as built.
    slots = tuple(
        replace(slot, transforms=("mirror@1",)) for slot in manifest.course.holes
    )
    manifest = replace(manifest, course=replace(manifest.course, holes=slots))
    holes = [synthetic_hole(number) for number in range(1, 19)]
    app = create_app(
        Config(database=":memory:", rangefinder_dir=tmp_path / "rangefinder"),
        strings=_unwritten(),
        builder=builder,
    )
    errors: list[str] = []
    with LiveServer(app) as base, sync_playwright() as playwright:
        seed_id = insert_seed(app.state.db, manifest, IPS, holes, builder.catalog)
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.set_default_timeout(TIMEOUT_MS)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "requestfailed", lambda request: errors.append(f"failed {request.url}")
            )
            page.goto(f"{base}/h/{seed_id}/book")
            page.wait_for_selector('.rangefinder[data-state="ready"]')

            assert not page.locator("#course-select").is_visible()
            assert page.locator("#hole-select").input_value() == "1"
            assert page.locator("#hole-select option").count() == 18
            image = page.locator("#hole-image")
            page.wait_for_function(
                "image => image.naturalWidth === 176", arg=image.element_handle()
            )

            def shows(number: int) -> None:
                slot = slots[number - 1]
                pin = predict_hole(slot.wind_seed).pin_index
                direction, _ = tee_wind(slot)
                assert f"/main_pin_{pin}.png" in (image.get_attribute("src") or "")
                assert (
                    page.locator("#hole-wind-arrow").evaluate(
                        "arrow => arrow.style.rotate"
                    )
                    == f"{direction * 360 / 256:g}deg"
                )
                assert page.locator("#hole-wind-text").inner_text() != ""

            shows(1)
            page.select_option("#hole-select", "5")
            shows(5)
            assert parse_qs(urlsplit(page.url).query) == {"hole": ["5"]}

            page.click("#green-view")
            page.wait_for_selector("#green-modal[open]")
            pin = predict_hole(slots[4].wind_seed).pin_index
            overlay = page.locator("#flag-overlay")
            assert f"/green_flag_{pin}.png" in (overlay.get_attribute("src") or "")
            assert page.locator("#flag-indicator").count() == 0
            page.keyboard.press("ArrowRight")
            assert f"/green_flag_{pin}.png" in (overlay.get_attribute("src") or "")
            page.keyboard.press("Escape")

            page.goto(f"{base}/h/{seed_id}/book?hole=12")
            page.wait_for_selector('.rangefinder[data-state="ready"]')
            assert page.locator("#hole-select").input_value() == "12"
            shows(12)
        finally:
            browser.close()

    assert not errors
