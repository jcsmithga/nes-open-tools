"""`server/routes/claim_pages.py`: a player claiming a slot of a hosted ROM from their phone."""

import re

import pytest
from fastapi.testclient import TestClient

from server import hosting
from server.seeds import withdraw_seed
from server.users import sign_in as sign_in_user
from tests.app_state import app_state
from tests.unit.server_app.helpers import UNWRITTEN, dev_client, generate_seed


@pytest.fixture
def client(fake_builder):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        yield test_client


@pytest.fixture
def hosted(client) -> hosting.HostedRom:
    db = app_state(client).db
    host = sign_in_user(db, "dev:host", "host", None, None)
    return hosting.create_hosted_rom(db, generate_seed(client), host.id)


def sign_in(client: TestClient, name: str) -> None:
    client.post("/auth/logout", data={"next": "/"})
    client.get("/auth/login", params={"as": name})


def state(client: TestClient, token: str) -> str:
    match = re.search(r'data-claim-state="(\w+)"', client.get(f"/claim/{token}").text)
    assert match is not None
    return match.group(1)


def claimants(client: TestClient, hosted: hosting.HostedRom):
    loaded = hosting.load_hosted_rom(app_state(client).db, hosted.public_id)
    assert loaded is not None
    return [slot.user_name for slot in loaded.slots]


def test_signed_out_the_page_links_to_sign_in_and_back(client, hosted):
    response = client.get(f"/claim/{hosted.token}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert 'data-claim-state="sign_in"' in response.text
    assert f'href="/auth/login?next=/claim/{hosted.token}"' in response.text
    back = client.get(
        "/auth/login",
        params={"as": "alice", "next": f"/claim/{hosted.token}"},
        follow_redirects=False,
    )
    assert back.headers["location"] == f"/claim/{hosted.token}"


def test_the_page_names_the_seed_and_host(client, hosted):
    page = client.get(f"/claim/{hosted.token}").text
    assert "claim.seed words=" in page
    assert "host=host" in page


def test_confirming_claims_player_one_then_player_two(client, hosted):
    sign_in(client, "alice")
    assert state(client, hosted.token) == "confirm"
    response = client.post(f"/claim/{hosted.token}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/claim/{hosted.token}"
    page = client.get(f"/claim/{hosted.token}").text
    assert 'data-claim-state="claimed"' in page
    assert 'data-claimed-slot="0"' in page

    sign_in(client, "bob")
    client.post(f"/claim/{hosted.token}")
    assert 'data-claimed-slot="1"' in client.get(f"/claim/{hosted.token}").text
    assert claimants(client, hosted) == ["alice", "bob"]


def test_the_get_changes_nothing(client, hosted):
    sign_in(client, "alice")
    client.get(f"/claim/{hosted.token}")
    assert claimants(client, hosted) == [None, None]


def test_signed_out_a_post_claims_nothing(client, hosted):
    client.post(f"/claim/{hosted.token}")
    assert claimants(client, hosted) == [None, None]


def test_claiming_again_keeps_the_slot(client, hosted):
    sign_in(client, "alice")
    client.post(f"/claim/{hosted.token}")
    client.post(f"/claim/{hosted.token}")
    assert claimants(client, hosted) == ["alice", None]
    assert state(client, hosted.token) == "claimed"


def test_a_full_hosted_rom_says_so(client, hosted):
    for name in ("alice", "bob"):
        sign_in(client, name)
        client.post(f"/claim/{hosted.token}")
    sign_in(client, "carol")
    assert state(client, hosted.token) == "full"
    client.post(f"/claim/{hosted.token}")
    assert claimants(client, hosted) == ["alice", "bob"]
    client.post("/auth/logout", data={"next": "/"})
    assert state(client, hosted.token) == "full"


def test_an_expired_hosted_rom_says_so(client):
    db = app_state(client).db
    host = sign_in_user(db, "dev:host", "host", None, None)
    expired = hosting.create_hosted_rom(
        db, generate_seed(client), host.id, now="2020-01-01T00:00:00Z"
    )
    assert state(client, expired.token) == "expired"
    sign_in(client, "alice")
    assert state(client, expired.token) == "expired"
    client.post(f"/claim/{expired.token}")
    assert claimants(client, expired) == [None, None]


def test_a_withdrawn_seed_says_so(client, hosted):
    db = app_state(client).db
    withdraw_seed(db, hosted.seed_id, hosted.host_id)
    sign_in(client, "alice")
    assert state(client, hosted.token) == "withdrawn"


def test_an_unknown_token_is_not_found(client):
    response = client.get("/claim/not-a-real-token")
    assert response.status_code == 404
    assert 'data-claim-state="unknown"' in response.text


def test_a_released_player_can_claim_again(client, hosted):
    sign_in(client, "alice")
    client.post(f"/claim/{hosted.token}")
    hosting.release_slot(app_state(client).db, hosted.public_id, 0, hosted.host_id)
    assert state(client, hosted.token) == "confirm"
