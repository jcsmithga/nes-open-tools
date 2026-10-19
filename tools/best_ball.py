"""golf-best-ball: match players from a seed's scores CSV into pairs, then print each
pair's best-ball scorecard and a leaderboard.

The CSV is the admin seed page's "Download scores (CSV)". At the prompt, enter two
player numbers to pair them (`1, 2`), `undo` to unpair the last pair, and `done` to print
the cards. Logic in golf/randomizer/best_ball.py.
"""

import argparse
import sys
from pathlib import Path

from golf.randomizer.best_ball import Matching, ScoresError, read_scores, report

DONE = {"done", "d"}
UNDO = {"undo", "u"}
QUIT = {"quit", "q"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pair players from a seed's scores CSV and print best-ball cards."
    )
    parser.add_argument("csv", type=Path, help="the admin seed page's scores CSV")
    args = parser.parse_args()
    try:
        scores = read_scores(args.csv.read_text(encoding="utf-8"))
    except (OSError, ScoresError) as problem:
        print(f"golf-best-ball: {problem}", file=sys.stderr)
        return 1
    if len(scores.players) < 2:
        print(
            "golf-best-ball: fewer than two players' own rounds to pair",
            file=sys.stderr,
        )
        return 1
    if scores.skipped:
        print(f"(skipped {scores.skipped} teammate round(s): not a player's own)\n")

    matching = Matching(scores.players)
    print("Enter two numbers to pair them (1, 2), 'undo', or 'done'.\n")
    while True:
        print(matching.listing())
        try:
            line = input("\n> ").strip().lower()
        except EOFError:
            line = "done"
        if line in QUIT:
            return 0
        if line in DONE:
            break
        try:
            message = matching.undo() if line in UNDO else matching.pair(line)
        except ValueError as problem:
            message = str(problem)
        print(f"\n{message}\n")

    print()
    print(report(scores, matching))
    return 0


if __name__ == "__main__":
    sys.exit(main())
