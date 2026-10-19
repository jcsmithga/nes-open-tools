"""The host pages: who they admit, creating a hosted ROM, its page, and its download."""

import re

import pytest
from fastapi.testclient import TestClient

from golf.core.patches.sram_defaults import VANILLA_NAME, Club
from golf.randomizer.build import credentials_for, credentials_for_slots
from golf.randomizer.catalog import Catalog, HoleStore
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.manifest import ClubRules
from server import hosting
from server.app import create_app
from server.config import Config
from server.entries import load_entry
from server.routes.common import DOWNLOAD_COOKIE
from server.routes.host_pages import club_warning, seed_id_from
from server.seeds import load_seed, withdraw_seed
from tests.app_state import app_state
from tests.unit.server_app.helpers import (
    ALL_HASHES,
    FINISHED,
    FakeBuilder,
    generate_seed,
    seed_form,
)

HOSTED_URL = re.compile(r"^/host/([0-9A-Za-z]{10})$")


@pytest.fixture
def fake_builder(tmp_path):
    return FakeBuilder(
        Catalog.load(), CurationSnapshot.load(), HoleStore(), tmp_path / "unused.nes"
    )


@pytest.fixture
def client(fake_builder):
    config = Config(
        database=":memory:",
        dev_login=True,
        admin_users=frozenset({"dev:admin"}),
        host_users=frozenset({"dev:host", "dev:other"}),
    )
    with TestClient(create_app(config, builder=fake_builder)) as test_client:
        yield test_client


def sign_in(client: TestClient, name: str) -> None:
    client.post("/auth/logout", data={"next": "/"})
    client.get("/auth/login", params={"as": name})


def post(client: TestClient, path: str, **data):
    return client.post(path, data=data, follow_redirects=False)


def user(client: TestClient, name: str):
    """A dev user, signed in once so they exist, as their `users` row."""
    sign_in(client, name)
    with app_state(client).db.transaction() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (name,)
        ).fetchone()


def host_seed(client: TestClient, seed_id: str) -> str:
    """Host a seed as the signed-in host, and the hosted ROM's public id."""
    response = post(client, "/host", seed=seed_id)
    assert response.status_code == 303, response.text
    match = HOSTED_URL.match(response.headers["location"])
    assert match is not None, response.headers["location"]
    return match.group(1)


def hosted_rom(client: TestClient, public_id: str) -> hosting.HostedRom:
    loaded = hosting.load_hosted_rom(app_state(client).db, public_id)
    assert loaded is not None
    return loaded


def claim(client: TestClient, public_id: str, *names: str) -> None:
    """Each named player claims the hosted ROM, in order; signed in as host afterwards."""
    token = hosted_rom(client, public_id).token
    for name in names:
        hosting.claim_slot(app_state(client).db, token, user(client, name)["id"])
    sign_in(client, "host")


def download(client: TestClient, public_id: str, hashes=None):
    return client.post(
        f"/host/{public_id}/patch.ips",
        data=ALL_HASHES if hashes is None else hashes,
    )


@pytest.fixture
def seed_id(client):
    return generate_seed(client)


@pytest.fixture
def hosted(client, seed_id):
    """A hosted ROM of the seed, created by host, who stays signed in."""
    sign_in(client, "host")
    return host_seed(client, seed_id)


# -- Access -------------------------------------------------------------------------------


@pytest.mark.parametrize("who", [None, "alice"])
def test_host_pages_are_not_found_for_anyone_else(client, seed_id, hosted, who):
    client.post("/auth/logout", data={"next": "/"})
    if who:
        sign_in(client, who)
    for method, path in (
        ("get", "/host"),
        ("post", "/host"),
        ("get", f"/host/{hosted}"),
        ("post", f"/host/{hosted}/name"),
        ("post", f"/host/{hosted}/slots/0/release"),
        ("post", f"/host/{hosted}/patch.ips"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 404, (method, path)


def test_a_host_who_is_not_an_admin_sees_no_admin_page(client, hosted):
    assert client.get("/host").status_code == 200
    assert client.get("/admin").status_code == 404


def test_an_admin_is_a_host(client, seed_id):
    sign_in(client, "admin")
    assert client.get("/host").status_code == 200
    host_seed(client, seed_id)


def test_a_host_cannot_open_another_hosts_hosted_rom_but_an_admin_can(client, hosted):
    sign_in(client, "other")
    assert client.get(f"/host/{hosted}").status_code == 404
    assert post(client, f"/host/{hosted}/slots/0/release").status_code == 404
    assert download(client, hosted).status_code == 404
    assert hosted not in client.get("/host").text
    sign_in(client, "admin")
    assert client.get(f"/host/{hosted}").status_code == 200


# -- Creating -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("4np03ChsZL", "4np03ChsZL"),
        ("  4np03ChsZL ", "4np03ChsZL"),
        ("https://example.com/h/4np03ChsZL", "4np03ChsZL"),
        ("https://example.com/h/4np03ChsZL/book", "4np03ChsZL"),
        ("/h/4np03ChsZL?x=1", "4np03ChsZL"),
        ("", ""),
    ],
)
def test_a_seed_link_or_id_names_the_seed(text, expected):
    assert seed_id_from(text) == expected


def test_hosting_from_a_seed_link_opens_its_hosting_page(client, seed_id):
    sign_in(client, "host")
    public_id = host_seed(client, seed_id=f"http://testserver/h/{seed_id}")
    loaded = hosted_rom(client, public_id)
    assert loaded.seed_id == seed_id
    page = client.get(f"/host/{public_id}")
    assert page.status_code == 200
    listing = client.get("/host").text
    assert f'href="/host/{public_id}"' in listing
    assert 'data-status="open"' in listing


def test_hosting_an_unknown_or_withdrawn_seed_goes_back_saying_so(client, seed_id):
    host = user(client, "host")
    assert post(client, "/host", seed="nonsense").headers["location"] == (
        "/host?result=unknown_seed"
    )
    assert post(client, "/host", seed="0000000000").headers["location"] == (
        "/host?result=unknown_seed"
    )
    withdraw_seed(app_state(client).db, seed_id, host["id"])
    assert post(client, "/host", seed=seed_id).headers["location"] == (
        "/host?result=withdrawn_seed"
    )


def test_host_this_seed_again_makes_the_next_hosted_rom(client, seed_id, hosted):
    page = client.get(f"/host/{hosted}").text
    assert f'<input type="hidden" name="seed" value="{seed_id}">' in page
    again = host_seed(client, seed_id)
    assert again != hosted
    assert hosted_rom(client, again).seed_id == seed_id


def test_host_lists_recent_seeds_to_host_with_one_click(client, seed_id):
    """Every user's seeds, not only the host's own: this one was generated signed out."""
    sign_in(client, "host")
    page = client.get("/host").text
    assert f'data-recent-seed="{seed_id}"' in page
    assert f'<input type="hidden" name="seed" value="{seed_id}">' in page
    assert hosted_rom(client, host_seed(client, seed_id)).seed_id == seed_id


# -- The hosting page ---------------------------------------------------------------------


def test_the_claim_code_shows_while_a_slot_is_free(client, hosted):
    token = hosted_rom(client, hosted).token
    page = client.get(f"/host/{hosted}").text
    assert "data-claim-code" in page
    assert f"{Config().base_url}/claim/{token}" in page
    assert "<svg" in page
    claim(client, hosted, "alice")
    page = client.get(f"/host/{hosted}").text
    assert "data-claim-code" in page
    assert re.search(r'data-slot="0"\s+data-claimed="true"', page)
    assert "Alice" in page or "alice" in page
    claim(client, hosted, "bob")
    assert "data-claim-code" not in client.get(f"/host/{hosted}").text


def test_a_download_hides_the_code_until_a_release(client, hosted):
    claim(client, hosted, "alice")
    assert download(client, hosted).status_code == 200
    page = client.get(f"/host/{hosted}").text
    assert "data-claim-code" not in page
    assert "data-code-hidden" in page
    assert 'data-status="downloaded"' in client.get("/host").text
    response = post(client, f"/host/{hosted}/slots/1/release")
    assert response.headers["location"] == f"/host/{hosted}?result=released"
    assert "data-claim-code" in client.get(f"/host/{hosted}").text


def test_releasing_a_slot_frees_it(client, hosted):
    claim(client, hosted, "alice", "bob")
    post(client, f"/host/{hosted}/slots/0/release")
    assert hosted_rom(client, hosted).slots[0].user_id is None
    assert post(client, f"/host/{hosted}/slots/2/release").status_code == 404


def test_the_name_starts_from_the_defaults_and_is_saved(client, hosted):
    page = client.get(f"/host/{hosted}").text
    assert f'name="player_name"\n             value="{VANILLA_NAME}"' in page
    response = post(client, f"/host/{hosted}/name", player_name="big al")
    assert response.headers["location"] == f"/host/{hosted}?result=name_saved"
    options = hosted_rom(client, hosted).options
    assert options is not None and options.player_name == "BIG AL"
    claim(client, hosted, "alice")
    page = client.get(f"/host/{hosted}").text
    assert re.search(
        rf'data-filename="BIG_AL_{hosted}_notgr_par\d+_[0-9A-Za-z]{{10}}\.nes"', page
    )


@pytest.mark.parametrize("name", ["", "A" * 11, "ALICE!"])
def test_a_name_the_game_cannot_show_is_not_saved(client, hosted, name):
    response = post(client, f"/host/{hosted}/name", player_name=name)
    assert response.headers["location"] == f"/host/{hosted}?result=invalid_name"
    assert hosted_rom(client, hosted).options is None


def test_the_hosting_page_holds_no_saved_settings(client, hosted):
    """The name form starts from the defaults, not the host's saved settings."""
    saved = client.post(
        "/me/download-settings",
        data={"player_name": "hosty", "clubs": ["1W"], "bgm": "on"},
        follow_redirects=False,
    )
    assert saved.status_code == 303, saved.text
    with app_state(client).db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM download_settings").fetchone()[0] == 1
    assert f'value="{VANILLA_NAME}"' in client.get(f"/host/{hosted}").text


def test_an_expired_hosted_rom_says_so_and_refuses_downloads(client, seed_id):
    host = user(client, "host")
    db = app_state(client).db
    expired = hosting.create_hosted_rom(
        db, seed_id, host["id"], now="2020-01-01T00:00:00Z"
    )
    hosting.claim_slot(db, expired.token, host["id"], now="2020-01-01T00:00:01Z")
    page = client.get(f"/host/{expired.public_id}").text
    assert "has expired" in page
    assert "data-claim-code" not in page
    response = download(client, expired.public_id)
    assert response.status_code == 410
    assert response.json()["error"] == "expired"


# -- Club rules ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rules, warned",
    [
        (ClubRules(), False),
        (ClubRules(required_bag=frozenset({Club.W1, Club.I7})), False),
        (ClubRules(max=10, required_bag=frozenset({Club.W1})), False),
        (ClubRules(max=10), True),
        (ClubRules(banned=frozenset({Club.W1})), True),
    ],
)
def test_the_club_warning_is_for_rules_without_a_locked_bag(rules, warned):
    assert club_warning(rules) == warned


def test_the_hosting_page_warns_of_club_rules_without_a_locked_bag(client):
    sign_in(client, "host")
    plain = host_seed(client, generate_seed(client))
    assert "data-club-warning" not in client.get(f"/host/{plain}").text
    banned = host_seed(client, generate_seed(client, seed_form(banned={"1W"})))
    assert "data-club-warning" in client.get(f"/host/{banned}").text
    locked = host_seed(
        client, generate_seed(client, seed_form(required_bag={"1W", "7I"}))
    )
    assert "data-club-warning" not in client.get(f"/host/{locked}").text


def test_a_locked_bag_is_what_a_hosted_rom_carries(client, fake_builder):
    sign_in(client, "host")
    hosted = host_seed(
        client, generate_seed(client, seed_form(required_bag={"1W", "7I"}))
    )
    claim(client, hosted, "alice")
    assert download(client, hosted).status_code == 200
    _manifest, _ips, options = fake_builder.finished
    assert options.clubs == {Club.W1, Club.I7, Club.PT}


def test_a_hosted_bag_over_a_club_max_is_refused(client):
    sign_in(client, "host")
    hosted = host_seed(client, generate_seed(client, seed_form(clubs_max="10")))
    claim(client, hosted, "alice")
    response = download(client, hosted)
    assert response.status_code == 400
    assert response.json()["error"] == "clubs_over_max"


# -- The download -------------------------------------------------------------------------


def test_two_claimants_each_sign_with_their_own_slot_zero_key(
    client, fake_builder, seed_id, hosted
):
    alice, bob = user(client, "alice"), user(client, "bob")
    claim(client, hosted, "alice", "bob")
    post(client, f"/host/{hosted}/name", player_name="alice")
    response = download(client, hosted)
    assert response.status_code == 200
    assert response.content == FINISHED
    assert response.headers["content-disposition"] == (
        f'attachment; filename="ALICE_{hosted}_notgr_par'
        f'{hosted_seed_par(client, seed_id)}_{seed_id}.ips"'
    )
    db = app_state(client).db
    alice_entry = load_entry(db, seed_id, alice["id"])
    bob_entry = load_entry(db, seed_id, bob["id"])
    assert alice_entry is not None and bob_entry is not None
    assert (alice_entry.player_name, bob_entry.player_name) == ("ALICE", "ALICE")
    qr_seed_id = hosted_rom_seed_qr(client, seed_id)
    assert fake_builder.credentials == credentials_for_slots(
        qr_seed_id,
        (alice["player_id"], alice_entry.keys[0]),
        (bob["player_id"], bob_entry.keys[0]),
    )
    assert hosted_rom(client, hosted).downloaded_at is not None


@pytest.mark.parametrize("released", [None, 0])
def test_one_claimant_gets_the_rom_they_would_download_themselves(
    client, fake_builder, seed_id, hosted, released
):
    """Alone in either slot, the claimant is in both positions with both their keys."""
    claim(client, hosted, "alice", "bob")
    post(client, f"/host/{hosted}/slots/{1 if released is None else 0}/release")
    left = "alice" if released is None else "bob"
    player = user(client, left)
    sign_in(client, "host")
    assert download(client, hosted).status_code == 200
    entry = load_entry(app_state(client).db, seed_id, player["id"])
    assert entry is not None
    assert fake_builder.credentials == credentials_for(
        hosted_rom_seed_qr(client, seed_id), player["player_id"], entry.keys
    )


def test_a_download_needs_a_claim(client, hosted):
    assert "data-filename" not in client.get(f"/host/{hosted}").text
    response = download(client, hosted)
    assert response.status_code == 409
    assert response.json()["error"] == "no_claims"


def test_a_download_needs_the_seeds_roms(client, hosted):
    claim(client, hosted, "alice")
    response = download(client, hosted, hashes={})
    assert response.status_code == 403
    assert response.json()["error"] == "roms_missing"


def test_a_withdrawn_seed_refuses_the_download(client, seed_id, hosted):
    claim(client, hosted, "alice")
    withdraw_seed(app_state(client).db, seed_id, user(client, "admin")["id"])
    sign_in(client, "host")
    response = download(client, hosted)
    assert response.status_code == 410
    assert response.json()["error"] == "seed_withdrawn"


def test_hosting_never_changes_anyones_saved_settings(client, hosted):
    claim(client, hosted, "alice", "bob")
    response = download(client, hosted)
    assert response.status_code == 200
    assert DOWNLOAD_COOKIE not in response.headers.get("set-cookie", "")
    with app_state(client).db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM download_settings").fetchone()[0] == 0


def test_the_audit_log_links_the_hosting_actions(client, hosted):
    claim(client, hosted, "alice")
    download(client, hosted)
    post(client, f"/host/{hosted}/slots/0/release")
    sign_in(client, "admin")
    activity = client.get("/admin/activity").text
    for label in ("hosted ROM downloaded", "slot released", "hosted"):
        assert label in activity
    assert f'href="/host/{hosted}"' in activity


def hosted_seed_par(client: TestClient, seed_id: str) -> int:
    row = load_seed(app_state(client).db, seed_id)
    assert row is not None
    return row.manifest.course.par


def hosted_rom_seed_qr(client: TestClient, seed_id: str) -> int:
    with app_state(client).db.transaction() as conn:
        return conn.execute(
            "SELECT qr_seed_id FROM seeds WHERE id = ?", (seed_id,)
        ).fetchone()["qr_seed_id"]
