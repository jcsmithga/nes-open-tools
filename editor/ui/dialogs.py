"""
NES Open Tournament Golf - File Dialogs

Cross-platform file dialog utilities using plyer with tkinter fallback.

macOS uses AppleScript's dialogs through `osascript` instead: plyer's macOS backend
needs pyobjus, which is not a dependency, and Tk cannot run in the same process as
SDL there (Tk 9 sends its own messages to NSApp, which pygame has already made an
SDLApplication, and the process aborts).
"""

import os
import subprocess
import sys
from typing import cast

from plyer import filechooser as _filechooser
from plyer.facades import FileChooser

# plyer's proxy forwards to the platform's FileChooser but is untyped
filechooser = cast(FileChooser, _filechooser)


def get_app_directory() -> str:
    """
    Get the directory where the application is located.

    For PyInstaller bundles, this returns the directory containing the executable.
    For normal Python execution, this returns the directory containing the main script.
    """
    if getattr(sys, "frozen", False):
        # Running as PyInstaller bundle
        return os.path.dirname(sys.executable)
    else:
        # Running as normal Python script
        return os.path.dirname(os.path.abspath(sys.argv[0]))


def _run_osascript(lines: list[str], args: list[str]) -> str | None:
    """Run an AppleScript `on run argv` handler; its output, or None on cancel.

    Values are passed as arguments, not spliced into the script, so paths and
    titles need no escaping."""
    command = ["osascript"]
    for line in ["on run argv", *lines, "end run"]:
        command += ["-e", line]
    try:
        result = subprocess.run(
            [*command, *args], capture_output=True, text=True, check=False
        )
    except OSError as e:
        print(f"osascript not available for file dialog: {e}")
        return None
    if result.returncode != 0:
        # -128 is "User canceled."; anything else is worth seeing in the terminal
        if "-128" not in result.stderr:
            print(f"File dialog failed: {result.stderr.strip()}")
        return None
    path = result.stdout.strip()
    return path or None


def _accepted_extensions(filetypes: list[tuple[str, str]]) -> list[str]:
    """The extensions `filetypes` names, or [] if any pattern accepts every file
    (AppleScript's dialogs take one list of types, with no filter dropdown)."""
    extensions = []
    for _, patterns in filetypes:
        for pattern in patterns.split():
            if pattern in ("*", "*.*"):
                return []
            if pattern.startswith("*."):
                extensions.append(pattern[2:])
    return extensions


def _open_file_macos(
    title: str, filetypes: list[tuple[str, str]], initial_dir: str | None
) -> str | None:
    """AppleScript open file dialog."""
    clause = "choose file with prompt (item 1 of argv)"
    args = [title]
    if initial_dir and os.path.isdir(initial_dir):
        clause += " default location (POSIX file (item 2 of argv) as alias)"
        args.append(initial_dir)
    extensions = _accepted_extensions(filetypes)
    if extensions:
        clause += f" of type (items {len(args) + 1} thru -1 of argv)"
        args += extensions
    return _run_osascript([f"return POSIX path of ({clause})"], args)


def _save_file_macos(
    title: str, default_extension: str, initial_dir: str | None
) -> str | None:
    """AppleScript save file dialog."""
    clause = (
        "choose file name with prompt (item 1 of argv) default name (item 2 of argv)"
    )
    args = [title, f"untitled{default_extension}"]
    if initial_dir and os.path.isdir(initial_dir):
        clause += " default location (POSIX file (item 3 of argv) as alias)"
        args.append(initial_dir)
    return _run_osascript([f"return POSIX path of ({clause})"], args)


def _with_extension(path: str | None, default_extension: str) -> str | None:
    """`path` with `default_extension` appended if it lacks it."""
    if path and default_extension and not path.endswith(default_extension):
        return path + default_extension
    return path


def _open_file_tkinter(
    title: str, filetypes: list[tuple[str, str]], initial_dir: str | None
) -> str | None:
    """Tkinter fallback for open file dialog."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        path = filedialog.askopenfilename(
            title=title, filetypes=filetypes, initialdir=initial_dir
        )
        root.destroy()
        return path if path else None
    except ImportError:
        print("tkinter not available for file dialog")
        return None


def _save_file_tkinter(
    title: str,
    default_extension: str,
    filetypes: list[tuple[str, str]],
    initial_dir: str | None,
) -> str | None:
    """Tkinter fallback for save file dialog."""
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        path = filedialog.asksaveasfilename(
            title=title,
            defaultextension=default_extension,
            filetypes=filetypes,
            initialdir=initial_dir,
        )
        root.destroy()
        return path if path else None
    except ImportError:
        print("tkinter not available for file dialog")
        return None


def open_file_dialog(
    title: str,
    filetypes: list[tuple[str, str]],
    initial_dir: str | None = None,
) -> str | None:
    """
    Display an 'Open File' dialog and return the selected path.

    Args:
        title: Dialog window title
        filetypes: List of (description, pattern) tuples, e.g. [("JSON files", "*.json")]
        initial_dir: Initial directory to open. Defaults to application directory.

    Returns:
        Selected file path, or None if canceled
    """
    if initial_dir is None:
        initial_dir = get_app_directory()

    if sys.platform == "darwin":
        return _open_file_macos(title, filetypes, initial_dir)

    try:
        result = filechooser.open_file(title=title, filters=filetypes, path=initial_dir)
        if result:
            return result[0]  # plyer returns a list, we want single path
        return None
    except (OSError, NotImplementedError):
        # plyer backend not available, fall back to tkinter
        return _open_file_tkinter(title, filetypes, initial_dir)


def save_file_dialog(
    title: str,
    default_extension: str,
    filetypes: list[tuple[str, str]],
    initial_dir: str | None = None,
) -> str | None:
    """
    Display a 'Save File' dialog and return the selected path.

    Args:
        title: Dialog window title
        default_extension: Default file extension (e.g., ".json")
        filetypes: List of (description, pattern) tuples, e.g. [("JSON files", "*.json")]
        initial_dir: Initial directory to open. Defaults to application directory.

    Returns:
        Selected file path, or None if canceled
    """
    if initial_dir is None:
        initial_dir = get_app_directory()

    if sys.platform == "darwin":
        path = _save_file_macos(title, default_extension, initial_dir)
        return _with_extension(path, default_extension)

    try:
        result = filechooser.save_file(title=title, filters=filetypes, path=initial_dir)
        if result:
            # plyer returns a list
            return _with_extension(result[0], default_extension)
        return None
    except (OSError, NotImplementedError):
        # plyer backend not available, fall back to tkinter
        return _save_file_tkinter(title, default_extension, filetypes, initial_dir)
