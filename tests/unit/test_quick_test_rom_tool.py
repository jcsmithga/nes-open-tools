"""The editor's quick-test ROM action: finding the vanilla ROM, and where the ROM goes.

The build itself is faked here; `tests/integration/test_quick_test_rom.py` boots a real
one.
"""

import json

import pytest

from editor.tools import quick_test_rom_tool
from editor.tools.quick_test_rom_tool import ROM_SETTING, QuickTestRomTool
from golf.formats.hole_check import rom_problems
from golf.formats.hole_data import HoleData
from tests.synthetic_holes import synthetic_hole

VANILLA = b"vanilla"
OTHER = b"some other rom"


def checked_hole(number: int) -> HoleData:
    """A synthetic hole that Check passes."""
    hole = synthetic_hole(number)
    hole.metadata["scroll_limit"] = 1
    hole.metadata["tee"] = {"x": 88, "y": 200}
    assert rom_problems(hole) == []
    return hole


class FakeContext:
    def __init__(self, hole_data):
        self.hole_data = hole_data
        self.messages: list[str] = []

    def show_message(self, message):
        self.messages.append(message)


@pytest.fixture
def dialog(monkeypatch):
    """The open dialog's answers, in order; each call records its title."""
    answers: list[str | None] = []
    calls: list[str] = []

    def open_file_dialog(title, filetypes, initial_dir=None):
        calls.append(title)
        return answers.pop(0) if answers else None

    monkeypatch.setattr(quick_test_rom_tool, "open_file_dialog", open_file_dialog)
    return answers, calls


@pytest.fixture(autouse=True)
def fake_rom(monkeypatch):
    """`VANILLA` is the vanilla ROM, and building appends the hole's number to it."""
    monkeypatch.setattr(
        quick_test_rom_tool, "is_vanilla_us_rom", lambda data: data == VANILLA
    )
    monkeypatch.setattr(
        quick_test_rom_tool,
        "build_quick_test_rom",
        lambda base, hole: base + bytes([hole.metadata["hole"]]),
    )


@pytest.fixture
def paths(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "vanilla.nes").write_bytes(VANILLA)
    (tmp_path / "other.nes").write_bytes(OTHER)
    return tmp_path


def make_tool(paths) -> QuickTestRomTool:
    return QuickTestRomTool(paths / "config" / "settings.json", paths / "out")


def run(tool, hole=None) -> FakeContext:
    context = FakeContext(hole or checked_hole(3))
    tool.on_activated(context)  # pyright: ignore[reportArgumentType]
    return context


def test_first_build_asks_for_the_rom_and_remembers_it(paths, dialog):
    answers, calls = dialog
    answers.append(str(paths / "vanilla.nes"))
    tool = make_tool(paths)

    context = run(tool)
    assert (paths / "out" / "quick_test.nes").read_bytes() == VANILLA + bytes([3])
    assert context.messages == [f"Wrote {paths / 'out' / 'quick_test.nes'}"]
    settings = json.loads((paths / "config" / "settings.json").read_text())
    assert settings == {ROM_SETTING: str(paths / "vanilla.nes")}

    # The next build, by a new tool as after a restart, does not ask
    run(make_tool(paths), checked_hole(4))
    assert len(calls) == 1
    assert (paths / "out" / "quick_test.nes").read_bytes() == VANILLA + bytes([4])


def test_keeps_other_settings(paths, dialog):
    answers, _ = dialog
    answers.append(str(paths / "vanilla.nes"))
    settings_path = paths / "config" / "settings.json"
    settings_path.parent.mkdir()
    settings_path.write_text(json.dumps({"other": 1}))
    run(make_tool(paths))
    assert json.loads(settings_path.read_text()) == {
        "other": 1,
        ROM_SETTING: str(paths / "vanilla.nes"),
    }


def test_refuses_a_rom_that_is_not_vanilla(paths, dialog):
    answers, _ = dialog
    answers.append(str(paths / "other.nes"))
    context = run(make_tool(paths))
    assert context.messages == [
        "other.nes is not the vanilla NES Open Tournament Golf (USA) ROM"
    ]
    assert not (paths / "out" / "quick_test.nes").exists()
    assert not (paths / "config" / "settings.json").exists()


def test_asks_again_when_the_remembered_rom_is_gone(paths, dialog):
    answers, calls = dialog
    moved = paths / "moved.nes"
    answers += [str(paths / "vanilla.nes"), str(moved)]
    run(make_tool(paths))
    (paths / "vanilla.nes").rename(moved)

    run(make_tool(paths))
    assert len(calls) == 2
    settings = json.loads((paths / "config" / "settings.json").read_text())
    assert settings[ROM_SETTING] == str(moved)


def test_canceling_the_dialog_writes_nothing(paths, dialog):
    context = run(make_tool(paths))
    assert context.messages == []
    assert not (paths / "out" / "quick_test.nes").exists()


def test_a_hole_check_rejects_is_not_built(paths, dialog):
    _, calls = dialog
    hole = checked_hole(3)
    hole.metadata["par"] = 9
    context = run(make_tool(paths), hole)
    assert len(context.messages) == 1
    assert context.messages[0].startswith("Can't build a quick test ROM: ")
    assert "par" in context.messages[0]
    assert calls == []  # refused before asking for the ROM
    assert not (paths / "out" / "quick_test.nes").exists()


def test_build_errors_are_reported(paths, dialog, monkeypatch):
    answers, _ = dialog
    answers.append(str(paths / "vanilla.nes"))

    def overflow(base, hole):
        raise quick_test_rom_tool.BankOverflowError("too big")

    monkeypatch.setattr(quick_test_rom_tool, "build_quick_test_rom", overflow)
    context = run(make_tool(paths))
    assert context.messages == ["Can't build a quick test ROM: too big"]


def test_output_directory_from_source_is_the_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr("sys.frozen", raising=False)
    assert quick_test_rom_tool.output_directory() == tmp_path


def test_output_directory_when_bundled_is_the_executables(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", str(tmp_path / "Open Golf Editor"))
    assert quick_test_rom_tool.output_directory() == tmp_path
