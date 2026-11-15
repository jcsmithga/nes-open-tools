"""The editor's macOS file dialogs, which run AppleScript through osascript."""

import subprocess

import pytest

from editor.ui import dialogs

FILETYPES = [("JSON files", "*.json"), ("All files", "*.*")]


class FakeRun:
    """Stands in for subprocess.run, recording the command it was given."""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.command: list[str] = []

    def __call__(self, command, **kwargs):
        self.command = command
        return self.result

    @property
    def script(self) -> str:
        return "\n".join(
            arg
            for prev, arg in zip(self.command, self.command[1:], strict=False)
            if prev == "-e"
        )

    @property
    def argv(self) -> list[str]:
        """The arguments after the script, which the handler sees as argv."""
        last_line = max(i for i, arg in enumerate(self.command) if arg == "-e") + 1
        return self.command[last_line + 1 :]


@pytest.fixture
def macos(monkeypatch):
    monkeypatch.setattr(dialogs.sys, "platform", "darwin")

    class NoPlyer:
        def __getattr__(self, name):
            raise AssertionError("plyer must not be used on macOS")

    monkeypatch.setattr(dialogs, "filechooser", NoPlyer())


def test_open_passes_values_as_arguments(macos, monkeypatch, tmp_path):
    run = FakeRun(stdout=f"{tmp_path}/hole.json\n")
    monkeypatch.setattr(dialogs.subprocess, "run", run)

    path = dialogs.open_file_dialog('Load "Hole"', FILETYPES, str(tmp_path))

    assert path == f"{tmp_path}/hole.json"
    assert run.command[0] == "osascript"
    assert run.argv == ['Load "Hole"', str(tmp_path)]
    assert "Hole" not in run.script
    # "All files" accepts everything, so no type filter
    assert "of type" not in run.script


def test_open_filters_by_extension_without_a_catch_all(macos, monkeypatch, tmp_path):
    run = FakeRun(stdout="/a.json\n")
    monkeypatch.setattr(dialogs.subprocess, "run", run)

    dialogs.open_file_dialog("Load", [("JSON files", "*.json")], str(tmp_path))

    assert run.argv == ["Load", str(tmp_path), "json"]
    assert "of type (items 3 thru -1 of argv)" in run.script


def test_open_skips_a_missing_directory(macos, monkeypatch, tmp_path):
    run = FakeRun(stdout="/a.json\n")
    monkeypatch.setattr(dialogs.subprocess, "run", run)

    dialogs.open_file_dialog("Load", FILETYPES, str(tmp_path / "missing"))

    assert run.argv == ["Load"]
    assert "default location" not in run.script


def test_cancel_returns_none_quietly(macos, monkeypatch, tmp_path, capsys):
    run = FakeRun(returncode=1, stderr="execution error: User canceled. (-128)")
    monkeypatch.setattr(dialogs.subprocess, "run", run)

    assert dialogs.open_file_dialog("Load", FILETYPES, str(tmp_path)) is None
    assert dialogs.save_file_dialog("Save", ".json", FILETYPES, str(tmp_path)) is None
    assert capsys.readouterr().out == ""


def test_failure_returns_none_and_reports(macos, monkeypatch, tmp_path, capsys):
    run = FakeRun(returncode=1, stderr="execution error: something else (-1700)")
    monkeypatch.setattr(dialogs.subprocess, "run", run)

    assert dialogs.open_file_dialog("Load", FILETYPES, str(tmp_path)) is None
    assert "-1700" in capsys.readouterr().out


def test_save_adds_the_default_extension(macos, monkeypatch, tmp_path):
    run = FakeRun(stdout=f"{tmp_path}/hole\n")
    monkeypatch.setattr(dialogs.subprocess, "run", run)

    path = dialogs.save_file_dialog("Save Hole", ".json", FILETYPES, str(tmp_path))

    assert path == f"{tmp_path}/hole.json"
    assert run.argv == ["Save Hole", "untitled.json", str(tmp_path)]
    assert "choose file name" in run.script


def test_save_keeps_an_existing_extension(macos, monkeypatch, tmp_path):
    monkeypatch.setattr(
        dialogs.subprocess, "run", FakeRun(stdout=f"{tmp_path}/hole.json\n")
    )

    path = dialogs.save_file_dialog("Save", ".json", FILETYPES, str(tmp_path))

    assert path == f"{tmp_path}/hole.json"
