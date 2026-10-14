"""Finish a frozen build 4 artifact and accept the v1 URLs its own code emits.

Only the vanilla ROM is needed. The unfinished IPS, manifest, catalog aliases,
routine addresses and RAM layout are frozen in tests/fixtures/build4; neither
historical course dumps nor a historical Git checkout are needed at test time.
"""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from golf.core import ips
from golf.core.asm6502 import Program
from golf.qr.payload import RoundPayload, base64url_decode
from golf.qr.port.sim import Machine
from golf.randomizer.catalog import US_ROM, Catalog, HoleStore
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.manifest import StoredManifest
from server.app import create_app
from server.builder import SeedBuilder
from server.config import Config
from tests.app_state import app_state
from tests.stored_seeds import store_issued

ROOT = Path(__file__).resolve().parents[2]
ROM_PATH = ROOT / "nes_open_us.nes"
FIXTURE = ROOT / "tests/fixtures/build4"

pytestmark = pytest.mark.skipif(
    not ROM_PATH.exists(), reason=f"{ROM_PATH.name} not present"
)

# Different scores make a swapped player ID, key or score array observable.
ROUNDS = {
    0: [(4, 2)] * 9 + [(5, 2)] * 9,
    1: [(3, 1)] * 18,
}


@pytest.fixture(scope="module")
def frozen():
    metadata = json.loads((FIXTURE / "fixture.json").read_text())
    patch = (FIXTURE / "unfinished.ips").read_bytes()
    assert hashlib.sha256(patch).hexdigest() == metadata["unfinished_ips_sha256"]
    assert hashlib.sha1(ROM_PATH.read_bytes()).hexdigest() == metadata["base_rom_sha1"]
    manifest = StoredManifest.from_json(metadata["manifest"])
    assert (manifest.build_version, manifest.finish_abi_version) == (4, 2)
    assert manifest.course.layout == (4,) * 18
    catalog = Catalog.from_json(metadata["catalog"])
    assert len(catalog) == 18
    source = next(iter(catalog))
    assert all(
        entry.source == source.source and entry.content_hash == source.content_hash
        for entry in catalog
    )
    return metadata, manifest, catalog, patch


@pytest.fixture
def stored_seed(frozen, tmp_path):
    metadata, manifest, catalog, patch = frozen
    # This real builder only finishes. No course files are available to rebuild
    # the artifact with the current unfinished recipe accidentally.
    builder = SeedBuilder(catalog, CurationSnapshot(), HoleStore(tmp_path), ROM_PATH)
    with TestClient(
        create_app(Config(database=":memory:", dev_login=True), builder=builder)
    ) as client:
        seed_id = store_issued(
            app_state(client).db, metadata["manifest"], patch, catalog
        )
        yield client, seed_id, metadata


def download(client: TestClient, seed_id: str, metadata: dict) -> bytes:
    response = client.post(
        f"/h/{seed_id}/patch.ips",
        data={
            "player_name": "toad",
            "clubs": ["1W", "PW"],
            f"rom_{US_ROM}": metadata["base_rom_sha1"],
        },
    )
    assert response.status_code == 200, response.text
    return ips.apply(ROM_PATH.read_bytes(), response.content)


def historical_url(rom: bytes, slot: int, qr: dict) -> str:
    """Run the downloaded bank using only the build 4 addresses and lengths."""
    start = qr["header_size"] + qr["bank"] * qr["bank_size"]
    bank = rom[start : start + qr["bank_size"]]
    machine = Machine(Program(qr["bank_origin"], bank, qr["symbols"]))
    # Use the historical game RAM layout too: Machine.set_round uses today's.
    for player, holes in ROUNDS.items():
        machine.write(
            qr["strokes_address"] + player * qr["strokes_stride"],
            bytes(strokes for strokes, _ in holes),
        )
        machine.write(
            qr["putts_address"] + player * qr["putts_stride"],
            bytes(putts for _, putts in holes),
        )
    machine.poke(qr["game_progress_address"], 18)
    machine.poke(qr["player_count_address"], 1)
    machine.poke(qr["game_mode_address"], 0)
    machine.call("QrBuildPayload", a=slot)
    machine.call("QrBuildUrl")
    return machine.read(qr["url_address"], qr["url_length"]).decode("ascii")


def test_build4_downloads_still_submit_both_players_v1_rounds(stored_seed):
    client, seed_id, metadata = stored_seed
    client.get("/auth/login", params={"as": "alice"})
    rom = download(client, seed_id, metadata)
    client.post("/auth/logout", data={"next": "/"})
    db = app_state(client).db
    qr = metadata["qr"]

    for slot, holes in ROUNDS.items():
        url = historical_url(rom, slot, qr)
        assert url.startswith(qr["url_prefix"])
        scan = url.removeprefix(qr["url_prefix"])
        assert len(scan) == 48
        sent, _mac = RoundPayload.from_bytes(base64url_decode(scan))
        assert sent.protocol_version == 1
        assert sent.player_slot == slot
        assert [(hole.strokes, hole.putts) for hole in sent.holes] == holes
        assert sent.fairways is None and sent.penalty_strokes is None

        response = client.get(f"/s/{scan}", follow_redirects=False)
        assert response.status_code == 303, response.text
        assert response.headers["cache-control"] == "no-store"
        permalink = response.headers["location"]
        assert permalink.endswith("?recorded")
        page = client.get(permalink)
        assert page.status_code == 200, page.text
        assert "with-fairways" not in page.text
        assert "data-penalty-strokes" not in page.text

        again = client.get(f"/s/{scan}", follow_redirects=False)
        assert again.status_code == 303
        assert again.headers["location"] == permalink.removesuffix("?recorded")

    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT slot, payload, total_strokes, total_putts, penalty_strokes "
            "FROM rounds ORDER BY slot"
        ).fetchall()
        recorded_holes = conn.execute(
            "SELECT rounds.slot, position, strokes, putts, fairway_hit "
            "FROM round_holes JOIN rounds ON rounds.id = round_holes.round_id "
            "ORDER BY rounds.slot, position"
        ).fetchall()
    assert len(rows) == 2
    for row in rows:
        holes = ROUNDS[row["slot"]]
        assert len(row["payload"]) == qr["payload_length"] == 36
        assert row["payload"][0] == 1
        assert row["total_strokes"] == sum(s for s, _ in holes)
        assert row["total_putts"] == sum(p for _, p in holes)
        assert row["penalty_strokes"] is None
    assert [tuple(row) for row in recorded_holes] == [
        (slot, position, strokes, putts, None)
        for slot, holes in ROUNDS.items()
        for position, (strokes, putts) in enumerate(holes, start=1)
    ]


def test_build4_guest_download_disables_the_historical_qr_splice(stored_seed):
    client, seed_id, metadata = stored_seed
    qr = metadata["qr"]
    start = qr["header_size"] + qr["splice_prg_offset"]
    unfinished = ips.apply(
        ROM_PATH.read_bytes(), (FIXTURE / "unfinished.ips").read_bytes()
    )
    assert unfinished[start : start + 2] == bytes.fromhex(qr["enabled_splice"])
    rom = download(client, seed_id, metadata)
    assert rom[start : start + 2] == bytes.fromhex(qr["disabled_splice"])
    with app_state(client).db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 0
