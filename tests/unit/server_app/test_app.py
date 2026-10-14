"""The app factory: startup, the pages and files every route shares, the error pages and how strings render."""

import re

import pytest
from fastapi.testclient import TestClient

from golf.randomizer.catalog import HoleStore
from golf.randomizer.manifest import Settings
from golf.randomizer.roms import VANILLA_ROMS
from server.app import create_app
from server.builder import SeedBuilder
from server.config import Config
from server.db import Database, DatabaseError
from server.migrations import MIGRATIONS
from tests.app_state import app_state
from tests.stored_seeds import store_seed, unfill
from tests.unit.server_app.helpers import (
    UNWRITTEN,
    FakeBuilder,
    app_client,
    catalog_with_text,
    generate_seed,
    post_download,
    post_generate,
)


class BrokenGenerate(FakeBuilder):
    """A builder with a bug that generating a seed runs into."""

    def generate(self, settings, request=None):
        raise RuntimeError("bug")


class BrokenFinish(FakeBuilder):
    """A builder with a bug that finishing a download runs into."""

    def finish(self, manifest, unfinished_ips, options, credentials=None):
        raise RuntimeError("bug")


def test_startup_migrates_the_database(client):
    assert app_state(client).db.version() == len(MIGRATIONS)


def test_startup_refuses_seeds_stored_before_their_holes_were(tmp_path, fake_builder):
    path = str(tmp_path / "site.db")
    db = Database(path)
    db.migrate()
    seed_id = store_seed(db, fake_builder.generate(Settings(prng_seed="old")))
    unfill(db, seed_id)
    db.close()
    app = create_app(Config(database=path), builder=fake_builder)
    with (
        pytest.raises(DatabaseError, match="1 seeds.*golf-site-backfill"),
        TestClient(app),
    ):
        pass


def test_startup_makes_a_builder_from_the_config_when_given_none(tmp_path):
    with TestClient(
        create_app(Config(database=":memory:", rom_dir=tmp_path))
    ) as test_client:
        assert app_state(test_client).builder.rom_path == tmp_path / "nes_open_us.nes"


def test_the_site_name_is_the_link_home(client):
    home = client.get("/").text
    assert re.search(r'<a href="/"\s+class="brand"\s+aria-current="page">', home)
    rom = client.get("/rom").text
    assert re.search(r'<a href="/"\s+class="brand"\s*>', rom)


def test_player_facing_pages_show_the_affiliation_footer(client):
    page = client.get("/").text
    assert '<footer class="container site-footer">' in page
    assert "NES and Mario are trademarks of Nintendo." in page


def test_the_footer_shows_the_site_version(fake_builder):
    with app_client(builder=fake_builder, version="v9.8.7-3-gabc1234") as test_client:
        page = test_client.get("/").text
    footer = page[page.index('<footer class="container site-footer">') :]
    assert '<small class="version">v9.8.7-3-gabc1234</small>' in footer


@pytest.mark.parametrize(
    "path",
    [
        "/static/pico.green.min.css",
        "/static/site.css",
        "/static/romstore.js",
        "/static/rom.js",
        "/static/download.js",
        "/static/favicon.ico",
        "/static/apple-touch-icon.png",
        "/static/logo.png",
    ],
)
def test_static_files_are_served(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.content


def test_pages_use_the_vendored_and_site_stylesheets(client):
    page = client.get("/").text
    assert 'href="/static/pico.green.min.css?v=' in page
    assert 'href="/static/site.css?v=' in page


def test_pages_link_the_site_icons(client):
    page = client.get("/").text
    assert re.search(
        r'<link rel="icon" href="/static/favicon\.ico\?v=[0-9a-f]{12}"', page
    )
    assert re.search(r'href="/static/apple-touch-icon\.png\?v=[0-9a-f]{12}"', page)


def test_the_favicon_is_served_at_the_root(client):
    """For a browser showing a manifest, or a crawler that reads no page."""
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/x-icon"
    assert response.headers["cache-control"] == "no-cache"
    assert response.content == client.get("/static/favicon.ico").content


def test_api_docs_are_not_exposed(client):
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404


@pytest.mark.parametrize(
    "path", ["/h/0000000001", "/h/not-a-seed", "/h/0000000000", "/no-such-page"]
)
def test_unknown_pages_render_not_found(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "not_found.heading" in response.text or "<h1>" in response.text


def broken_client(builder: SeedBuilder) -> TestClient:
    """A client that answers an unhandled exception with the 500 instead of raising it."""
    app = create_app(Config(database=":memory:"), strings=UNWRITTEN, builder=builder)
    return TestClient(app, raise_server_exceptions=False)


def test_an_unhandled_error_renders_the_server_error_page(catalog, curation, tmp_path):
    builder = BrokenGenerate(catalog, curation, HoleStore(), tmp_path / "unused.nes")
    with broken_client(builder) as test_client:
        response = post_generate(test_client)
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/html")
    assert "server_error.heading" in response.text
    request_ref = response.headers["x-request-id"]
    assert f"server_error.reference request_id={request_ref}" in response.text


def test_an_unhandled_error_on_a_machine_path_stays_plain_text(
    catalog, curation, tmp_path
):
    builder = BrokenFinish(catalog, curation, HoleStore(), tmp_path / "unused.nes")
    with broken_client(builder) as test_client:
        response = post_download(test_client, generate_seed(test_client))
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["x-request-id"]


def test_written_strings_render_without_placeholders(fake_builder):
    written = catalog_with_text(lambda key: f"TEXT:{key}")
    with app_client(strings=written, builder=fake_builder) as test_client:
        seed_id = generate_seed(test_client)
        scan = "/s/" + "A" * 48
        pages = {
            path: test_client.get(path).text
            for path in ("/", "/rom", "/generate", f"/h/{seed_id}", "/nope", scan)
        }
    for page in pages.values():
        assert "⟦" not in page
        assert 'class="unwritten"' not in page
    assert "TEXT:home.heading" in pages["/"]
    assert "<title>TEXT:rom.page_title</title>" in pages["/rom"]
    assert '"TEXT:rom.status.stored"' in pages["/rom"]
    assert "TEXT:generate.clubs.heading" in pages["/generate"]
    assert "TEXT:seed.holes.total" in pages[f"/h/{seed_id}"]
    assert "TEXT:seed.download.submit" in pages[f"/h/{seed_id}"]
    assert '"TEXT:seed.download.status.done"' in pages[f"/h/{seed_id}"]
    assert "TEXT:not_found.heading" in pages["/nope"]
    assert "TEXT:scan_rejected.malformed" in pages[scan]


def test_unwritten_strings_render_as_placeholders_with_their_notes(fake_builder):
    unwritten = catalog_with_text(lambda key: "")
    with app_client(strings=unwritten, builder=fake_builder) as test_client:
        rom = test_client.get("/rom").text
    assert "<title>⟦rom.page_title⟧</title>" in rom
    assert (
        '<span class="unwritten" title="ROM setup page h1">⟦rom.heading⟧</span>' in rom
    )
    assert f"⟦rom.expected_hash sha1={VANILLA_ROMS[0].sha1}⟧" in rom
    assert '"rom.status.stored": null' in rom
