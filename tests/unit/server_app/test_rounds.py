"""`server/routes/round_pages.py`: scanning a round in and the round's page."""

import logging
import re

import pytest
from fastapi.testclient import TestClient

from server.views import RoundHoleView
from tests.app_state import app_state
from tests.unit.server_app.helpers import UNWRITTEN, dev_client, entered_seed, scan_path


def recorded_rounds(client: TestClient) -> list[dict]:
    with app_state(client).db.transaction() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM rounds ORDER BY id")]


def test_scanning_records_the_round_and_redirects_to_its_permalink(fake_builder):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = entered_seed(test_client, "alice")
        test_client.post("/auth/logout", data={"next": "/"})
        response = test_client.get(
            scan_path(test_client, seed_id, "alice", strokes=5), follow_redirects=False
        )
        recorded = recorded_rounds(test_client)
        page = test_client.get(response.headers["location"])
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    assert [
        (row["slot"], row["total_strokes"], row["total_putts"]) for row in recorded
    ] == [(0, 90, 36)]
    assert response.headers["location"] == f"/r/{recorded[0]['public_id']}?recorded"
    assert page.status_code == 200
    assert "round.heading_recorded" in page.text
    assert "round.player_one name=alice" in page.text
    assert f'href="/h/{seed_id}"' in page.text
    assert (
        '<span class="score-mark square score-depth-0"><span class="score-digit">5</span></span>'
        in page.text
    )
    assert '<td class="num strokes over-par">90</td>' in page.text


def test_the_permalink_confirms_the_round_only_for_the_scan_that_recorded_it(
    fake_builder,
):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = entered_seed(test_client, "alice")
        test_client.get(scan_path(test_client, seed_id, "alice", strokes=5))
        [row] = recorded_rounds(test_client)
        confirmed = test_client.get(f"/r/{row['public_id']}?recorded")
        plain = test_client.get(f"/r/{row['public_id']}")
    for page in (confirmed, plain):
        assert page.status_code == 200
        # a permalink is an ordinary page: unlike /s/, it may be cached
        assert "cache-control" not in page.headers
        assert '<td class="num strokes over-par">90</td>' in page.text
    assert "round.heading_recorded" in confirmed.text
    assert "history.replaceState" in confirmed.text
    assert "round.heading" in plain.text
    assert "round.heading_recorded" not in plain.text
    assert "history.replaceState" not in plain.text


@pytest.mark.parametrize("round_id", ["0123456789", "not-an-id", "", "short"])
def test_a_permalink_naming_no_round_is_not_found(fake_builder, round_id):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        assert test_client.get(f"/r/{round_id}").status_code == 404


def test_scanning_again_reaches_the_first_round_and_records_nothing(fake_builder):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = entered_seed(test_client, "alice")
        path = scan_path(test_client, seed_id, "alice", strokes=5)
        first = test_client.get(path, follow_redirects=False)
        again = test_client.get(path, follow_redirects=False)
        different = test_client.get(
            scan_path(test_client, seed_id, "alice", strokes=3), follow_redirects=False
        )
        recorded = recorded_rounds(test_client)
        pages = [
            test_client.get(response.headers["location"])
            for response in (again, different)
        ]
    permalink = f"/r/{recorded[0]['public_id']}"
    assert first.headers["location"] == f"{permalink}?recorded"
    # a rescan lands on the same round, without the confirmation the first scan earned
    for response in (again, different):
        assert response.status_code == 303
        assert response.headers["location"] == permalink
    for page in pages:
        assert "round.heading_recorded" not in page.text
        assert '<td class="num strokes over-par">90</td>' in page.text
    assert len(recorded) == 1


def test_strokes_are_marked_against_par(fake_builder):
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        course = fake_builder.built.course
        page = test_client.get(scan_path(test_client, seed_id, "alice", strokes=4)).text
    pars = [hole.par for hole in course.holes]
    assert course.par == 72
    # a 4 on every hole: a bogey square over a par 3, a birdie circle under a par 5, and
    # level par over the round
    assert page.count(
        '<td class="num strokes over-par"><span class="score-mark square score-depth-0"><span class="score-digit">4</span></span></td>'
    ) == pars.count(3)
    assert page.count(
        '<td class="num strokes under-par"><span class="score-mark circle score-depth-0"><span class="score-digit">4</span></span></td>'
    ) == pars.count(5)
    # total par and total strokes both land on 72; only the strokes cell carries the
    # column's class
    assert page.count('<td class="num">72</td>') == 1
    assert page.count('<td class="num strokes">72</td>') == 1


def fairway_cells(page: str) -> list[str]:
    return re.findall(r'<td class="fairway" data-fairway="(\w+)">', page)


def test_a_round_shows_each_holes_fairway_and_the_penalties(fake_builder):
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        pars = [hole.par for hole in fake_builder.built.course.holes]
        # the ROM sets a bit on every par 4 and 5 but the first two, and never on a par 3
        long_holes = [i for i, par in enumerate(pars) if par >= 4]
        hits = tuple(i in long_holes[2:] for i in range(18))
        page = test_client.get(
            scan_path(test_client, seed_id, "alice", fairways=hits, penalty_strokes=3)
        ).text
    expected = [
        "na" if par < 4 else "hit" if hits[i] else "miss" for i, par in enumerate(pars)
    ]
    assert fairway_cells(page) == expected
    possible = len(long_holes)
    counts = re.findall(
        r'data-fairways-hit="(\d+)"\s+data-fairways-possible="(\d+)"', page
    )
    # Out, In, then the round; the two misses are both on the front nine's first holes
    front = sum(par >= 4 for par in pars[:9])
    assert front >= 2
    assert [tuple(map(int, count)) for count in counts] == [
        (front - 2, front),
        (possible - front, possible - front),
        (possible - 2, possible),
    ]
    assert re.search(r'data-penalty-strokes="3"', page)
    assert 'class="nine striped with-fairways"' in page


def gir_cells(page: str) -> list[str]:
    return re.findall(r'<td class="gir"\s+data-gir="(\w+)">', page)


def greens_counts(page: str) -> list[tuple[int, int]]:
    found = re.findall(r'data-greens-hit="(\d+)"\s+data-greens-possible="(\d+)"', page)
    return [(int(hit), int(possible)) for hit, possible in found]


def test_a_round_shows_each_holes_green_in_regulation(fake_builder):
    """Fours with two putts: on in two, which is regulation on a par 4 or 5 and not on a
    par 3."""
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        pars = [hole.par for hole in fake_builder.built.course.holes]
        page = test_client.get(
            scan_path(test_client, seed_id, "alice", strokes=4, putts=2)
        ).text
    assert gir_cells(page) == ["hit" if par >= 4 else "miss" for par in pars]
    front = sum(par >= 4 for par in pars[:9])
    back = sum(par >= 4 for par in pars[9:])
    assert greens_counts(page) == [(front, 9), (back, 9), (front + back, 18)]


def test_a_hole_out_counts_as_a_green_in_regulation(fake_builder):
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        page = test_client.get(
            scan_path(test_client, seed_id, "alice", strokes=1, putts=0)
        ).text
    assert gir_cells(page) == ["hit"] * 18


@pytest.mark.parametrize(
    "strokes, putts, par, gir",
    [
        (3, 2, 3, True),
        (4, 2, 3, False),
        (5, 3, 4, True),
        (5, 2, 4, False),
        (3, 0, 5, True),
        (6, 3, 5, True),
        (6, 2, 5, False),
    ],
)
def test_green_in_regulation_is_the_strokes_before_the_putts(strokes, putts, par, gir):
    hole = RoundHoleView(1, par, strokes, putts)
    assert hole.green_in_regulation is gir


def test_a_tee_shot_that_goes_in_counts_as_a_fairway(fake_builder):
    """It never comes to rest, so the ROM sends no bit for it; the site counts it."""
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        pars = [hole.par for hole in fake_builder.built.course.holes]
        page = test_client.get(
            scan_path(test_client, seed_id, "alice", strokes=1, putts=0)
        ).text
    assert fairway_cells(page) == ["na" if par < 4 else "hit" for par in pars]


def test_a_version_one_round_shows_no_fairways_or_penalties(fake_builder):
    """Recorded before ROMs counted them: nothing shown, rather than misses and a zero."""
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        path = scan_path(test_client, seed_id, "alice", protocol_version=1)
        assert len(path.removeprefix("/s/")) == 48
        page = test_client.get(path).text
    assert 'class="nine striped"' in page
    assert "data-fairway" not in page
    assert "data-gir" not in page
    assert "data-penalty-strokes" not in page


def test_far_under_par_gets_a_second_or_third_ring(fake_builder):
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        course = fake_builder.built.course
        page = test_client.get(scan_path(test_client, seed_id, "alice", strokes=2)).text
    pars = [hole.par for hole in course.holes]
    # a 2 on every hole: an eagle on a par 4, drawn through an invisible spacer ring so its
    # two visible rings sit as far apart as an albatross's outer and inner ring rather than
    # its outer and middle; an albatross (or better) on a par 5, still just the one triple
    # ring regardless of how many strokes under
    assert page.count(
        '<span class="score-ring circle score-depth-0">'
        '<span class="score-ring circle score-depth-1 score-spacer">'
        '<span class="score-mark circle score-depth-2"><span class="score-digit">2</span></span></span></span>'
    ) == pars.count(4)
    assert page.count(
        '<span class="score-ring circle score-depth-0">'
        '<span class="score-ring circle score-depth-1">'
        '<span class="score-mark circle score-depth-2"><span class="score-digit">2</span></span></span></span>'
    ) == pars.count(5)


def test_far_over_par_gets_a_second_ring_or_a_triangle(fake_builder):
    with dev_client(fake_builder) as test_client:
        seed_id = entered_seed(test_client, "alice")
        course = fake_builder.built.course
        page = test_client.get(scan_path(test_client, seed_id, "alice", strokes=6)).text
    pars = [hole.par for hole in course.holes]
    # a 6 on every hole: a double bogey on a par 4, through the same spacer trick as an
    # eagle; a triple bogey (or worse) on a par 3, a triangle instead of a third square
    assert page.count(
        '<span class="score-ring square score-depth-0">'
        '<span class="score-ring square score-depth-1 score-spacer">'
        '<span class="score-mark square score-depth-2"><span class="score-digit">6</span></span></span></span>'
    ) == pars.count(4)
    assert page.count('<span class="score-triangle score-depth-0">') == pars.count(3)
    assert page.count('<span class="score-triangle-value">6</span>') == pars.count(3)


def test_a_player_two_scan_is_marked_on_the_page(fake_builder):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = entered_seed(test_client, "alice")
        response = test_client.get(scan_path(test_client, seed_id, "alice", slot=1))
    assert response.status_code == 200
    assert "round.player_two name=alice" in response.text


@pytest.mark.parametrize(
    "path, status, notice",
    [
        ("/s/" + "A" * 48, 400, "malformed"),
        ("/s/" + "A" * 20, 400, "malformed"),
        ("/s/" + "AQ" + "A" * 46, 400, "unfinished"),
    ],
)
def test_scans_that_are_not_rounds_are_refused(fake_builder, path, status, notice):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        response = test_client.get(path)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    assert "scan_rejected.heading" in response.text
    assert f"scan_rejected.{notice}" in response.text


def test_a_scan_signed_with_the_wrong_key_is_not_recognized(fake_builder):
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = entered_seed(test_client, "alice")
        response = test_client.get(
            scan_path(test_client, seed_id, "alice", key=bytes(8))
        )
        assert recorded_rounds(test_client) == []
    assert response.status_code == 404
    assert "scan_rejected.unrecognized" in response.text


def test_a_rejected_scan_never_logs_the_entry_keys(fake_builder, caplog):
    """The cause of a rejection goes to the log; the MAC keys behind it never do."""
    with dev_client(fake_builder, strings=UNWRITTEN) as test_client:
        seed_id = entered_seed(test_client, "alice")
        with app_state(test_client).db.transaction() as conn:
            keys = conn.execute(
                "SELECT key_slot0, key_slot1 FROM entries WHERE seed_id = ?", (seed_id,)
            ).fetchone()
        with caplog.at_level(logging.WARNING, logger="server.submissions"):
            test_client.get(scan_path(test_client, seed_id, "alice", key=bytes(8)))
    assert "scan rejected" in caplog.text
    for key in keys:
        assert key.hex() not in caplog.text
        assert str(key) not in caplog.text
