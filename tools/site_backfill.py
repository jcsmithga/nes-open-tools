#!/usr/bin/env python3
"""
NES Open Tournament Golf - Randomizer site backfill

Fills in what seeds stored before migration 7 lack: every slot's hole as built in
`hole_data`, its distance, author and source, and the seed's required ROMs (ADR 0024).
Run it once, with the site stopped, in the site's environment: it reads the same
GOLF_-prefixed variables as golf-site, migrates the database, and reads the hole store
golf-rehydrate fills. The site refuses to start while any seed is unfilled. Nothing is
written unless every seed can be filled; running it again finds nothing to do.
"""

import argparse
import sys

from golf.randomizer.catalog import Catalog, CatalogError, HoleStore
from server.config import Config
from server.db import Database
from server.seeds import BackfillError, backfill


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fill in the stored holes and catalog facts of seeds made before "
        "the site kept them."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="work out every seed's holes and report, writing nothing",
    )
    args = parser.parse_args()

    config = Config.from_env()
    db = Database(config.database)
    try:
        db.migrate()
        filled = backfill(
            db, Catalog.load(), HoleStore(config.holes_dir), write=not args.dry_run
        )
    except (CatalogError, BackfillError) as error:
        print(f"error: {error}\nnothing was written", file=sys.stderr)
        return 1
    finally:
        db.close()
    verb = "would fill" if args.dry_run else "filled"
    print(f"{verb} {len(filled)} seeds" if filled else "every seed is filled in")
    for seed_id in filled:
        print(f"  {seed_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
