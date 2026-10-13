"""The golf-randomize CLI's generate and show subcommands, which need no ROM."""

import json
import subprocess
import sys
from pathlib import Path

from golf.core.patches.sram_defaults import Club
from golf.randomizer.catalog import US_ROM, Catalog
from golf.randomizer.curation import CurationSnapshot
from golf.randomizer.generate import generate
from golf.randomizer.manifest import (
    ClubRules,
    Manifest,
    Settings,
    standard_rules,
)
from golf.randomizer.wind import compass
from tests.legacy_manifest import as_schema

ROOT = Path(__file__).resolve().parents[2]


def run(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.randomize", *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def load(path: Path) -> Manifest:
    return Manifest.from_json(json.loads(path.read_text()))


def test_generate_writes_the_manifest_the_library_generates(tmp_path):
    path = tmp_path / "seed.json"
    completed = run("generate", "--seed", "cli-unit", "-o", path)
    assert completed.returncode == 0, completed.stderr
    expected = generate(
        Catalog.load(), CurationSnapshot.load(), Settings(prng_seed="cli-unit")
    )
    assert load(path) == expected
    assert f"wrote {path}" in completed.stdout


def test_generate_draws_a_seed_when_none_is_given(tmp_path):
    path = tmp_path / "seed.json"
    assert run("generate", "-o", path).returncode == 0
    assert load(path).settings.prng_seed


def test_every_generate_flag_lands_in_the_settings(tmp_path):
    path = tmp_path / "seed.json"
    completed = run(
        "generate",
        "-o",
        path,
        "--seed",
        "flags",
        "--par",
        "70",
        "--sources",
        US_ROM,
        "--include",
        "derived,community",
        "--exclude-tags",
        "long,scenic",
        "--allow-family-repeats",
        "--experts-per-nine",
        "2",
        "--wind-speed",
        "dying_wind",
        "--wind-direction",
        "prevailing",
        "--music",
        "nes_uk",
        "--mercy-point",
        "none",
        "--clubs-max",
        "10",
        "--banned",
        "1W",
        "--required-bag",
        "3W,PW",
    )
    assert completed.returncode == 0, completed.stderr
    settings = load(path).settings
    assert settings == Settings(
        prng_seed="flags",
        par=70,
        sources=frozenset({US_ROM}),
        include=frozenset({"derived", "community"}),
        exclude_tags=frozenset({"long", "scenic"}),
        allow_family_repeats=True,
        constraints=standard_rules(70, 2),
        wind_speed_profile="dying_wind",
        wind_direction_profile="prevailing",
        music="nes_uk",
        mercy_point=None,
        clubs=ClubRules(
            max=10,
            banned=frozenset({Club.W1}),
            required_bag=frozenset({Club.W3, Club.PW}),
        ),
    )
    assert load(path).course.par == 70


def test_the_expert_cap_flags_choose_the_cap(tmp_path):
    path = tmp_path / "seed.json"
    for args, constraints, cap in [
        ([], None, 1),
        (["--experts-per-nine", "1"], standard_rules(72, 1), 1),
        (["--experts-per-nine", "0", "--par", "71"], standard_rules(71, 0), 0),
        (["--no-expert-cap"], standard_rules(72, None), None),
    ]:
        completed = run("generate", "--seed", "rule", "-o", path, *args)
        assert completed.returncode == 0, completed.stderr
        settings = load(path).settings
        assert settings.constraints == constraints, args
        assert settings.experts_per_nine == cap, args
        shown = [
            line.removeprefix("rule: ")
            for line in completed.stdout.splitlines()
            if line.startswith("rule: ")
        ]
        assert shown == [str(rule) for rule in settings.rules], args
        assert len(shown) == (1 if cap is None else 2)


def test_show_prints_the_course(tmp_path):
    path = tmp_path / "seed.json"
    assert run("generate", "--seed", "show", "-o", path).returncode == 0
    completed = run("show", path)
    assert completed.returncode == 0, completed.stderr
    manifest = load(path)
    for slot in manifest.course.holes:
        assert str(slot.id) in completed.stdout
    assert " ".join(manifest.course.magic_words) in completed.stdout
    assert f"music: {manifest.course.music}" in completed.stdout
    assert "wind: vanilla speed, vanilla direction" in completed.stdout
    direction, speed = manifest.course.holes[0].wind
    assert f"wind {speed:>2} to {compass(direction)}" in completed.stdout


def test_show_reads_schema_one_but_build_refuses_its_historical_buildchain(tmp_path):
    current = as_schema(
        generate(
            Catalog.load(),
            CurationSnapshot.load(),
            Settings(prng_seed="schema-one", constraints=standard_rules(72, None)),
        ).to_json(),
        1,
    )
    path = tmp_path / "schema-one.json"
    path.write_text(json.dumps(current))

    shown = run("show", path)
    assert shown.returncode == 0, shown.stderr

    rom = tmp_path / "base.nes"
    rom.write_bytes(b"")
    built = run("build", rom, path, "--unfinished")
    assert built.returncode == 1
    assert "requires unfinished build version 1" in built.stderr


def test_generate_refuses_settings_the_model_refuses(tmp_path):
    path = tmp_path / "seed.json"
    for args, message in [
        (["--music", "bogus"], "music must be"),
        (["--banned", "PT"], "putter cannot be banned"),
        (["--clubs-max", "2", "--required-bag", "1W,3W"], "over the max"),
        (["--banned", "5W"], "unknown club"),
        (["--experts-per-nine", "10"], "per_nine: expected 0-9"),
        (["--no-expert-cap", "--experts-per-nine", "1"], "does not apply"),
    ]:
        completed = run("generate", "-o", path, *args)
        assert completed.returncode == 1, args
        assert completed.stderr.startswith("error:") and message in completed.stderr, (
            completed.stderr
        )
    assert not path.exists()


def test_argparse_refuses_an_unsupported_par(tmp_path):
    completed = run("generate", "-o", tmp_path / "seed.json", "--par", "69")
    assert completed.returncode == 2
    assert "invalid choice" in completed.stderr


def test_show_refuses_a_malformed_manifest(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema": 1}))
    completed = run("show", path)
    assert completed.returncode == 1
    assert completed.stderr.startswith("error:")


# -- Requests, presets and surveys --------------------------------------------------------------


def test_generate_takes_every_setting_from_a_preset(tmp_path):
    path = tmp_path / "seed.json"
    completed = run(
        "generate", "--preset", "par_3_course", "--seed", "short", "-o", path
    )
    assert completed.returncode == 0, completed.stderr
    manifest = load(path)
    assert manifest.course.layout == (3,) * 18
    assert manifest.course.par == 54
    assert manifest.settings.prng_seed == "short"
    assert [rule.name for rule in manifest.settings.constraints or ()] == [
        "par_counts",
        "expert_cap",
    ]
    assert "total: par 54" in completed.stdout
    assert 'rule: {"counts":{"3":18},"rule":"par_counts"}' in completed.stdout


def test_generate_takes_a_request_file(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "sources": [US_ROM],
                "constraints": [{"rule": "vanilla_course", "course": "nes_uk"}],
            }
        )
    )
    path = tmp_path / "seed.json"
    completed = run("generate", "--request", request, "-o", path)
    assert completed.returncode == 0, completed.stderr
    assert [str(slot.id) for slot in load(path).course.holes] == [
        f"nes_uk/{hole:02d}" for hole in range(1, 19)
    ]


def test_generate_reports_a_request_the_pool_cannot_meet(tmp_path):
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "sources": [US_ROM],
                "constraints": [{"rule": "par_counts", "counts": {"3": 18}}],
            }
        )
    )
    completed = run("generate", "--request", request, "-o", tmp_path / "seed.json")
    assert completed.returncode == 1
    assert 'the pool has 12 holes matching {"par": 3}' in completed.stderr
    unknown = run("generate", "--preset", "nonesuch", "-o", tmp_path / "seed.json")
    assert unknown.returncode == 1 and "no preset 'nonesuch'" in unknown.stderr


def test_survey_reports_what_a_preset_gives():
    completed = run("survey", "--preset", "executive", "-n", "15")
    assert completed.returncode == 0, completed.stderr
    assert "15 courses, 0 failures" in completed.stdout
    assert "course par: 58 (100%)" in completed.stdout
    mixed = run("survey", "--preset", "executive", "-n", "15", "--mix", "100")
    assert mixed.returncode == 0, mixed.stderr
    assert "course par: 58 (100%)" in mixed.stdout
    assert mixed.stdout != completed.stdout
    legacy = run("survey", "--legacy", "-n", "15")
    assert legacy.returncode == 0, legacy.stderr
    assert "course par: 72 (100%)" in legacy.stdout
    uniform = run("survey", "--preset", "natural_par", "--uniform", "-n", "5")
    assert uniform.returncode == 0, uniform.stderr
    assert "kept the rules" in uniform.stdout and "5 courses" in uniform.stdout
    rare = run("survey", "--preset", "par_3_course", "--uniform", "-n", "1")
    assert rare.returncode == 1 and "too rare" in rare.stderr


def test_rules_lists_the_vocabulary():
    completed = run("rules")
    assert completed.returncode == 0, completed.stderr
    for name in ("count", "standard", "vanilla_course", "weight"):
        assert name in completed.stdout
