"""The link preview card every page carries: the Open Graph tags in `base.html`."""

import re
from html import unescape

import pytest
from fastapi.testclient import TestClient

from golf.randomizer.catalog import JP_ROM
from golf.randomizer.roms import vanilla_rom
from server.app import create_app
from server.config import Config
from server.static_files import LOGO_SIZE
from server.strings import Strings
from tests.unit.server_app.helpers import (
    catalog_with_text,
    dev_client,
    entered_seed,
    generate_seed,
    legacy_seed,
    scan_path,
    seed_form,
)

BASE_URL = "http://127.0.0.1:8000"

#: each card string as the values its page passes it, so a test reads those and no English
CARD_TEXT = {
    "site.card_name": "name",
    "site.card_description": "site",
    "seed.card.title": "{words}",
    "seed.card.description": "{par}|{yards}|{mode}",
    "seed.card.mode_standard": "standard",
    "seed.card.mode_natural_par": "natural_par",
    "seed.card.mode_executive": "executive",
    "seed.card.mode_par_3": "par_3",
    "seed.card.mode_vanilla_course": '{rom}|"{course}"',
    "round.card.title": "{words}",
    "round.card.player_one": "one|{name}",
    "round.card.player_two": "two|{name}",
    "yardage_book.card.title": "{words}",
    "yardage_book.card.description": "book",
}


def card_strings() -> Strings:
    real = Strings.load()
    return catalog_with_text(lambda key: CARD_TEXT.get(key, real.entry(key).text))


def card(page: str) -> dict[str, str]:
    """The page's Open Graph tags, by property without its `og:`."""
    found = re.findall(r'<meta property="og:([a-z:_]+)"\s+content="([^"]*)">', page)
    assert len(found) == len(dict(found)), found
    return {name: unescape(content) for name, content in found}


def test_every_card_string_has_a_stand_in():
    keys = Strings.load().keys()
    assert {key for key in keys if ".card" in key} == set(CARD_TEXT)


def test_a_page_without_a_card_of_its_own_gets_the_sites(fake_builder):
    with dev_client(fake_builder, strings=card_strings()) as test_client:
        page = test_client.get("/rom")
        tags = card(page.text)
    [title] = re.findall(r"<title>([^<]*)</title>", page.text)
    assert tags["site_name"] == "name"
    assert tags["type"] == "website"
    assert tags["url"] == f"{BASE_URL}/rom"
    assert tags["title"] == unescape(title)
    assert tags["description"] == "site"


def test_the_card_shows_the_logo_by_its_full_versioned_url(client):
    tags = card(client.get("/").text)
    image = re.fullmatch(
        rf"{BASE_URL}(/static/logo\.png\?v=[0-9a-f]{{12}})", tags["image"]
    )
    assert image is not None
    assert client.get(image.group(1)).headers["content-type"] == "image/png"
    assert tags["image:width"] == tags["image:height"] == str(LOGO_SIZE)


def test_the_cards_stripe_is_the_logos_green(client):
    page = client.get("/").text
    assert '<meta name="theme-color" content="#37946e">' in page


def test_card_urls_are_on_the_public_site(fake_builder):
    config = Config(database=":memory:", base_url="https://golf.example/")
    with TestClient(create_app(config, builder=fake_builder)) as test_client:
        tags = card(test_client.get("/generate?par=72").text)
    assert tags["url"] == "https://golf.example/generate"
    assert tags["image"].startswith("https://golf.example/static/logo.png?v=")


def test_a_seeds_card_names_its_words_par_and_length(fake_builder):
    with dev_client(fake_builder, strings=card_strings()) as test_client:
        seed_id = generate_seed(test_client)
        course = test_client.get(f"/h/{seed_id}.json").json()["course"]
        page = test_client.get(f"/h/{seed_id}").text
    tags = card(page)
    # the hole table's totals row
    [(par, yards)] = re.findall(
        r'<td class="num">(\d+)</td>\s*<td class="num">(\d+)</td>\s*</tr>\s*</tfoot>',
        page,
    )
    assert tags["url"] == f"{BASE_URL}/h/{seed_id}"
    assert tags["title"] == " ".join(course["magic_words"])
    assert tags["description"] == f"{par}|{yards}|standard"


@pytest.mark.parametrize("mode", ["standard", "natural_par", "executive", "par_3"])
def test_a_seeds_card_names_its_mode(fake_builder, mode):
    with dev_client(fake_builder, strings=card_strings()) as test_client:
        seed_id = generate_seed(test_client, seed_form(mode=mode))
        tags = card(test_client.get(f"/h/{seed_id}").text)
    assert tags["description"].endswith(f"|{mode}")


def test_a_vanilla_seeds_card_names_its_course_and_rom(fake_builder):
    with dev_client(fake_builder, strings=card_strings()) as test_client:
        seed_id = generate_seed(
            test_client, seed_form(mode="vanilla_course", course="jp_australia")
        )
        tags = card(test_client.get(f"/h/{seed_id}").text)
    rom = vanilla_rom(JP_ROM).title
    # the card's quotes reach the tag escaped once, not twice
    assert tags["description"].endswith(f'|{rom}|"Australia"')


def test_a_seed_from_before_modes_names_no_mode(fake_builder):
    """Its par may be 70 or 71, which `standard` would misname."""
    with dev_client(fake_builder, strings=card_strings()) as test_client:
        seed_id = legacy_seed(test_client)
        tags = card(test_client.get(f"/h/{seed_id}").text)
    assert tags["description"].endswith("|")


def test_a_seeds_card_stays_the_same_once_rounds_are_recorded(fake_builder):
    """A chat keeps the card it first drew, and a score would spoil the seed."""
    with dev_client(fake_builder, strings=card_strings()) as test_client:
        seed_id = entered_seed(test_client, "alice")
        before = card(test_client.get(f"/h/{seed_id}").text)
        test_client.get(scan_path(test_client, seed_id, "alice", strokes=5))
        after = card(test_client.get(f"/h/{seed_id}").text)
    assert after == before


def test_a_rounds_card_names_the_player_and_the_seed_and_no_score(fake_builder):
    with dev_client(fake_builder, strings=card_strings()) as test_client:
        seed_id = entered_seed(test_client, "alice")
        course = test_client.get(f"/h/{seed_id}.json").json()["course"]
        scanned = test_client.get(scan_path(test_client, seed_id, "alice", strokes=5))
        other = test_client.get(scan_path(test_client, seed_id, "alice", slot=1))
    tags = card(scanned.text)
    # the scan redirects to `?recorded`, which the card's address leaves out
    assert scanned.url.query == b"recorded"
    assert tags["url"] == f"{BASE_URL}{scanned.url.path}"
    assert tags["title"] == " ".join(course["magic_words"])
    assert tags["description"] == "one|alice"
    assert card(other.text)["description"] == "two|alice"


def test_a_yardage_books_card_names_its_seed(fake_builder):
    with dev_client(fake_builder, strings=card_strings()) as test_client:
        seed_id = generate_seed(test_client)
        course = test_client.get(f"/h/{seed_id}.json").json()["course"]
        tags = card(test_client.get(f"/h/{seed_id}/book").text)
    assert tags["url"] == f"{BASE_URL}/h/{seed_id}/book"
    assert tags["title"] == " ".join(course["magic_words"])
    assert tags["description"] == "book"
