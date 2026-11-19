"""A daily's results on its seed page: the standing, the viewer's matches and each hole's
skins, to a player who has played it while it is open and to everyone once it closes."""

import re
from datetime import UTC, date, datetime

import pytest

from golf.qr.payload import HoleRecord
from golf.randomizer.catalog import Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Settings
from golf.ratings.rules import DailyRules
from server.dailies import score_day
from server.ratings import refit
from server.submissions import submit_scan
from tests.app_state import app_state
from tests.stored_seeds import store_seed
from tests.unit.server_app.helpers import dev_client
from tests.unit.test_server_ratings import make_daily, play_all
from tests.unit.test_server_submissions import Player

DAY = "2026-11-18"
OPEN = datetime(2026, 11, 18, 20, 0, tzinfo=UTC)
CLOSED = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
#: the next day, with DAY closed and the home page's previous day
NEXT_DAY = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture(scope="module")
def manifests():
    catalog, curation = Catalog.load(), CurationSnapshot.load()
    return [
        generate(catalog, curation, Settings(prng_seed=f"results{i}")) for i in range(3)
    ]


@pytest.fixture
def clock():
    return Clock(OPEN)


@pytest.fixture
def site(fake_builder, clock):
    with dev_client(fake_builder, clock=clock) as test_client:
        yield test_client


@pytest.fixture
def daily(site, manifests):
    """A rated day's seed, with the day's rules."""
    state = app_state(site)
    played = [store_seed(state.db, m) for m in manifests[:2]]
    play_all(state.db, played)
    assert refit(state.db, state.ratings, date(2026, 11, 17), min_rounds=5)
    seed = store_seed(state.db, manifests[2], rater=state.ratings)
    rules = DailyRules.from_parameters(state.ratings.parameters)
    make_daily(state.db, DAY, seed, rules=rules)
    return seed


def play(site, seed: str, name: str, strokes: int, hour: int) -> str:
    """`name` scans a round of `strokes` on every hole but the 18th, one more there;
    returns the round's public id."""
    holes = (HoleRecord(strokes, 2),) * 17 + (HoleRecord(strokes + 1, 2),)
    player = Player(app_state(site).db, seed, name)
    scanned = submit_scan(
        app_state(site).db, player.scan(holes=holes), now=f"{DAY}T{hour}:00:00Z"
    )
    return scanned.round.public_id


def results(site, seed: str) -> str | None:
    page = site.get(f"/h/{seed}").text
    found = re.search(r'<article class="daily-results">(.*?)</article>', page, re.S)
    return None if found is None else found.group(1)


def standing_users(article: str) -> list[str]:
    standing = article[: article.index("</table>")]
    return re.findall(r'data-user="(\d+)"', standing)


def test_a_player_who_has_played_sees_the_standing_their_matches_and_skins(site, daily):
    play(site, daily, "dee", 4, 12)
    play(site, daily, "eve", 5, 13)
    site.get("/auth/login", params={"as": "dee"})
    article = results(site, daily)
    assert article is not None
    assert len(standing_users(article)) == 2
    assert "data-winner" in article and "data-viewer" in article
    assert re.findall(r'data-result="(\w+)"', article) == ["win"]
    # dee's 4s beat eve's 5s on every hole: the match is over 10 up with 8 to play, and
    # its result opens the match's card
    assert 'data-lead="10"' in article and 'data-to-play="8"' in article
    assert "W 10&amp;8" in article
    assert re.search(r'href="/r/\w{10}/vs/\w{10}"', article)
    # every skin is dee's, none carried
    assert 'class="skins-list' in article
    assert "data-carried" not in article


def test_a_player_who_has_not_played_an_open_day_sees_no_results(site, daily):
    play(site, daily, "dee", 4, 12)
    play(site, daily, "eve", 5, 13)
    site.get("/auth/login", params={"as": "fay"})
    assert results(site, daily) is None


def test_once_closed_anyone_sees_the_standing_but_no_matches(site, daily, clock):
    play(site, daily, "dee", 4, 12)
    play(site, daily, "eve", 5, 13)
    clock.now = CLOSED
    article = results(site, daily)
    assert article is not None
    assert len(standing_users(article)) == 2
    assert "data-result" not in article
    assert "data-viewer" not in article


def test_tied_holes_carry_their_skins(site, daily):
    play(site, daily, "dee", 4, 12)
    play(site, daily, "eve", 4, 13)
    site.get("/auth/login", params={"as": "dee"})
    article = results(site, daily)
    assert article is not None
    skins = article[article.index('class="skins-list') :]
    # both play at the starting index, so every hole ties and every skin carries
    assert skins.count("data-carried") == 18
    assert re.findall(r'data-result="(\w+)"', article) == ["half"]
    assert "Halved" in article


def test_a_field_of_one_has_a_standing_but_no_skins(site, daily):
    play(site, daily, "dee", 4, 12)
    site.get("/auth/login", params={"as": "dee"})
    article = results(site, daily)
    assert article is not None
    assert len(standing_users(article)) == 1
    assert "data-winner" not in article
    assert "skins-list" not in article


# -- The net card on a round page -----------------------------------------------------------


def attribute_values(page: str, name: str) -> list[str]:
    return re.findall(rf'{name}="(-?\d+)"', page)


def test_a_daily_rounds_page_shows_its_net_card_separately_by_default(site, daily):
    round_id = play(site, daily, "dee", 5, 12)
    site.get("/auth/login", params={"as": "dee"})
    page = site.get(f"/r/{round_id}").text
    assert 'class="net-card"' in page
    handicap = int(attribute_values(page, "data-handicap")[0])
    received = [int(r) for r in attribute_values(page, "data-received")]
    assert len(received) == 18 and sum(received) == handicap
    # strokes land on the lowest stroke indexes
    indexes = [int(i) for i in attribute_values(page, "data-stroke-index")]
    assert sorted(indexes) == list(range(1, 19))
    assert all(
        (r > 0) == (si <= handicap) for r, si in zip(received, indexes, strict=True)
    )
    assert "with-net" not in page


def nine_tables(page: str) -> list[str]:
    return re.findall(r'<table class="nine[^"]*">(.*?)</table>', page, re.S)


def net_cells(html: str) -> list[str]:
    return [
        c.strip()
        for c in re.findall(r'<td class="num net"[^>]*>(.*?)</td>', html, re.S)
    ]


def test_combined_shows_each_net_after_its_gross(site, daily):
    round_id = play(site, daily, "dee", 5, 12)
    site.get("/auth/login", params={"as": "dee"})
    page = site.get(f"/r/{round_id}", params={"net": "combined"}).text
    assert page.count("with-net") == 2
    assert 'class="net-card"' not in page
    handicap = int(attribute_values(page, "data-handicap")[0])
    received = [int(r) for r in attribute_values(page, "data-received")]
    nets = [int(n) for n in attribute_values(page, "data-net")]
    assert len(received) == 18 and sum(received) == handicap
    assert len(attribute_values(page, "data-points")) >= 18
    # a hole shows its net only where strokes received changed it
    holes = [
        cell
        for table in nine_tables(page)
        for cell in net_cells(table[: table.index("<tfoot>")])
    ]
    assert holes == [str(n) if r else "" for r, n in zip(received, nets, strict=True)]
    # the scores together, then the stats: fairways (when recorded) before putts
    for table in nine_tables(page):
        cols = re.findall(r'<col class="(\w+)">', table)
        assert "received" not in cols
        start = cols.index("strokes")
        assert cols[start : start + 3] == ["strokes", "net", "points"]
        assert cols[-1] == "putts"
        if "fairway" in cols:
            assert cols.index("points") < cols.index("fairway") < cols.index("putts")
    # a hole that scored nothing is muted
    points = re.findall(
        r'<td class="num points( no-points)?"\s+data-points="(\d+)"', page
    )
    assert len(points) == 18
    assert all((muted != "") == (value == "0") for muted, value in points)


def test_a_nines_net_total_follows_its_gross(site, daily):
    round_id = play(site, daily, "dee", 5, 12)
    site.get("/auth/login", params={"as": "dee"})
    page = site.get(f"/r/{round_id}", params={"net": "combined"}).text
    received = [int(r) for r in attribute_values(page, "data-received")]
    nets = [int(n) for n in attribute_values(page, "data-net")]
    for number, table in enumerate(nine_tables(page)):
        nine = slice(9 * number, 9 * number + 9)
        expected = [str(sum(nets[nine])) if any(received[nine]) else ""]
        if number == 1:
            expected.append(str(sum(nets)) if any(received) else "")
        assert net_cells(table[table.index("<tfoot>") :]) == expected


def test_an_ordinary_round_has_no_net_card(site, daily, manifests):
    state = app_state(site)
    seed = store_seed(state.db, manifests[0])
    round_id = play(site, seed, "dee", 5, 12)
    site.get("/auth/login", params={"as": "dee"})
    for layout in ("separate", "combined"):
        page = site.get(f"/r/{round_id}", params={"net": layout}).text
        assert "data-received" not in page


def test_an_open_dailys_net_card_is_hidden_from_a_player_who_has_not_played(
    site, daily
):
    round_id = play(site, daily, "dee", 5, 12)
    site.get("/auth/login", params={"as": "fay"})
    page = site.get(f"/r/{round_id}").text
    assert 'data-state="scores-hidden"' in page
    assert "data-received" not in page


def podium(site) -> list[tuple[str, str]]:
    """The home page's previous day's top places, as (position, points) pairs."""
    page = site.get("/").text
    found = re.search(r'<table class="daily-podium">(.*?)</table>', page, re.S)
    assert found is not None
    return re.findall(r'data-position="(\d+)"\s+data-points="(\d+)"', found.group(1))


def test_the_home_page_shows_yesterdays_top_three_with_shared_places(
    site, daily, clock
):
    rounds = {
        name: play(site, daily, name, strokes, 12 + number)
        for number, (name, strokes) in enumerate(
            (("dee", 3), ("eve", 4), ("fay", 5), ("gus", 5), ("hal", 6))
        )
    }
    clock.now = NEXT_DAY
    places = podium(site)
    # fay and gus play the same card, so they share third and both show; hal is fifth
    assert [position for position, _ in places] == ["1", "2", "3", "3"]
    points = [int(p) for _, p in places]
    assert points == sorted(points, reverse=True) and points[2] == points[3]
    page = site.get("/").text
    assert 'class="podium-header"' in page
    assert all(f'href="/r/{rounds[n]}"' in page for n in ("dee", "eve", "fay", "gus"))
    assert f'href="/r/{rounds["hal"]}"' not in page
    assert 'data-players="5"' in page


def test_yesterdays_box_shows_a_player_their_own_round(site, daily, clock):
    play(site, daily, "dee", 4, 12)
    clock.now = NEXT_DAY
    site.get("/auth/login", params={"as": "dee"})
    page = site.get("/").text
    box = page[page.index("previous-daily") :]
    assert 'class="daily-seed-card"' in box


# -- A match's card -------------------------------------------------------------------------


def match_card(site, mine: str, theirs: str) -> str:
    response = site.get(f"/r/{mine}/vs/{theirs}")
    assert response.status_code == 200
    return response.text


def test_a_match_card_shows_the_match_hole_by_hole_from_either_side(site, daily):
    dee = play(site, daily, "dee", 4, 12)
    eve = play(site, daily, "eve", 5, 13)
    site.get("/auth/login", params={"as": "dee"})
    page = match_card(site, dee, eve)
    assert 'class="match-card"' in page
    assert 'data-lead="10"' in page and 'data-to-play="8"' in page
    # dee wins the first 10 holes, and the match is over: the last 8 count for nothing
    assert page.count("data-won") == 10
    assert re.findall(r'<td class="num" data-lead="(-?\d+)"', page) == [
        str(n) for n in range(1, 11)
    ]
    assert page.count("after-decided") == 3 * 8
    assert "W 10&amp;8" in page
    theirs = match_card(site, eve, dee)
    assert 'data-lead="-10"' in theirs and "L 10&amp;8" in theirs
    # level handicaps: nobody receives a stroke
    assert "match-dots" not in page


def test_the_weaker_player_receives_the_difference_in_the_match(site, daily):
    dee = play(site, daily, "dee", 4, 12)
    eve = play(site, daily, "eve", 4, 13)
    db = app_state(site).db
    with db.transaction() as conn:
        conn.execute(
            "UPDATE daily_cards SET handicap = handicap + 2 WHERE round_public_id = ?",
            (eve,),
        )
        score_day(conn, DAY)
    site.get("/auth/login", params={"as": "dee"})
    page = match_card(site, dee, eve)
    received = [int(r) for r in attribute_values(page, "data-received")]
    # both players' 18 holes, and only eve's two hardest carry a dot
    assert len(received) == 36 and sum(received[:18]) == 0 and sum(received[18:]) == 2
    assert page.count('class="match-dots"') == 2
    # eve's two strokes win her the two hardest holes, the 1st and 12th, and nothing
    # else differs: 2 up with 1 to play
    assert 'data-lead="-2"' in page and 'data-to-play="1"' in page
    assert "L 2&amp;1" in page


def test_a_match_card_hides_from_a_viewer_who_has_not_played_an_open_day(site, daily):
    dee = play(site, daily, "dee", 4, 12)
    eve = play(site, daily, "eve", 5, 13)
    site.get("/auth/login", params={"as": "fay"})
    page = match_card(site, dee, eve)
    assert 'data-state="scores-hidden"' in page
    assert "data-won" not in page and "match-card" not in page


def test_only_two_players_on_the_same_day_have_a_match_card(site, daily, manifests):
    dee = play(site, daily, "dee", 4, 12)
    eve = play(site, daily, "eve", 5, 13)
    other = play(site, store_seed(app_state(site).db, manifests[0]), "fay", 4, 14)
    site.get("/auth/login", params={"as": "dee"})
    assert site.get(f"/r/{dee}/vs/{dee}").status_code == 404
    assert site.get(f"/r/{dee}/vs/{other}").status_code == 404
    assert site.get(f"/r/{dee}/vs/nosuchrnd0").status_code == 404
    assert site.get(f"/r/{dee}/vs/{eve}").status_code == 200
