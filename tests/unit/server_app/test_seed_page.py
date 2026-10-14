"""`server/routes/seed_pages.py`: the seed page and its manifest."""

import json
import re

import pytest

from golf.randomizer.catalog import US_ROM, HoleStore
from golf.randomizer.manifest import Manifest, Settings, standard_rules
from golf.randomizer.roms import vanilla_rom
from server.forms import FormState
from tests.app_state import app_state
from tests.stored_seeds import store_seed
from tests.unit.server_app.helpers import (
    UNWRITTEN,
    US_ONLY,
    FakeBuilder,
    app_client,
    dev_client,
    download_article,
    entered_seed,
    generate_seed,
    post_download,
    scan_path,
    seed_form,
    summary_values,
)


def test_the_seed_page_shows_the_course(client, fake_builder, catalog):
    seed_id = generate_seed(client)
    manifest = fake_builder.built
    response = client.get(f"/h/{seed_id}")
    assert response.status_code == 200
    page = response.text
    assert " ".join(manifest.course.magic_words) in page
    codes = re.findall(r"<code>([^<]+)</code>", page)
    assert codes == [str(slot.id) for slot in manifest.course.holes]
    yards = sum(catalog[slot.id].distance for slot in manifest.course.holes)
    assert f'<td class="num">{manifest.course.par}</td>' in page
    assert f'<td class="num">{yards}</td>' in page
    assert f'href="/h/{seed_id}.json"' in page
    assert "Mario Open Golf (Japan)" in page or "NES Open Tournament Golf (USA)" in page


def test_the_seed_page_shows_its_creation_time_as_a_time_element(client):
    seed_id = generate_seed(client)
    with app_state(client).db.transaction() as conn:
        created = conn.execute(
            "SELECT created_at FROM seeds WHERE id = ?", (seed_id,)
        ).fetchone()[0]
    page = client.get(f"/h/{seed_id}").text
    assert f'<time datetime="{created}">' in page
    assert "localtime.js" in page


def test_the_seed_page_starts_its_hole_table_and_details_collapsed(unwritten_client):
    seed_id = generate_seed(unwritten_client)
    page = unwritten_client.get(f"/h/{seed_id}").text
    summaries = re.findall(r"<details\s*>\s*<summary>\s*<h2>(.*?)</h2>", page, re.S)
    assert len(summaries) == 2
    assert "seed.holes.heading" in summaries[0]
    assert "seed.details.heading" in summaries[1]
    assert "<details open" not in page


@pytest.mark.parametrize(
    ("choice", "rule", "per_nine", "key"),
    [
        ("experts_1", "expert_cap", "1", "seed.details.draw_rule_expert_cap"),
        ("experts_0", "expert_cap", "0", "seed.details.draw_rule_no_experts"),
        ("uniform", "uniform", None, "seed.details.draw_rule_uniform"),
    ],
)
def test_the_seed_page_shows_the_expert_cap_it_was_generated_under(
    unwritten_client, choice, rule, per_nine, key
):
    form = FormState.default()
    form.draw_rule = choice
    seed_id = generate_seed(unwritten_client, form)
    page = unwritten_client.get(f"/h/{seed_id}").text
    cell = re.search(r"<td data-draw-rule=[^>]*>(.*?)</td>", page, re.S)
    assert cell is not None
    assert f'data-draw-rule="{rule}"' in cell.group(0)
    if per_nine is None:
        assert "data-experts-per-nine" not in cell.group(0)
    else:
        assert f'data-experts-per-nine="{per_nine}"' in cell.group(0)
    assert key in cell.group(1)
    stored = unwritten_client.get(f"/h/{seed_id}.json").json()["settings"]
    assert "draw_rule" not in stored
    standard = {"rule": "standard", "par": 72}
    assert (
        stored["constraints"]
        == {
            "experts_1": None,
            "experts_0": [standard, {"rule": "expert_cap", "per_nine": 0}],
            "uniform": [standard],
        }[choice]
    )


def test_the_seed_page_names_the_wind_profiles_it_was_generated_under(
    unwritten_client,
):
    form = FormState.default()
    form.wind_speed, form.wind_direction = "storm_rolling_in", "out_and_back"
    seed_id = generate_seed(unwritten_client, form)
    page = unwritten_client.get(f"/h/{seed_id}").text
    speed = re.search(r'<td data-wind-speed="storm_rolling_in">(.*?)</td>', page)
    direction = re.search(r'<td data-wind-direction="out_and_back">(.*?)</td>', page)
    assert speed is not None and "wind.speed.storm_rolling_in" in speed.group(1)
    assert direction is not None
    assert "wind.direction.out_and_back" in direction.group(1)
    stored = unwritten_client.get(f"/h/{seed_id}.json").json()
    assert stored["settings"]["wind_speed_profile"] == "storm_rolling_in"
    assert stored["settings"]["wind_direction_profile"] == "out_and_back"
    holes = stored["course"]["holes"]
    assert all(hole["wind_speed"] in (0, 1, 2, 3) for hole in holes[:3])
    assert all(hole["wind_speed"] in (7, 8, 9) for hole in holes[-3:])
    with app_state(unwritten_client).db.transaction() as conn:
        rows = conn.execute(
            "SELECT wind_direction, wind_speed FROM seed_holes WHERE seed_id = ? "
            "ORDER BY position",
            (seed_id,),
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (hole["wind_direction"], hole["wind_speed"]) for hole in holes
    ]


def test_a_seed_stored_before_expert_caps_shows_none(catalog, curation, tmp_path):
    builder = FakeBuilder(catalog, curation, HoleStore(), tmp_path / "unused.nes")
    with app_client(strings=UNWRITTEN, builder=builder) as test_client:
        seed_id = legacy_seed(test_client)
        page = test_client.get(f"/h/{seed_id}").text
        stored = test_client.get(f"/h/{seed_id}.json").json()
    assert 'data-draw-rule="uniform"' in page
    assert 'data-wind-speed="vanilla"' in page
    assert 'data-wind-direction="vanilla"' in page
    assert "draw_rule" not in stored["settings"]
    assert "wind_speed_profile" not in stored["settings"]
    assert "wind_speed" not in stored["course"]["holes"][0]


def test_a_seed_shows_names_a_later_release_retired_by_name(client):
    seed_id = generate_seed(client)
    db = app_state(client).db
    with db.transaction() as conn:
        data = json.loads(
            conn.execute(
                "SELECT manifest FROM seeds WHERE id = ?", (seed_id,)
            ).fetchone()[0]
        )
        # a schema 3 or 4 seed's draw rule, which schema 5's constraints replaced
        del data["settings"]["constraints"]
        data["settings"]["draw_rule"] = {"rule": "ceiling", "per_nine": 2}
        data["settings"]["wind_speed_profile"] = "hurricane"
        data["settings"]["wind_direction_profile"] = "swirling"
        data["course"]["music"] = "jp_mars"
        conn.execute(
            "UPDATE seeds SET manifest = ? WHERE id = ?", (json.dumps(data), seed_id)
        )
    response = client.get(f"/h/{seed_id}")
    assert response.status_code == 200
    page = response.text
    assert re.search(r'data-draw-rule="ceiling"[^>]*>\s*ceiling\s*<', page)
    assert 'data-wind-speed="hurricane">hurricane<' in page
    assert 'data-wind-direction="swirling">swirling<' in page
    assert 'data-music="jp_mars">jp_mars<' in page
    assert client.get(f"/h/{seed_id}/book.json").status_code == 200


def test_the_manifest_json_is_the_stored_manifest(client, fake_builder):
    seed_id = generate_seed(client)
    response = client.get(f"/h/{seed_id}.json")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert Manifest.from_json(response.json()) == fake_builder.built
    with app_state(client).db.transaction() as conn:
        assert response.text == conn.execute("SELECT manifest FROM seeds").fetchone()[0]


@pytest.mark.parametrize("path", ["/h/0000000001.json", "/h/nope.json"])
def test_unknown_manifests_are_json_404s(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_the_seed_page_offers_the_download_form(client):
    seed_id = generate_seed(client, seed_form(**US_ONLY, banned={"1W", "SW"}))
    page = client.get(f"/h/{seed_id}").text
    article = page[
        page.index('<article class="download"') : page.index(
            "</article>", page.index('<article class="download"')
        )
    ]
    assert 'data-state="checking"' in article
    assert f'data-required-roms="{US_ROM}"' in article
    assert f'data-filename="notgr_par72_{seed_id}.nes"' in article
    assert f'action="/h/{seed_id}/patch.ips"' in article
    assert re.search(r'name="player_name"\s+value="MARIO"\s+maxlength="10"', article)
    assert article.count('name="clubs"') == 15
    assert 'value="PT"' not in article
    assert re.search(r'name="clubs"\s+value="1W"\s+disabled', article)
    assert re.search(r'name="clubs"\s+value="SW"\s+disabled', article)
    assert re.search(r'name="clubs"\s+value="3W"\s+checked', article)
    assert not re.search(r'name="clubs"\s+value="4W"\s+checked', article)
    assert re.search(r'<button type="submit" disabled>', article)
    assert 'href="/rom"' in article
    assert 'id="download-strings"' in page
    assert (
        f'id="download-roms">{{"{US_ROM}": {{"sha1": "{vanilla_rom(US_ROM).sha1}"'
        in page
    )
    assert page.index('src="/static/romstore.js?v=') < page.index(
        'src="/static/download.js?v='
    )


def test_a_locked_bag_seed_lists_no_clubs(unwritten_client):
    seed_id = generate_seed(unwritten_client, seed_form(required_bag={"1W", "PW"}))
    page = unwritten_client.get(f"/h/{seed_id}").text
    assert 'name="clubs"' not in page
    assert "seed.download.locked_bag clubs=1W PW PT" in page


def test_the_seed_page_tells_only_signed_out_players_the_rom_is_a_guest_rom(
    fake_builder,
):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = generate_seed(test_client)
        signed_out = test_client.get(f"/h/{seed_id}").text
        test_client.get("/auth/login", params={"as": "alice"})
        signed_in = test_client.get(f"/h/{seed_id}").text
    assert "seed.download.guest_notice" in signed_out
    assert f'href="/auth/login?next=/h/{seed_id}"' in signed_out
    assert "seed.download.guest_notice" not in signed_in


def test_the_seed_page_has_no_guest_notice_without_sign_in(unwritten_client):
    seed_id = generate_seed(unwritten_client)
    assert (
        "seed.download.guest_notice" not in unwritten_client.get(f"/h/{seed_id}").text
    )


def test_the_seed_page_lists_recorded_rounds(fake_builder):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = entered_seed(test_client, "alice", "bob")
        assert "seed.rounds.none" in test_client.get(f"/h/{seed_id}").text
        test_client.get(scan_path(test_client, seed_id, "alice", strokes=5))
        test_client.get(scan_path(test_client, seed_id, "alice", slot=1, strokes=6))
        test_client.get(scan_path(test_client, seed_id, "bob", strokes=4))
        page = test_client.get(f"/h/{seed_id}").text
    assert "seed.rounds.none" not in page
    rounds = page[page.index('class="rounds') :]
    assert (
        rounds.index(">bob</a>")
        < rounds.index(">alice</a>")
        < rounds.index("seed.rounds.player_two name=alice")
    )
    # every listed round links to its own permalink
    assert len(set(re.findall(r'href="(/r/\w{10})"', rounds))) == 3


def test_the_seed_page_collapses_rounds_until_the_viewer_has_recorded_one(fake_builder):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = entered_seed(test_client, "alice", "bob", "carol")
        test_client.get(scan_path(test_client, seed_id, "alice", strokes=5))
        test_client.get(scan_path(test_client, seed_id, "bob", slot=1, strokes=6))
        entered_only = test_client.get(f"/h/{seed_id}").text
        test_client.get("/auth/login", params={"as": "bob"})
        player_two_only = test_client.get(f"/h/{seed_id}").text
        test_client.post("/auth/logout", data={"next": "/"})
        guest = test_client.get(f"/h/{seed_id}").text

    def rounds_card(page: str) -> str:
        return page[: page.index('class="rounds')].rsplit("<details", 1)[1]

    # the summary counts the rounds, whether or not the card is open
    assert "seed.rounds.heading_count count=2" in rounds_card(guest)
    assert rounds_card(guest).startswith(">")
    assert rounds_card(entered_only).startswith(">")
    assert rounds_card(player_two_only).startswith(" open>")


def test_the_seed_page_shows_no_rounds_card_without_rounds(fake_builder):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = entered_seed(test_client, "alice")
        page = test_client.get(f"/h/{seed_id}").text
    assert "seed.rounds.none" in page
    # only the hole table and details are cards; the rounds heading stands alone
    assert page.count('<article class="collapsible">') == 2


def test_the_seed_page_marks_each_hole_against_par(fake_builder):
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        course = fake_builder.built.course
        test_client.get(scan_path(test_client, seed_id, "alice", strokes=4))
        page = test_client.get(f"/h/{seed_id}").text
    rounds = page[page.index('class="rounds') :]
    pars = [hole.par for hole in course.holes]
    # the same notation as the round page: a 4 is a bogey square over a par 3, a birdie
    # circle under a par 5 and a plain number on a par 4
    assert rounds.count(
        '<td class="num strokes over-par"><span class="score-mark square score-depth-0"><span class="score-digit">4</span></span></td>'
    ) == pars.count(3)
    assert rounds.count(
        '<td class="num strokes under-par"><span class="score-mark circle score-depth-0"><span class="score-digit">4</span></span></td>'
    ) == pars.count(5)
    assert rounds.count('<td class="num strokes">4</td>') == pars.count(4)


def legacy_seed(client) -> str:
    """A seed stored as the randomizer 1.0 site stored them: schema 1, finish ABI 1,
    drawn uniformly with the wind its seeds deal."""
    state = app_state(client)
    manifest = state.builder.generate(Settings(constraints=standard_rules(72, None)))
    return store_seed(state.db, manifest, schema=1, catalog=state.builder.catalog)


def settings_tag(article: str) -> str:
    match = re.search(r'<details class="download-settings"[^>]*>', article)
    assert match is not None
    return match.group(0)


def selected_text(article: str, field: str) -> str:
    """The text of a select's chosen option."""
    start = article.index(f'<select name="{field}">')
    select = article[start : article.index("</select>", start)]
    match = re.search(r"<option[^>]*\sselected>(.*?)</option>", select, re.S)
    assert match is not None
    return match.group(1)


def test_the_download_settings_start_collapsed_under_a_summary(client):
    seed_id = generate_seed(client)
    article = download_article(client.get(f"/h/{seed_id}").text)
    tag = settings_tag(article)
    assert " open" not in tag
    assert 'data-over-max="false"' in tag
    assert 'data-clubs-max="14"' in tag
    values = summary_values(article)
    assert values["name"] == ["MARIO"]
    assert values["clubs"] == ["14", "14"]
    for field in ("swing", "putt", "spin"):
        assert values[field] == [selected_text(article, field)]
    music = re.search(
        r'data-summary="bgm"\s+data-on="([^"]*)"\s+data-off="([^"]*)">([^<]*)<', article
    )
    assert music is not None
    assert music.group(3) == music.group(1)
    assert 'class="download-removed"' not in article
    assert '<mark class="download-over-max">' in article
    assert '<input type="hidden" name="bgm" value="off">' in article
    assert re.search(r'name="bgm"\s+value="on"\s+role="switch"\s+checked', article)
    for field, count in (("swing", 4), ("putt", 4), ("spin", 6)):
        start = article.index(f'<select name="{field}">')
        select = article[start : article.index("</select>", start)]
        assert select.count("<option") == count
        assert re.search(r'<option value="off"\s+selected>', select)


def test_a_bag_over_the_max_opens_the_settings_and_is_marked(client):
    seed_id = generate_seed(client, seed_form(clubs_max="10"))
    article = download_article(client.get(f"/h/{seed_id}").text)
    tag = settings_tag(article)
    assert " open" in tag
    assert 'data-over-max="true"' in tag
    assert 'data-clubs-max="10"' in tag
    assert '<mark class="download-over-max">' in article


def test_banned_clubs_are_marked_as_removed_from_the_starting_bag(client):
    seed_id = generate_seed(client, seed_form(banned={"SW", "1W"}))
    article = download_article(client.get(f"/h/{seed_id}").text)
    assert 'class="download-removed"' in article
    assert summary_values(article)["clubs"] == ["12", "12"]
    assert " open" not in settings_tag(article)


def test_an_abi_one_seed_offers_bgm_but_no_swing_putt_or_spin(
    catalog, curation, tmp_path
):
    builder = FakeBuilder(catalog, curation, HoleStore(), tmp_path / "unused.nes")
    with app_client(builder=builder) as test_client:
        seed_id = legacy_seed(test_client)
        article = download_article(test_client.get(f"/h/{seed_id}").text)
        assert set(summary_values(article)) == {"name", "clubs", "bgm"}
        assert 'name="bgm"' in article
        for field in ("swing", "putt", "spin"):
            assert f'name="{field}"' not in article

        assert post_download(test_client, seed_id).status_code == 200
        response = post_download(test_client, seed_id, extra={"spin": "back1"})
        assert response.status_code == 400
        assert response.json() == {"error": "invalid", "values": {"field": "spin"}}
