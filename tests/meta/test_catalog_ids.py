"""No catalog id is ever removed or given to another hole, across the index's git history.

Seeds keep the holes they were built with and what their pages show of them (ADR 0024), so
an entry's hash, par, distance or author may be corrected in place. What must hold is
what stats join on: every id ever committed is still in the index, with the same source,
and once withdrawn stays withdrawn. The index version never decreases. See
docs/catalog.md.
"""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INDEX = "data/catalog/holes.json"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def _committed_indexes() -> list[tuple[str, dict]]:
    try:
        commits = _git("log", "--format=%H", "--", INDEX).split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git history unavailable")
    return [
        (commit[:10], json.loads(_git("show", f"{commit}:{INDEX}")))
        for commit in commits
    ]


def test_no_committed_id_was_removed_or_given_another_hole():
    current = json.loads((ROOT / INDEX).read_text())
    problems = []
    for commit, past in _committed_indexes():
        if current["version"] < past["version"]:
            problems.append(
                f"{commit}: version went from {past['version']} to {current['version']}"
            )
        for hole_id, fields in past["holes"].items():
            now = current["holes"].get(hole_id)
            if now is None:
                problems.append(f"{commit}: {hole_id} was removed")
                continue
            if fields.get("withdrawn") and not now.get("withdrawn"):
                problems.append(f"{commit}: {hole_id} was un-withdrawn")
            if now["source"] != fields["source"]:
                problems.append(
                    f"{commit}: {hole_id} has another source; publish a new lineage "
                    "or version instead"
                )
    assert not problems, "\n".join(problems)
