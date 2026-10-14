"""golf-site-backfill: filling in seeds stored before their holes were (tools/site_backfill.py)."""

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from golf.randomizer.catalog import Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Settings
from server.config import PREFIX
from server.db import Database
from server.seeds import load_seed_holes, unfilled_seeds
from tests.stored_seeds import store_seed, unfill

ROOT = Path(__file__).resolve().parents[2]


def run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {
        name: value for name, value in os.environ.items() if not name.startswith(PREFIX)
    }
    environ = {
        "GOLF_DATABASE": str(tmp_path / "site.db"),
        "GOLF_HOLES_DIR": str(tmp_path / "no-holes"),
    }
    return subprocess.run(
        [sys.executable, "-m", "tools.site_backfill", *args],
        cwd=ROOT,
        env=env | environ,
        capture_output=True,
        text=True,
        timeout=60,
    )


def old_seed(tmp_path: Path, transformed: bool) -> str:
    """A seed as stored before migration 7. Transformed, every hole was stored, so the
    backfill needs no hole store; otherwise it needs the vanilla holes."""
    manifest = generate(
        Catalog.load(), CurationSnapshot.load(), Settings(prng_seed="backfill")
    )
    if transformed:
        slots = tuple(
            replace(slot, transforms=("mirror@1",)) for slot in manifest.course.holes
        )
        manifest = replace(manifest, course=replace(manifest.course, holes=slots))
    db = Database(str(tmp_path / "site.db"))
    db.migrate()
    seed_id = store_seed(db, manifest)
    unfill(db, seed_id, keep=tuple(range(1, 19)) if transformed else ())
    db.close()
    return seed_id


def test_fills_in_an_old_seed_and_then_finds_nothing_to_do(tmp_path):
    seed_id = old_seed(tmp_path, transformed=True)
    dry = run(tmp_path, "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert dry.stdout == f"would fill 1 seeds\n  {seed_id}\n"

    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"filled 1 seeds\n  {seed_id}\n"
    db = Database(str(tmp_path / "site.db"))
    assert unfilled_seeds(db) == []
    assert len(load_seed_holes(db, seed_id)) == 18
    db.close()

    again = run(tmp_path)
    assert (again.returncode, again.stdout) == (0, "every seed is filled in\n")


def test_writes_nothing_when_the_hole_store_lacks_a_seeds_holes(tmp_path):
    seed_id = old_seed(tmp_path, transformed=False)
    result = run(tmp_path)
    assert result.returncode == 1
    assert "not found" in result.stderr
    assert result.stderr.endswith("nothing was written\n")
    db = Database(str(tmp_path / "site.db"))
    assert unfilled_seeds(db) == [seed_id]
    db.close()
