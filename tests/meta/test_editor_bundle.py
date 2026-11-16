"""The bundled editor carries every data file its imports read.

PyInstaller bundles the Python it finds by following imports, but data files only when
`run_editor.spec` lists them. A file read at import time and missing from that list
breaks the bundled editor on start, while the editor run from source, with the whole
repository beside it, works. This test lays out what the bundle holds - every module of
`editor/` and `golf/`, and the spec's data files at their bundled paths - and imports
the editor from it in a fresh interpreter.
"""

import ast
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "run_editor.spec"
PACKAGES = ("editor", "golf")

#: What the bundled editor imports on start, and the quick test ROM's builder, which
#: the editor imports only from its action
MODULES = ("editor.main", "golf.core.patches.quick_test")


def spec_datas() -> list[tuple[str, str]]:
    """The `datas` list of the spec's `Analysis(...)` call."""
    for node in ast.walk(ast.parse(SPEC.read_text())):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Analysis"
        ):
            for keyword in node.keywords:
                if keyword.arg == "datas":
                    return ast.literal_eval(keyword.value)
    raise AssertionError(f"no Analysis(datas=...) in {SPEC.name}")


def lay_out_bundle(root: Path) -> None:
    for package in PACKAGES:
        for source in (REPO_ROOT / package).rglob("*.py"):
            target = root / source.relative_to(REPO_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    for pattern, destination in spec_datas():
        matches = sorted(REPO_ROOT.glob(pattern))
        assert matches, f"{SPEC.name} bundles {pattern}, which matches nothing"
        for source in matches:
            if source.is_dir():
                shutil.copytree(source, root / destination, dirs_exist_ok=True)
            else:
                (root / destination).mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, root / destination / source.name)


def test_the_bundle_imports_the_editor(tmp_path):
    lay_out_bundle(tmp_path)
    script = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        f"for name in {MODULES!r}:\n"
        "    module = importlib.import_module(name)\n"
        f"    assert module.__file__.startswith({str(tmp_path)!r}), module.__file__\n"
    )
    # -I: no repository on the path, and no working directory to fall back on
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
