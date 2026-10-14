"""`/h/<id>/book`: a seed's yardage book and the metadata its rangefinder reads."""

import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from golf.core.rng import predict_hole
from golf.randomizer.catalog import HoleStore, content_hash
from golf.randomizer.manifest import Manifest, Settings
from golf.rendering.rangefinder import RENDER_VERSION
from server.app import create_app
from server.config import Config
from server.seeds import insert_seed, withdraw_seed
from server.users import sign_in
from server.yardage_book import COURSE
from tests.app_state import app_state
from tests.synthetic_holes import built_holes, synthetic_hole
from tests.unit.server_app.helpers import IPS, UNWRITTEN, FakeBuilder

#: what the seed's build made; any 18 holes do, since nothing here transforms
HOLES = [synthetic_hole(number) for number in range(1, 19)]


def book_client(tmp_path, catalog, curation) -> TestClient:
    """A client whose renders go under `tmp_path`, with an empty hole store: a book reads
    only the seed's stored holes."""
    store = HoleStore(tmp_path / "no-holes")
    builder = FakeBuilder(catalog, curation, store, tmp_path / "unused.nes")
    config = Config(database=":memory:", rangefinder_dir=tmp_path / "rangefinder")
    return TestClient(create_app(config, strings=UNWRITTEN, builder=builder))


@pytest.fixture
def client(tmp_path, catalog, curation):
    with book_client(tmp_path, catalog, curation) as test_client:
        yield test_client


def plain_manifest(client: TestClient) -> Manifest:
    return app_state(client).builder.generate(Settings(prng_seed="yardage"))


def transformed_seed(client: TestClient) -> tuple[str, Manifest]:
    """A seed whose every hole has a transform, built as `HOLES`."""
    manifest = plain_manifest(client)
    slots = tuple(
        replace(slot, transforms=("mirror@1",)) for slot in manifest.course.holes
    )
    manifest = replace(manifest, course=replace(manifest.course, holes=slots))
    state = app_state(client)
    seed_id = insert_seed(state.db, manifest, IPS, HOLES, state.builder.catalog)
    return seed_id, manifest


def book_holes(client: TestClient, seed_id: str) -> list[dict]:
    response = client.get(f"/h/{seed_id}/book.json")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-cache"
    courses = response.json()["courses"]
    assert list(courses) == [COURSE]
    return courses[COURSE]["holes"]


def test_the_book_is_the_seeds_holes_in_order_as_built(client):
    seed_id, manifest = transformed_seed(client)
    course = client.get(f"/h/{seed_id}/book.json").json()["courses"][COURSE]
    assert course["name"] == " ".join(manifest.course.magic_words)
    holes = course["holes"]
    assert [hole["number"] for hole in holes] == list(range(1, 19))
    for hole, slot, built in zip(holes, manifest.course.holes, HOLES, strict=True):
        assert hole["par"] == slot.par
        assert hole["distance"] == built.metadata["distance"]
        assert (hole["width"], hole["height"]) == (176, 240)
        assert f"/variants/{content_hash(built)}/" in hole["image"]


def test_each_hole_shows_only_the_seeds_pin(client):
    seed_id, manifest = transformed_seed(client)
    for hole, slot, built in zip(
        book_holes(client, seed_id), manifest.course.holes, HOLES, strict=True
    ):
        pin = predict_hole(slot.wind_seed).pin_index
        base = f"/rangefinder-data/variants/{content_hash(built)}"
        version = f"?v={RENDER_VERSION}"
        assert hole["image"] == f"{base}/main_pin_{pin}.png{version}"
        assert hole["green_image"] == f"{base}/green.png{version}"
        assert hole["flag_images"] == [f"{base}/green_flag_{pin}.png{version}"]


def test_each_hole_shows_its_tee_shot_wind_and_no_later_one(client):
    seed_id, manifest = transformed_seed(client)
    for hole, slot in zip(
        book_holes(client, seed_id), manifest.course.holes, strict=True
    ):
        forecast = predict_hole(slot.wind_seed, swings=3, anchors=slot.wind)
        direction, speed = forecast.winds[0]
        assert hole["wind"].keys() == {"direction", "compass", "speed"}
        assert (hole["wind"]["direction"], hole["wind"]["speed"]) == (direction, speed)
        assert 0 <= speed <= 9


def test_the_books_images_are_rendered_and_served_for_good(client, tmp_path):
    seed_id, _ = transformed_seed(client)
    hole = book_holes(client, seed_id)[0]
    for url in (hole["image"], hole["green_image"], *hole["flag_images"]):
        image = client.get(url)
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert "immutable" in image.headers["cache-control"]
    rendered = list((tmp_path / "rangefinder").rglob("*.png"))
    assert len(rendered) == 3 * 18
    stamps = {path: path.stat().st_mtime_ns for path in rendered}
    book_holes(client, seed_id)
    assert {path: path.stat().st_mtime_ns for path in rendered} == stamps


def test_a_hole_without_transforms_is_the_one_stored_with_the_seed(client):
    state = app_state(client)
    manifest = plain_manifest(client)
    built = built_holes(manifest, state.builder.catalog)
    seed_id = insert_seed(state.db, manifest, IPS, built, state.builder.catalog)
    holes = book_holes(client, seed_id)
    for hole, made in zip(holes, built, strict=True):
        assert f"/variants/{content_hash(made)}/" in hole["image"]
        assert hole["distance"] == made.metadata["distance"]
    assert client.get(holes[0]["image"]).status_code == 200


def test_the_book_page_is_the_viewer_for_one_course_at_one_pin(client):
    seed_id, manifest = transformed_seed(client)
    response = client.get(f"/h/{seed_id}/book")
    assert response.status_code == 200
    page = response.text
    assert 'data-kind="book"' in page
    assert f'data-metadata-url="/h/{seed_id}/book.json"' in page
    assert " ".join(manifest.course.magic_words) in page
    assert f'href="/h/{seed_id}"' in page
    assert 'id="hole-wind-arrow"' in page
    assert 'id="flag-indicator"' not in page
    assert re.search(r'src="/static/rangefinder/app\.js\?v=[0-9a-f]{12}"', page)
    assert '"rangefinder.script.tee_wind": null' in page


def test_the_rangefinder_page_keeps_its_courses_and_pins(client):
    page = client.get("/rangefinder").text
    assert 'data-kind="catalog"' in page
    assert 'id="flag-indicator"' in page
    assert 'id="hole-wind-arrow"' not in page


def test_the_seed_page_links_to_its_book(client):
    seed_id, _ = transformed_seed(client)
    assert f'href="/h/{seed_id}/book"' in client.get(f"/h/{seed_id}").text


def test_a_withdrawn_seed_keeps_its_book(client):
    seed_id, _ = transformed_seed(client)
    db = app_state(client).db
    admin = sign_in(db, discord_id="1", username="admin", global_name=None, avatar=None)
    withdraw_seed(db, seed_id, admin.id)
    assert client.get(f"/h/{seed_id}/book").status_code == 200
    assert len(book_holes(client, seed_id)) == 18


def test_a_seed_that_does_not_exist_has_no_book(client):
    page = client.get("/h/0000000001/book")
    assert page.status_code == 404
    assert page.headers["content-type"].startswith("text/html")
    metadata = client.get("/h/0000000001/book.json")
    assert metadata.status_code == 404
    assert metadata.headers["content-type"] == "application/json"
