"""Integration: the QR code a signed-in download's ROM draws records its round on the site.

Downloads a finished ROM from the app, pulls the scorecard QR image back out of it, runs the
6502 routine under the simulator to build the URL the code carries, and opens that URL. A
hosted ROM, two players' credentials in one ROM, is finished from the seed's stored IPS.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from golf.core import ips
from golf.core.patches import SCORECARD_QR_PATCH
from golf.qr.payload import URL_LEN, URL_PREFIX, pack_stats
from golf.qr.port import layout
from golf.qr.port.sim import Machine
from golf.randomizer.build import PlayerOptions, credentials_for_slots, finish
from golf.randomizer.catalog import US_ROM
from golf.randomizer.manifest import Manifest
from golf.randomizer.roms import vanilla_rom
from server.app import create_app
from server.config import Config
from server.entries import load_entry
from server.forms import FormState
from tests.app_state import app_state

ROOT = Path(__file__).resolve().parents[2]
ROM_PATH = ROOT / "nes_open_us.nes"
HEADER = 0x10

pytestmark = pytest.mark.skipif(
    not ROM_PATH.exists(), reason=f"{ROM_PATH.name} not present"
)

#: (strokes, putts) for holes 1-18, per player slot
ROUNDS = {
    0: [(4, 2)] * 9 + [(5, 2)] * 9,
    1: [(3, 1)] * 18,
}
#: (fairway bits, penalty strokes) per player slot, as round_stats keeps them
STATS = {
    0: (tuple(hole % 2 == 0 for hole in range(18)), 2),
    1: ((False,) * 18, 0),
}


def rom_url(rom: bytes, slot: int) -> str:
    """The URL the ROM's QR screen shows for `slot` after ROUNDS has been played."""
    image = rom[HEADER + SCORECARD_QR_PATCH.image_offset :][
        : len(SCORECARD_QR_PATCH.image)
    ]
    machine = Machine()
    machine.write(layout.TABLE_ORIGIN, image)
    for player, holes in ROUNDS.items():
        machine.set_round(
            holes, player=player, player_count=1, stats=pack_stats(*STATS[player])
        )
    machine.call("QrBuildPayload", a=slot)
    machine.call("QrBuildUrl")
    return machine.read(layout.URL, URL_LEN).decode("ascii")


@pytest.fixture
def client(vanilla_courses):
    with TestClient(
        create_app(
            Config(
                database=":memory:",
                rom_dir=ROOT,
                holes_dir=vanilla_courses,
                dev_login=True,
            )
        )
    ) as test_client:
        yield test_client


def generate(client: TestClient) -> str:
    form = FormState.default()
    form.sources, form.music = {US_ROM}, "nes_us"
    data: dict[str, list[str]] = {}
    for name, value in form.to_pairs():
        data.setdefault(name, []).append(value)
    generated = client.post("/generate", data=data, follow_redirects=False)
    assert generated.status_code == 303, generated.text
    return generated.headers["location"]


def download(client: TestClient, seed_path: str, player: str) -> bytes:
    """The IPS `player` downloads signed in, which enters them in the seed."""
    client.get("/auth/login", params={"as": player})
    response = client.post(
        f"{seed_path}/patch.ips",
        data={
            "player_name": "toad",
            "clubs": ["1W", "PW"],
            f"rom_{US_ROM}": vanilla_rom(US_ROM).sha1,
        },
    )
    assert response.status_code == 200, response.text
    client.post("/auth/logout", data={"next": "/"})
    return response.content


def submit(client: TestClient, rom: bytes, slot: int) -> None:
    url = rom_url(rom, slot)
    assert url.startswith(URL_PREFIX)
    # the ROM's own URL submits and hands the phone the round's permalink
    submitted = client.get("/s/" + url.removeprefix(URL_PREFIX), follow_redirects=False)
    assert submitted.status_code == 303, submitted.text
    permalink = submitted.headers["location"]
    assert permalink.endswith("?recorded")
    response = client.get(permalink)
    assert response.status_code == 200, response.text


def test_a_downloaded_roms_codes_record_both_players_rounds(client):
    seed_path = generate(client)
    rom = ips.apply(ROM_PATH.read_bytes(), download(client, seed_path, "alice"))
    for slot in ROUNDS:
        submit(client, rom, slot)

    with app_state(client).db.transaction() as conn:
        recorded = conn.execute(
            """
            SELECT slot, total_strokes, total_putts, penalty_strokes FROM rounds
            ORDER BY slot
            """
        ).fetchall()
        fairways = conn.execute(
            """
            SELECT rounds.slot, round_holes.fairway_hit FROM round_holes
            JOIN rounds ON rounds.id = round_holes.round_id
            ORDER BY rounds.slot, round_holes.position
            """
        ).fetchall()
    assert [tuple(row) for row in recorded] == [
        (slot, sum(s for s, _ in holes), sum(p for _, p in holes), STATS[slot][1])
        for slot, holes in ROUNDS.items()
    ]
    for slot in ROUNDS:
        assert (
            tuple(bool(row["fairway_hit"]) for row in fairways if row["slot"] == slot)
            == STATS[slot][0]
        )


def test_a_hosted_roms_codes_record_each_players_own_round(client):
    """Alice plays player 1 and Bob player 2, each signing with their own slot 0 key."""
    seed_path = generate(client)
    seed_id = seed_path.removeprefix("/h/")
    for player in ("alice", "bob"):
        download(client, seed_path, player)

    db = app_state(client).db
    with db.transaction() as conn:
        seed = conn.execute(
            "SELECT manifest, qr_seed_id, unfinished_ips FROM seeds WHERE id = ?",
            (seed_id,),
        ).fetchone()
        users = {
            row["discord_id"]: row
            for row in conn.execute("SELECT id, discord_id, player_id FROM users")
        }
    alice, bob = users["dev:alice"], users["dev:bob"]
    alice_entry = load_entry(db, seed_id, alice["id"])
    bob_entry = load_entry(db, seed_id, bob["id"])
    assert alice_entry is not None and bob_entry is not None
    credentials = credentials_for_slots(
        seed["qr_seed_id"],
        (alice["player_id"], alice_entry.keys[0]),
        (bob["player_id"], bob_entry.keys[0]),
    )
    rom = finish(
        Manifest.from_json(json.loads(seed["manifest"])),
        ROM_PATH.read_bytes(),
        seed["unfinished_ips"],
        PlayerOptions("TOAD", frozenset()),
        credentials,
    ).rom
    for slot in ROUNDS:
        submit(client, rom, slot)

    with db.transaction() as conn:
        recorded = conn.execute(
            "SELECT entry_id, slot, total_strokes, total_putts FROM rounds ORDER BY id"
        ).fetchall()
    assert [tuple(row) for row in recorded] == [
        (entry.id, 0, sum(s for s, _ in holes), sum(p for _, p in holes))
        for entry, holes in ((alice_entry, ROUNDS[0]), (bob_entry, ROUNDS[1]))
    ]
