"""What the app tests share: stand-in builders, clients, and the forms and scans they post."""

import re

from fastapi.testclient import TestClient

from golf.qr.payload import URL_PREFIX, HoleRecord, RoundPayload
from golf.randomizer.catalog import US_ROM
from golf.randomizer.manifest import Manifest
from golf.randomizer.roms import VANILLA_ROMS
from server.app import create_app
from server.builder import BuiltSeed, SeedBuilder
from server.config import Config
from server.forms import FormState
from server.strings import Entry, Strings
from tests.app_state import app_state
from tests.synthetic_holes import built_holes

IPS = b"PATCH\x00\x00\x10\x00\x01\xeaEOF"
FINISHED = b"PATCH\x00\x00\x20\x00\x01\x60EOF"
SEED_URL = re.compile(r"^/h/([0-9A-Za-z]{10})$")


class FakeBuilder(SeedBuilder):
    """Generates for real and stores a fixed IPS instead of building, so no ROM is needed."""

    def build(self, manifest: Manifest, sample=None) -> BuiltSeed:
        self.built = manifest
        return BuiltSeed(IPS, built_holes(manifest, self.catalog))

    def finish(self, manifest, unfinished_ips, options, credentials=None):
        self.finished = (manifest, unfinished_ips, options)
        self.credentials = credentials
        return FINISHED


def app_client(**kwargs) -> TestClient:
    return TestClient(create_app(Config(database=":memory:"), **kwargs))


def catalog_with_text(text_for) -> Strings:
    real = Strings.load()
    keys = real.keys()
    return Strings({key: Entry(real.entry(key).note, text_for(key)) for key in keys})


#: nothing written, so every string renders as the placeholder naming its key and values
UNWRITTEN = catalog_with_text(lambda key: "")


def form_data(form: FormState | None = None) -> dict[str, list[str]]:
    data: dict[str, list[str]] = {}
    for name, value in (form or FormState.default()).to_pairs():
        data.setdefault(name, []).append(value)
    return data


def post_generate(
    client: TestClient,
    form: FormState | None = None,
    headers: dict[str, str] | None = None,
):
    return client.post(
        "/generate", data=form_data(form), headers=headers, follow_redirects=False
    )


def generate_seed(client: TestClient, form: FormState | None = None) -> str:
    response = post_generate(client, form)
    assert response.status_code == 303, response.text
    match = SEED_URL.match(response.headers["location"])
    assert match is not None
    return match.group(1)


ALL_HASHES = {f"rom_{rom.id}": rom.sha1 for rom in VANILLA_ROMS}


def seed_form(**changes) -> FormState:
    form = FormState.default()
    for name, value in changes.items():
        setattr(form, name, value)
    return form


#: a seed built from the US ROM alone: its holes and its theme
US_ONLY = {"sources": {US_ROM}, "music": "nes_us"}


def post_download(
    client: TestClient,
    seed_id: str,
    name: str = "luigi",
    clubs=("1W", "PW"),
    hashes=None,
    extra=None,
):
    data = {
        "player_name": name,
        "clubs": list(clubs),
        **(ALL_HASHES if hashes is None else hashes),
        **(extra or {}),
    }
    return client.post(f"/h/{seed_id}/patch.ips", data=data)


def users(client: TestClient) -> list[dict]:
    with app_state(client).db.transaction() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM users ORDER BY id")]


def dev_client(fake_builder, **kwargs) -> TestClient:
    return TestClient(
        create_app(
            Config(database=":memory:", dev_login=True), builder=fake_builder, **kwargs
        )
    )


def scan_path(
    client: TestClient,
    seed_id: str,
    username: str,
    slot: int = 0,
    strokes: int = 4,
    key=None,
    putts: int = 2,
    **changes,
) -> str:
    """
    The path a ROM's QR code opens: username's entry in the seed, every hole `strokes` with
    `putts` putts. `changes` go to the `RoundPayload`: a protocol version, fairways, penalties.
    """
    with app_state(client).db.transaction() as conn:
        row = conn.execute(
            """
            SELECT seeds.qr_seed_id, users.player_id, entries.key_slot0, entries.key_slot1
            FROM entries JOIN seeds ON seeds.id = entries.seed_id JOIN users ON users.id = entries.user_id
            WHERE entries.seed_id = ? AND users.username = ?
            """,
            (seed_id, username),
        ).fetchone()
    round_payload = RoundPayload(
        seed_id=row["qr_seed_id"].to_bytes(8, "big"),
        player_id=row["player_id"].to_bytes(4, "big"),
        holes=(HoleRecord(strokes, putts),) * 18,
        player_slot=slot,
        **changes,
    )
    signing_key = (
        key
        if key is not None
        else bytes(row["key_slot1"] if slot else row["key_slot0"])
    )
    return "/s/" + round_payload.to_url(signing_key).removeprefix(URL_PREFIX)


def entered_seed(test_client: TestClient, *names: str) -> str:
    """A seed each named dev user has downloaded signed in; signed in as the last of them."""
    seed_id = generate_seed(test_client)
    for name in names:
        test_client.get("/auth/login", params={"as": name})
        assert post_download(test_client, seed_id).status_code == 200
    return seed_id


def download_article(page: str) -> str:
    start = page.index('<article class="download"')
    return page[start : page.index("</article>", start)]


def summary_values(article: str) -> dict[str, list[str]]:
    """Each value the summary marks for download.js, by field, in the order they appear."""
    summary = article[article.index("<summary>") : article.index("</summary>")]
    values: dict[str, list[str]] = {}
    for field, text in re.findall(
        r'<span data-summary="(\w+)"[^>]*>(.*?)</span>', summary
    ):
        values.setdefault(field, []).append(text)
    return values
