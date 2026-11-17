"""
golf-site-refit: fit a ratings run into a site database now, as the daily rollover does,
and rate every seed it holds that is not yet rated.

    golf-site-refit [DB] [--day YYYY-MM-DD] [--min-rounds N]

For a development database with real rounds, run it on a copy of a production database:
the copy keeps its seeds and rounds, and seeds generated on it afterwards are rated as
they are stored. The database is migrated first, as the site does on startup. A day that
already has a run is left alone; pass another `--day` to fit again.
"""

import argparse
import sys
from datetime import UTC, date, datetime

from golf.randomizer.catalog import Catalog
from golf.randomizer.daily import daily_date
from golf.ratings.params import Parameters
from server.config import Config
from server.db import Database
from server.ratings import MIN_ROUNDS, Ratings, has_run, refit


def count(db: Database, sql: str) -> int:
    with db.transaction() as conn:
        return conn.execute(sql).fetchone()[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fit a ratings run into a site database now, as the rollover does."
    )
    parser.add_argument(
        "db",
        nargs="?",
        default=Config().database,
        help="the site database (default: the site's, %(default)s)",
    )
    parser.add_argument(
        "--day",
        type=date.fromisoformat,
        help="the rollover day the run is for (default: the daily open now)",
    )
    parser.add_argument(
        "--min-rounds",
        type=int,
        default=MIN_ROUNDS,
        help="write no run below this many rounds (default %(default)s)",
    )
    args = parser.parse_args(argv)
    day = args.day or daily_date(datetime.now(UTC))

    db = Database(args.db)
    try:
        before, after = db.version(), db.migrate()
        if after != before:
            print(f"migrated {args.db} from schema {before} to {after}")
        if has_run(db, day):
            print(f"{day} already has a ratings run; pass another --day to fit again")
            return 1
        ratings = Ratings(Catalog.load(), Parameters.load())
        unrated = "SELECT count(*) FROM seeds WHERE id NOT IN (SELECT seed_id FROM seed_ratings)"
        waiting = count(db, unrated)
        run_id = refit(db, ratings, day, min_rounds=args.min_rounds)
        if run_id is None:
            rounds = count(db, "SELECT count(*) FROM rounds WHERE slot = 0")
            print(
                f"no run: {rounds} rounds, fewer than {args.min_rounds} "
                "(--min-rounds lowers it)"
            )
            return 1
        rated = waiting - count(db, unrated)
        print(f"ratings run {run_id} for {day}: {rated} seeds rated")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
