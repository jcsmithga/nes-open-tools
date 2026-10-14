"""`server/routes/seed_pages.py`: the IPS download, its refusals and the entries it records."""

import logging

import pytest
from fastapi.testclient import TestClient

from golf.core.patches.extended_sram_defaults import BallSpin, SwingSpeed
from golf.core.patches.sram_defaults import Club
from golf.randomizer.catalog import JP_ROM, US_ROM, HoleStore
from golf.randomizer.roms import vanilla_rom
from server.builder import BuiltSeed, SeedBuilder
from tests.app_state import app_state
from tests.synthetic_holes import built_holes
from tests.unit.server_app.helpers import (
    FINISHED,
    IPS,
    UNWRITTEN,
    US_ONLY,
    app_client,
    dev_client,
    entered_seed,
    generate_seed,
    post_download,
    scan_path,
    seed_form,
    users,
)

US_HASHES = {f"rom_{US_ROM}": vanilla_rom(US_ROM).sha1}


def test_downloading_finishes_the_stored_seed_as_a_guest(client, fake_builder):
    seed_id = generate_seed(client)
    response = post_download(client, seed_id)
    assert response.status_code == 200
    assert response.content == FINISHED
    assert response.headers["content-type"] == "application/octet-stream"
    par = fake_builder.built.course.par
    assert (
        response.headers["content-disposition"]
        == f'attachment; filename="notgr_par{par}_{seed_id}.ips"'
    )
    manifest, unfinished_ips, options = fake_builder.finished
    assert manifest == fake_builder.built
    assert unfinished_ips == IPS
    assert options.player_name == "LUIGI"
    assert options.clubs == {Club.W1, Club.PW, Club.PT}
    assert fake_builder.credentials is None


def test_a_us_only_seed_needs_only_the_us_hash(client):
    seed_id = generate_seed(client, seed_form(**US_ONLY))
    assert post_download(client, seed_id, hashes=US_HASHES).status_code == 200


def test_a_seed_with_mario_open_content_is_refused_without_the_jp_hash(client):
    seed_id = generate_seed(client, seed_form(music="jp_france"))
    response = post_download(client, seed_id, hashes=US_HASHES)
    assert response.status_code == 403
    assert response.json() == {
        "error": "roms_missing",
        "values": {"roms": vanilla_rom(JP_ROM).title},
    }


def test_a_download_without_hashes_is_refused(client):
    seed_id = generate_seed(client, seed_form(**US_ONLY))
    response = post_download(client, seed_id, hashes={})
    assert response.status_code == 403
    assert response.json()["error"] == "roms_missing"


@pytest.mark.parametrize(
    "form, name, clubs, error",
    [
        ({}, "LU1GI", ("1W",), {"error": "invalid_name", "values": {"chars": "1"}}),
        ({}, "", ("1W",), {"error": "invalid_name", "values": {"chars": ""}}),
        ({}, "LUIGI", ("9W",), {"error": "invalid", "values": {"field": "clubs"}}),
        (
            {"banned": {"SW"}},
            "LUIGI",
            ("SW", "PW"),
            {"error": "clubs_banned", "values": {"clubs": "SW"}},
        ),
        (
            {"clubs_max": "2"},
            "LUIGI",
            ("1W", "PW"),
            {"error": "clubs_over_max", "values": {"count": 3, "max": 2}},
        ),
    ],
)
def test_a_download_the_seed_forbids_is_refused(client, form, name, clubs, error):
    seed_id = generate_seed(client, seed_form(**form))
    response = post_download(client, seed_id, name=name, clubs=clubs)
    assert response.status_code == 400
    assert response.json() == error


def test_downloading_an_unknown_seed_is_a_json_404(client):
    for seed_id in ("0000000001", "not-a-seed"):
        response = post_download(client, seed_id)
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}


def test_downloading_without_the_servers_rom_is_unavailable(
    catalog, curation, tmp_path
):
    class NoRom(SeedBuilder):
        def build(self, manifest, sample=None):
            return BuiltSeed(IPS, built_holes(manifest, self.catalog))

    with app_client(
        builder=NoRom(catalog, curation, HoleStore(), tmp_path / "missing.nes")
    ) as test_client:
        seed_id = generate_seed(test_client)
        response = post_download(test_client, seed_id)
        assert response.status_code == 503
        assert response.json() == {"error": "unavailable", "values": {}}


def entries(client: TestClient) -> list[dict]:
    with app_state(client).db.transaction() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM entries ORDER BY id")]


def qr_seed_id(client: TestClient, seed_id: str) -> int:
    with app_state(client).db.transaction() as conn:
        return conn.execute(
            "SELECT qr_seed_id FROM seeds WHERE id = ?", (seed_id,)
        ).fetchone()[0]


def test_a_signed_out_download_records_no_entry(fake_builder):
    with dev_client(fake_builder) as test_client:
        seed_id = generate_seed(test_client)
        assert post_download(test_client, seed_id).status_code == 200
        assert fake_builder.credentials is None
        assert entries(test_client) == []


def test_a_signed_in_download_enters_the_seed_and_finishes_with_credentials(
    fake_builder,
):
    with dev_client(fake_builder) as test_client:
        seed_id = generate_seed(test_client)
        test_client.get("/auth/login", params={"as": "alice"})
        response = post_download(test_client, seed_id, name="luigi", clubs=("1W", "PW"))
        assert response.status_code == 200
        assert response.content == FINISHED
        [alice] = users(test_client)
        [entry] = entries(test_client)
        expected_seed_id = qr_seed_id(test_client, seed_id)
    assert entry["seed_id"] == seed_id
    assert entry["user_id"] == alice["id"]
    assert entry["player_name"] == "LUIGI"
    assert entry["clubs"] == "1W PW PT"
    credentials = fake_builder.credentials
    assert credentials.seed_id == expected_seed_id.to_bytes(8, "big")
    assert credentials.player_ids == (alice["player_id"].to_bytes(4, "big"),) * 2
    assert credentials.keys == (entry["key_slot0"], entry["key_slot1"])
    assert entry["key_slot0"] != entry["key_slot1"]


def test_downloading_again_updates_the_entry_and_keeps_its_keys(fake_builder):
    with dev_client(fake_builder) as test_client:
        seed_id = generate_seed(test_client)
        test_client.get("/auth/login", params={"as": "alice"})
        post_download(test_client, seed_id, name="luigi", clubs=("1W", "PW"))
        first_keys = fake_builder.credentials.keys
        post_download(test_client, seed_id, name="toad", clubs=("3W", "SW"))
        [entry] = entries(test_client)
    assert fake_builder.credentials.keys == first_keys
    assert (entry["player_name"], entry["clubs"]) == ("TOAD", "3W SW PT")


def test_players_entering_the_same_seed_get_their_own_entries_and_keys(fake_builder):
    with dev_client(fake_builder) as test_client:
        seed_id = generate_seed(test_client)
        test_client.get("/auth/login", params={"as": "alice"})
        post_download(test_client, seed_id)
        alice_credentials = fake_builder.credentials
        test_client.get("/auth/login", params={"as": "bob"})
        post_download(test_client, seed_id)
        bob_credentials = fake_builder.credentials
        assert len(entries(test_client)) == 2
    assert alice_credentials.seed_id == bob_credentials.seed_id
    assert alice_credentials.player_ids != bob_credentials.player_ids
    assert set(alice_credentials.keys).isdisjoint(bob_credentials.keys)


@pytest.mark.parametrize("change", [{"name": "LU1GI"}, {"hashes": {}}])
def test_a_refused_download_records_no_entry(fake_builder, change):
    with dev_client(fake_builder) as test_client:
        seed_id = generate_seed(test_client)
        test_client.get("/auth/login", params={"as": "alice"})
        assert post_download(test_client, seed_id, **change).status_code in (400, 403)
        assert entries(test_client) == []


def test_downloading_after_a_round_finishes_with_the_new_choices_and_leaves_the_entry(
    fake_builder,
):
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        first_keys = fake_builder.credentials.keys
        test_client.get(scan_path(test_client, seed_id, "alice"))
        before = entries(test_client)
        response = post_download(test_client, seed_id, name="toad", clubs=("3W", "SW"))
        after = entries(test_client)
    assert response.status_code == 200
    _, _, options = fake_builder.finished
    assert options.player_name == "TOAD"
    assert options.clubs == frozenset({Club.W3, Club.SW, Club.PT})
    assert fake_builder.credentials.keys == first_keys
    assert after == before


def test_a_missing_server_rom_is_logged_when_a_download_finishes(
    catalog, curation, tmp_path, caplog
):
    class NoRom(SeedBuilder):
        def build(self, manifest, sample=None):
            return BuiltSeed(IPS, built_holes(manifest, self.catalog))

    builder = NoRom(catalog, curation, HoleStore(), tmp_path / "missing.nes")
    with app_client(strings=UNWRITTEN, builder=builder) as test_client:
        seed_id = generate_seed(test_client)
        with caplog.at_level(logging.ERROR, logger="server.routes.seed_pages"):
            post_download(test_client, seed_id)
    assert "missing.nes" in caplog.text


def test_a_download_carries_the_four_options(client, fake_builder):
    seed_id = generate_seed(client)
    extra = {"bgm": ["off"], "swing": "fast", "putt": "slow", "spin": "back2"}
    assert post_download(client, seed_id, extra=extra).status_code == 200
    _, _, options = fake_builder.finished
    assert options.bgm is False
    assert (options.swing, options.putt, options.spin) == (
        SwingSpeed.FAST,
        SwingSpeed.SLOW,
        BallSpin.BACK2,
    )


def test_a_download_with_the_music_switch_on_keeps_music(client, fake_builder):
    seed_id = generate_seed(client)
    post_download(client, seed_id, extra={"bgm": ["off", "on"]})
    assert fake_builder.finished[2].bgm is True
    post_download(client, seed_id)
    assert fake_builder.finished[2].bgm is True


@pytest.mark.parametrize(
    "field, value", [("swing", "warp"), ("putt", "back1"), ("spin", "fast")]
)
def test_an_option_the_game_does_not_have_is_refused(client, field, value):
    seed_id = generate_seed(client)
    response = post_download(client, seed_id, extra={field: value})
    assert response.status_code == 400
    assert response.json() == {"error": "invalid", "values": {"field": field}}
