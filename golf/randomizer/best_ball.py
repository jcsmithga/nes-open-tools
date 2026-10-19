"""
Best-ball scorecards from a seed's scores CSV (the admin seed page's `scores.csv`).

A pair's score on a hole is the better of its two players' strokes. This reads the CSV,
holds the pairs a person matches up (`Matching`), and renders each pair's card and a
leaderboard as plain text. It computes nothing the site stores; `golf-best-ball` is its
interactive front end.

Only each player's own rounds are read. A `teammate` row is a round played as player 2 on
a player's ROM, by someone the site does not know, so it is skipped.
"""

import csv
import io
from dataclasses import dataclass, field

HOLES = 18

#: the column of the first hole in the CSV, after round, player, username, kind,
#: received_at and flagged
FIRST_HOLE = 6
#: how many characters of a name a card's row label shows
LABEL = 12


class ScoresError(ValueError):
    """A CSV that is not a seed's scores export."""


@dataclass(frozen=True)
class Player:
    name: str
    strokes: tuple[int, ...]

    @property
    def total(self) -> int:
        return sum(self.strokes)


@dataclass(frozen=True)
class Scores:
    pars: tuple[int, ...]
    players: tuple[Player, ...]
    #: rows skipped because they are not a player's own round
    skipped: int


def read_scores(text: str) -> Scores:
    """A scores CSV's pars and its players' own rounds, in file order."""
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2 or rows[1][:1] != ["par"]:
        raise ScoresError("not a scores CSV: no par row after the header")
    header = rows[0]
    if header[FIRST_HOLE : FIRST_HOLE + HOLES] != [str(n) for n in range(1, HOLES + 1)]:
        raise ScoresError("not a scores CSV: the hole columns are not 1 to 18")

    def holes(row: list[str]) -> tuple[int, ...]:
        try:
            return tuple(int(value) for value in row[FIRST_HOLE : FIRST_HOLE + HOLES])
        except ValueError:
            raise ScoresError(
                f"a hole score is not a number in row {row[:2]}"
            ) from None

    players = []
    skipped = 0
    for row in rows[2:]:
        if not row:
            continue
        if row[3] != "own":
            skipped += 1
            continue
        players.append(Player(name=row[1] or row[2], strokes=holes(row)))
    return Scores(pars=holes(rows[1]), players=tuple(players), skipped=skipped)


@dataclass
class Matching:
    """Players numbered from 1 in file order, and the pairs matched so far."""

    players: tuple[Player, ...]
    pairs: list[tuple[int, int]] = field(default_factory=list)

    def matched(self) -> set[int]:
        return {number for pair in self.pairs for number in pair}

    def unmatched(self) -> list[int]:
        taken = self.matched()
        return [n for n in range(1, len(self.players) + 1) if n not in taken]

    def player(self, number: int) -> Player:
        return self.players[number - 1]

    def pair(self, text: str) -> str:
        """Match the two players `text` numbers, as `1, 2`; returns what happened.

        Raises ValueError, with a message to show, for anything that is not two
        different, unmatched players' numbers.
        """
        parts = [part for part in text.replace(",", " ").split() if part]
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("enter two player numbers, like 1, 2")
        first, second = (int(part) for part in parts)
        for number in (first, second):
            if not 1 <= number <= len(self.players):
                raise ValueError(f"there is no player {number}")
            if number in self.matched():
                raise ValueError(f"{self.player(number).name} is already matched")
        if first == second:
            raise ValueError("a pair is two different players")
        self.pairs.append((first, second))
        return f"matched {self.player(first).name} and {self.player(second).name}"

    def undo(self) -> str:
        if not self.pairs:
            raise ValueError("nothing to undo")
        first, second = self.pairs.pop()
        return f"unmatched {self.player(first).name} and {self.player(second).name}"

    def listing(self) -> str:
        """The pairs so far, then the players left to match, each with their number."""
        lines = []
        if self.pairs:
            lines.append("Matched:")
            for first, second in self.pairs:
                lines.append(
                    f"  {first:>2}. {self.player(first).name}"
                    f"  +  {second:>2}. {self.player(second).name}"
                )
        left = self.unmatched()
        lines.append("Players:" if left else "Every player is matched.")
        for number in left:
            lines.append(f"  {number:>2}. {self.player(number).name}")
        return "\n".join(lines)


def best_ball(first: Player, second: Player) -> tuple[int, ...]:
    return tuple(min(a, b) for a, b in zip(first.strokes, second.strokes, strict=True))


def to_par(strokes: int, par: int) -> str:
    difference = strokes - par
    return "E" if difference == 0 else f"{difference:+d}"


def _cell(value: int | str, counted: bool = False) -> str:
    """A hole's cell: a space, the value right-aligned in two columns (10 strokes is a
    hole's most), then its mark or a space, so a marked score never runs into the next."""
    return f" {value:>2}{'*' if counted else ' '}"


def _row(label: str, cells: list[str], totals: list[int] | list[str]) -> str:
    """A card row: the label, the front nine, the back nine, then out, in and total."""
    front, back = "".join(cells[:9]), "".join(cells[9:])
    out, back_nine, total = (f"{value:>3}" for value in totals)
    return f"{label[:LABEL]:<{LABEL}}{front}| {back}| {out} {back_nine} {total}"


def _nines(holes: tuple[int, ...]) -> list[int]:
    return [sum(holes[:9]), sum(holes[9:]), sum(holes)]


def card(pars: tuple[int, ...], first: Player, second: Player, title: str) -> str:
    """One pair's scorecard: hole numbers, par, each player, and the pair's best ball.

    A `*` marks the score that counted on each hole; on a tie, the first player's.
    """
    best = best_ball(first, second)

    def marked(player: Player, other: Player, counts_on_tie: bool) -> list[str]:
        return [
            _cell(mine, mine < theirs or (mine == theirs and counts_on_tie))
            for mine, theirs in zip(player.strokes, other.strokes, strict=True)
        ]

    header = _row("Hole", [_cell(n) for n in range(1, HOLES + 1)], ["Out", "In", "Tot"])
    rule = "-" * len(header)
    lines = [
        title,
        "=" * len(header),
        header,
        _row("Par", [_cell(par) for par in pars], _nines(pars)),
        rule,
        _row(first.name, marked(first, second, True), _nines(first.strokes)),
        _row(second.name, marked(second, first, False), _nines(second.strokes)),
        rule,
        _row("Best ball", [_cell(value) for value in best], _nines(best))
        + f"  {to_par(sum(best), sum(pars))}",
    ]
    return "\n".join(lines)


def leaderboard(pars: tuple[int, ...], teams: list[tuple[Player, Player]]) -> str:
    """The pairs by best-ball total, best first; tied pairs share a place."""
    scored = sorted(
        ((sum(best_ball(a, b)), f"{a.name} + {b.name}") for a, b in teams),
        key=lambda entry: entry[0],
    )
    lines = ["Leaderboard", "==========="]
    place = 0
    previous = None
    for index, (total, names) in enumerate(scored, start=1):
        if total != previous:
            place, previous = index, total
        lines.append(f"{place:>3}. {total:>3} ({to_par(total, sum(pars)):>3})  {names}")
    return "\n".join(lines)


def report(scores: Scores, matching: Matching) -> str:
    """Every pair's card, then the leaderboard, then who was left unmatched."""
    teams = [
        (matching.player(first), matching.player(second))
        for first, second in matching.pairs
    ]
    sections = [
        card(scores.pars, a, b, f"Team {index}: {a.name} + {b.name}")
        for index, (a, b) in enumerate(teams, start=1)
    ]
    if teams:
        sections.append(leaderboard(scores.pars, teams))
    left = matching.unmatched()
    if left:
        names = ", ".join(matching.player(number).name for number in left)
        sections.append(f"Not matched: {names}")
    return "\n\n".join(sections)
