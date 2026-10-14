"""`server/routes/seed_pages.py`: the generate form, its refusals and what it stores."""

import json
import logging
import re

import pytest
from fastapi.testclient import TestClient

from golf.randomizer.catalog import HoleStore
from golf.randomizer.generate import GenerationError
from golf.randomizer.manifest import DEFAULT_MERCY_POINT, Manifest
from golf.randomizer.music import TRACKS
from golf.randomizer.roms import VANILLA_ROMS
from golf.randomizer.wind import DIRECTION_PROFILES, SPEED_PROFILES
from server.builder import SeedBuilder
from server.forms import FormState
from server.ratelimit import RateLimiter
from tests.app_state import app_state
from tests.unit.server_app.helpers import (
    IPS,
    UNWRITTEN,
    FakeBuilder,
    app_client,
    dev_client,
    generate_seed,
    post_generate,
    users,
)


class PoolTooSmall(FakeBuilder):
    def generate(self, settings, request=None):
        raise GenerationError("no fill")


def seed_count(client: TestClient) -> int:
    with app_state(client).db.transaction() as conn:
        return conn.execute("SELECT count(*) FROM seeds").fetchone()[0]


def test_the_generate_form_offers_every_setting_with_its_default(client):
    response = client.get("/generate")
    assert response.status_code == 200
    page = response.text
    assert re.search(r'href="/generate"\s+aria-current="page"', page)
    assert 'name="par"' not in page
    mode = re.search(r'<select[^>]*name="mode"[^>]*>(.*?)</select>', page, re.S)
    assert mode is not None
    assert re.findall(r'<option value="(\w+)"\s*(selected)?', mode.group(1)) == [
        ("standard", "selected"),
        ("natural_par", ""),
        ("executive", ""),
        ("par_3", ""),
        ("vanilla_course", ""),
    ]
    course = re.search(r'<select[^>]*name="course">(.*?)</select>', page, re.S)
    assert course is not None
    assert re.findall(r'<option value="(\w+)"', course.group(1)) == list(TRACKS)
    for name in ("mirror_chance", "hazard_chance"):
        select = re.search(
            rf'<select[^>]*name="{name}"[^>]*>(.*?)</select>', page, re.S
        )
        assert select is not None
        assert re.findall(r'<option value="(\d+)"\s*(selected)?', select.group(1)) == [
            ("0", "selected"),
            ("25", ""),
            ("50", ""),
            ("75", ""),
            ("100", ""),
        ]
    assert 'href="/mystery"' not in page
    for rom in VANILLA_ROMS:
        assert re.search(rf'name="sources"\s+value="{rom.id}"\s+checked', page)
    assert 'name="allow_family_repeats"' in page
    assert re.search(r'<option value="random"\s+selected', page)
    assert page.count("<option ") == (
        5 + 8 + 4 + 2 * 5 + 9 + len(SPEED_PROFILES) + len(DIRECTION_PROFILES)
    )
    for name, profiles in [
        ("wind_speed", SPEED_PROFILES),
        ("wind_direction", DIRECTION_PROFILES),
    ]:
        select = re.search(rf'<select[^>]*name="{name}">(.*?)</select>', page, re.S)
        assert select is not None
        options = re.findall(r'<option value="(\w+)"\s*(selected)?', select.group(1))
        assert options == [
            (profile, "selected" if profile == "vanilla" else "")
            for profile in profiles
        ]
    rule = re.search(r'<select[^>]*name="draw_rule"[^>]*>(.*?)</select>', page, re.S)
    assert rule is not None
    assert re.findall(r'<option value="(\w+)"\s*(selected)?', rule.group(1)) == [
        ("experts_0", ""),
        ("experts_1", "selected"),
        ("experts_2", ""),
        ("uniform", ""),
    ]
    assert re.search(r'name="clubs_max"[^>]*value="14"', page)
    assert page.count('name="banned"') == 15
    assert page.count('name="required_bag"') == 15
    assert 'value="PT"' not in page
    assert "mercy" not in page


def test_club_rules_are_a_section_of_their_own_after_the_everyday_settings(client):
    page = client.get("/generate").text
    rules = page.index('<article class="club-rules">')
    assert page.index('name="music"') < rules < page.index('name="clubs_max"')
    assert (
        page.index('name="required_bag"')
        < page.index("</article>", rules)
        < page.index('type="submit"')
    )


def test_generating_stores_the_seed_and_redirects_to_its_page(client, fake_builder):
    seed_id = generate_seed(client)
    with app_state(client).db.transaction() as conn:
        seed = conn.execute("SELECT * FROM seeds WHERE id = ?", (seed_id,)).fetchone()
        holes = conn.execute(
            "SELECT count(*) FROM seed_holes WHERE seed_id = ?", (seed_id,)
        ).fetchone()[0]
    assert seed["unfinished_ips"] == IPS
    assert holes == 18
    stored = Manifest.from_json(__import__("json").loads(seed["manifest"]))
    assert stored == fake_builder.built
    assert stored.settings.mercy_point == DEFAULT_MERCY_POINT


def test_a_refused_form_comes_back_with_its_values_and_a_notice(unwritten_client):
    form = FormState.default()
    form.mode = "natural_par"
    form.sources = set()
    form.banned = {"2W"}
    response = post_generate(unwritten_client, form)
    assert response.status_code == 400
    page = response.text
    assert 'role="alert"' in page and "generate.error.no_sources" in page
    assert re.search(r'<option value="natural_par"\s+selected', page)
    assert re.search(r'name="banned"\s+value="2W"\s+checked', page)
    assert seed_count(unwritten_client) == 0


def test_a_club_rule_refusal_names_its_values(unwritten_client):
    form = FormState.default()
    form.clubs_max = "2"
    form.required_bag = {"1W", "PW"}
    response = post_generate(unwritten_client, form)
    assert response.status_code == 400
    assert "generate.error.required_bag_over_max count=3 max=2" in response.text
    assert re.search(r"<details\s+open>", response.text)


def test_club_rules_start_collapsed_and_the_family_toggle_is_offered(client):
    page = client.get("/generate").text
    assert re.search(r"<details\s*>", page)
    toggle = page.index('name="allow_family_repeats"')
    # the toggle's own fieldset is the last one opened before it, and is not hidden
    assert page.rindex("<fieldset>", 0, toggle) > page.rindex("</fieldset>", 0, toggle)
    assert "<fieldset hidden>" not in page


def test_a_pool_that_cannot_fill_the_course_is_refused(catalog, curation, tmp_path):
    with app_client(
        strings=UNWRITTEN,
        builder=PoolTooSmall(catalog, curation, HoleStore(), tmp_path / "x.nes"),
    ) as test_client:
        response = post_generate(test_client)
        assert response.status_code == 400
        assert "generate.error.pool" in response.text
        assert seed_count(test_client) == 0


def test_generating_is_rate_limited_per_client(fake_builder):
    with app_client(
        strings=UNWRITTEN, builder=fake_builder, rate_limiter=RateLimiter(1, 3600)
    ) as test_client:
        assert (
            post_generate(
                test_client, headers={"X-Forwarded-For": "192.0.2.1"}
            ).status_code
            == 303
        )
        refused = post_generate(test_client, headers={"X-Forwarded-For": "192.0.2.1"})
        assert refused.status_code == 429
        assert "generate.error.rate_limited" in refused.text
        assert (
            post_generate(
                test_client, headers={"X-Forwarded-For": "192.0.2.2"}
            ).status_code
            == 303
        )
        assert seed_count(test_client) == 2


def test_a_refused_form_spends_no_token(fake_builder):
    invalid = FormState.default()
    invalid.sources = set()
    with app_client(
        builder=fake_builder, rate_limiter=RateLimiter(1, 3600)
    ) as test_client:
        assert post_generate(test_client, invalid).status_code == 400
        assert post_generate(test_client).status_code == 303


def test_generating_without_the_servers_rom_is_unavailable(
    catalog, curation, tmp_path, caplog
):
    missing = SeedBuilder(catalog, curation, HoleStore(), tmp_path / "missing.nes")
    with app_client(strings=UNWRITTEN, builder=missing) as test_client:
        assert "cannot build seeds until this is fixed" in caplog.text
        response = post_generate(test_client)
        assert response.status_code == 503
        assert "generate.error.unavailable" in response.text
        assert seed_count(test_client) == 0


def test_a_seed_records_the_player_who_generated_it(fake_builder):
    with dev_client(fake_builder) as test_client:
        guest_seed = generate_seed(test_client)
        test_client.get("/auth/login", params={"as": "alice"})
        signed_in_seed = generate_seed(test_client)
        [alice] = users(test_client)
        with app_state(test_client).db.transaction() as conn:
            creators = dict(conn.execute("SELECT id, creator_id FROM seeds").fetchall())
    assert creators == {guest_seed: None, signed_in_seed: alice["id"]}


def test_signed_in_players_are_rate_limited_per_user(fake_builder):
    with dev_client(fake_builder, rate_limiter=RateLimiter(1, 3600)) as test_client:
        same_address = {"X-Forwarded-For": "192.0.2.1"}
        test_client.get("/auth/login", params={"as": "alice"})
        assert post_generate(test_client, headers=same_address).status_code == 303
        assert post_generate(test_client, headers=same_address).status_code == 429
        test_client.get("/auth/login", params={"as": "bob"})
        assert post_generate(test_client, headers=same_address).status_code == 303
        test_client.post("/auth/logout")
        assert post_generate(test_client, headers=same_address).status_code == 303
        assert seed_count(test_client) == 3


def test_a_missing_server_rom_is_logged_when_generating(
    catalog, curation, tmp_path, caplog
):
    """Every generate answers 503 and the site looks healthy; only the log says why."""
    missing = SeedBuilder(catalog, curation, HoleStore(), tmp_path / "missing.nes")
    with (
        app_client(strings=UNWRITTEN, builder=missing) as test_client,
        caplog.at_level(logging.ERROR, logger="server.routes.seed_pages"),
    ):
        post_generate(test_client)
    assert "missing.nes" in caplog.text


def test_a_pool_that_cannot_fill_is_logged_with_its_settings(
    catalog, curation, tmp_path, caplog
):
    """Which settings could not be filled is the curation signal the refusal throws away."""
    builder = PoolTooSmall(catalog, curation, HoleStore(), tmp_path / "x.nes")
    with (
        app_client(strings=UNWRITTEN, builder=builder) as test_client,
        caplog.at_level(logging.WARNING, logger="server.routes.seed_pages"),
    ):
        post_generate(test_client)
    (found,) = [r for r in caplog.records if "no pool" in r.getMessage()]
    assert found.levelno == logging.WARNING
    assert hasattr(found, "settings")


# -- The mystery page -------------------------------------------------------------------------


@pytest.fixture
def mystery_client(fake_builder):
    with app_client(mystery_seeds=True, builder=fake_builder) as test_client:
        yield test_client


@pytest.fixture
def mystery_unwritten_client(fake_builder):
    with app_client(
        mystery_seeds=True, strings=UNWRITTEN, builder=fake_builder
    ) as test_client:
        yield test_client


def test_the_mystery_page_is_off_unless_configured(client):
    assert 'href="/mystery"' not in client.get("/generate").text
    assert client.get("/mystery").status_code == 404
    assert post_request(client, b"{}").status_code == 404
    assert seed_count(client) == 0


def test_the_generate_page_links_to_the_mystery_page_when_it_is_on(mystery_client):
    assert 'href="/mystery"' in mystery_client.get("/generate").text


def post_request(client: TestClient, content: bytes | None, **kwargs):
    files = None if content is None else {"request": ("mine.json", content)}
    return client.post("/mystery", files=files, follow_redirects=False, **kwargs)


def test_the_mystery_page_takes_a_request_file(mystery_unwritten_client):
    page = mystery_unwritten_client.get("/mystery").text
    assert 'enctype="multipart/form-data"' in page
    assert re.search(
        r'<input type="file"\s+id="mystery-request"\s+name="request"', page
    )
    assert "mystery.file_help max_kb=16" in page


def test_a_mystery_request_is_resolved_and_recorded_as_given(
    mystery_client, fake_builder
):
    request = {
        "sets": [
            {"weight": 1, "settings": {"constraints": [{"rule": "par_3"}]}},
            {
                "weight": 1,
                "settings": {
                    "music": {
                        "one_of": [
                            {"weight": 1, "value": "random"},
                            {"weight": 1, "value": "nes_us"},
                        ]
                    }
                },
            },
        ]
    }
    response = post_request(mystery_client, json.dumps(request).encode())
    assert response.status_code == 303
    seed_id = response.headers["location"].removeprefix("/h/")
    stored = mystery_client.get(f"/h/{seed_id}.json").json()
    assert stored["request"] == request
    assert stored == fake_builder.built.to_json()
    assert fake_builder.built.settings.prng_seed


@pytest.mark.parametrize(
    "content, key",
    [
        (None, "mystery.error.no_file"),
        (b"{" * (16 * 1024 + 1), "mystery.error.too_large max_kb=16"),
        (b"{not json", "mystery.error.not_json"),
        (b"\xff\xfe", "mystery.error.not_json"),
        (b'{"set": []}', "mystery.error.bad_request problem=request: unknown key"),
        (b'{"music": "bogus"}', "mystery.error.bad_request problem=request: music"),
        (b'{"mercy_point": 5}', "mystery.error.bad_request problem=request: a request"),
    ],
)
def test_a_request_the_site_cannot_roll_is_refused(
    mystery_unwritten_client, content, key
):
    response = post_request(mystery_unwritten_client, content)
    assert response.status_code == 400
    assert 'role="alert"' in response.text and key in response.text
    assert seed_count(mystery_unwritten_client) == 0


def test_a_mystery_request_that_rolls_no_course_says_what_was_rolled(
    catalog, curation, tmp_path
):
    with app_client(
        mystery_seeds=True,
        strings=UNWRITTEN,
        builder=PoolTooSmall(catalog, curation, HoleStore(), tmp_path / "x.nes"),
    ) as test_client:
        response = post_request(test_client, b'{"mirror_chance": 25}')
        assert response.status_code == 400
        assert "mystery.error.pool" in response.text
        assert "rolled=" in response.text and "problem=no fill" in response.text
        assert seed_count(test_client) == 0


def test_mystery_seeds_share_the_generate_rate_limit(fake_builder):
    with app_client(
        mystery_seeds=True,
        strings=UNWRITTEN,
        builder=fake_builder,
        rate_limiter=RateLimiter(1, 3600),
    ) as test_client:
        assert post_request(test_client, b"{}").status_code == 303
        refused = post_request(test_client, b"{}")
        assert refused.status_code == 429
        assert "mystery.error.rate_limited" in refused.text
        assert post_request(test_client, b"{bad").status_code == 400
