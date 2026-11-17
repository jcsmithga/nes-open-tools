"""The golf-site-refit CLI: a ratings run fitted into a site database on demand."""

import subprocess
import sys
from pathlib import Path

import pytest

from golf.randomizer.catalog import Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import Settings
from server.db import Database
from tests.stored_seeds import store_seed
from tests.unit.test_server_ratings import play_all

ROOT = Path(__file__).resolve().parents[2]


def run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.site_refit", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def manifests():
    catalog, curation = Catalog.load(), CurationSnapshot.load()
    return [
        generate(catalog, curation, Settings(prng_seed=f"refit{i}")) for i in range(3)
    ]


def test_a_run_is_fitted_once_a_day_and_rates_every_seed(tmp_path, manifests):
    path = tmp_path / "site.db"
    db = Database(str(path))
    db.migrate()
    play_all(db, [store_seed(db, m) for m in manifests])
    db.close()
    first = run(path, "--day", "2026-11-18", "--min-rounds", "5")
    assert first.returncode == 0, first.stderr
    assert "3 seeds rated" in first.stdout
    again = run(path, "--day", "2026-11-18", "--min-rounds", "5")
    assert again.returncode == 1
    assert "already has a ratings run" in again.stdout


def test_too_few_rounds_fit_no_run(tmp_path):
    result = run(tmp_path / "site.db", "--day", "2026-11-18")
    assert result.returncode == 1
    assert "no run: 0 rounds" in result.stdout
