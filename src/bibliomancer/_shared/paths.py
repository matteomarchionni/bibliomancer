"""Where things are, per operating system, and how to open or run them.

Every Windows-only call of the two panels goes through here, so the apps
themselves stay portable (Windows, macOS, Linux).

Layout rules shared by both apps
  * app_home(pkg_file)  the folder that holds the code. In a source checkout it is
    the repository root (the parent of src/); in an installed or frozen app it is
    the package folder itself.
  * portable mode: a checkout whose root holds the app's config file keeps
    everything (config, theme, and by default data) next to the code - this is how
    the apps have always run on the author's PC.
  * otherwise settings live in the per-user config folder and data in
    Documents/<App name>.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = not (IS_WIN or IS_MAC)
FROZEN = bool(getattr(sys, "frozen", False))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ------------------------------------------------------------------ folders --
def app_home(pkg_file: str) -> str:
    """Code folder: repo root for a src/ checkout, else the package folder."""
    pkg = os.path.dirname(os.path.abspath(pkg_file))
    if FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    parent = os.path.dirname(pkg)
    if os.path.basename(parent) == "src":
        return os.path.dirname(parent)
    return pkg


def src_dir(pkg_file: str) -> str | None:
    """The src/ folder of a checkout (for PYTHONPATH), None when installed."""
    parent = os.path.dirname(os.path.dirname(os.path.abspath(pkg_file)))
    return parent if os.path.basename(parent) == "src" else None


def resource(pkg_file: str, *parts: str) -> str:
    """A file shipped inside the package (icons, default configs)."""
    base = os.path.dirname(os.path.abspath(pkg_file))
    if FROZEN and hasattr(sys, "_MEIPASS"):
        base = os.path.join(sys._MEIPASS, os.path.basename(base))
    return os.path.join(base, *parts)


def user_config_dir(app: str) -> str:
    if IS_WIN:
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), app)
    if IS_MAC:
        return os.path.join(os.path.expanduser("~/Library/Application Support"), app)
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        app.lower().replace(" ", "-"))


def user_state_dir(app: str) -> str:
    """Small per-user state (panel settings): %LOCALAPPDATA%\\App on Windows."""
    if IS_WIN:
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), app)
    if IS_MAC:
        return os.path.join(os.path.expanduser("~/Library/Application Support"), app)
    return os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
                        app.lower().replace(" ", "-"))


def documents_dir() -> str:
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:   # CSIDL_PERSONAL
                return buf.value
        except Exception:
            pass
    if IS_LINUX:
        try:
            p = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True,
                               timeout=5)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def desktop_dir() -> str:
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0, None, 0, buf) == 0:   # CSIDL_DESKTOP
                return buf.value
        except Exception:
            pass
    if IS_LINUX:
        try:
            p = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True,
                               timeout=5)
            if p.returncode == 0 and p.stdout.strip():
                return p.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return os.path.join(os.path.expanduser("~"), "Desktop")


def find_config(app: str, pkg_file: str, filename: str, env: str) -> tuple[str, bool]:
    """(config path, portable?) - env var, then next to the code, then per-user."""
    if os.environ.get(env):
        return os.path.abspath(os.environ[env]), False
    home = app_home(pkg_file)
    local = os.path.join(home, filename)
    if not FROZEN and os.path.exists(local):
        return local, True
    return os.path.join(user_config_dir(app), filename), False


def looks_like_cloud_folder(path: str) -> bool:
    """True for Google Drive / OneDrive / Dropbox / iCloud mounts (SQLite can be locked there)."""
    p = path.lower().replace("\\", "/")
    return any(k in p for k in ("il mio drive", "my drive", "google drive", "googledrive",
                                "onedrive", "dropbox", "icloud", "mobile documents"))


# ------------------------------------------------------ opening things --
def open_path(path: str) -> None:
    """Open a file or folder with its default application."""
    if IS_WIN:
        os.startfile(path)                      # noqa: S606 - the user's own file
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def edit_file(path: str) -> None:
    """Open a text file for editing."""
    if IS_WIN:
        try:
            os.startfile(path, "edit")
            return
        except OSError:
            subprocess.Popen(["notepad.exe", path])
            return
    if IS_MAC:
        subprocess.Popen(["open", "-t", path])
        return
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if editor and shutil.which(editor.split()[0]) and not os.environ.get("TERM"):
        subprocess.Popen([*editor.split(), path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal(path: str) -> None:
    """Show a file selected in the file manager."""
    if IS_WIN:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif IS_MAC:
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", os.path.dirname(path) or "."])


FILE_MANAGER = "Explorer" if IS_WIN else "Finder" if IS_MAC else "the file manager"


# ------------------------------------------------------- running things --
def python_exe(windowed: bool = False) -> str:
    """The interpreter to run child jobs with (never pythonw for jobs: no stdout)."""
    exe = sys.executable
    if IS_WIN:
        low = exe.lower()
        if windowed and low.endswith("python.exe"):
            w = exe[:-10] + "pythonw.exe"
            return w if os.path.exists(w) else exe
        if not windowed and low.endswith("pythonw.exe"):
            return exe[:-11] + "python.exe"
    return exe


def self_command(pkg: str, pkg_file: str, windowed: bool = False) -> list[str]:
    """argv prefix that runs this app's command line: `<prefix> run --limit 5`.

    Frozen app: two executables side by side - <pkg> (windowed: the panel and
    what the OS scheduler starts) and <pkg>-cli (console: the jobs, whose output
    the panel reads). Checkout or installed package: `python -m <pkg>`
    (child_env() puts src/ on PYTHONPATH).
    """
    if FROZEN:
        d, ext = os.path.dirname(sys.executable), ".exe" if IS_WIN else ""
        exe = os.path.join(d, pkg + ("" if windowed else "-cli") + ext)
        return [exe if os.path.exists(exe) else sys.executable]
    if windowed:
        return [python_exe(True), "-m", pkg]
    return [python_exe(), "-u", "-m", pkg]


def launcher_command(pkg: str, pkg_file: str, launcher: str) -> list[str]:
    """For the OS scheduler, which cannot set PYTHONPATH: the checkout's root
    launcher script (e.g. BiblioMancer.pyw) if there is one, else self_command."""
    home = app_home(pkg_file)
    path = os.path.join(home, launcher)
    if not FROZEN and os.path.exists(path):
        return [python_exe(windowed=True), path]
    return self_command(pkg, pkg_file, windowed=True)


def child_env(pkg_file: str, **extra: str) -> dict:
    """Environment for child jobs: UTF-8 output and src/ importable."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    src = src_dir(pkg_file)
    if src:
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.update({k: v for k, v in extra.items() if v is not None})
    return env


def popen_kwargs() -> dict:
    """No console window on Windows; own process group elsewhere (for kill_tree)."""
    return {"creationflags": NO_WINDOW} if IS_WIN else {"start_new_session": True}


def kill_tree(pid: int) -> None:
    """Stop a job and every process it started."""
    if IS_WIN:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                       creationflags=NO_WINDOW)
        return
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass


def run_quiet(argv: list[str], timeout: float | None = None,
              console: bool = False) -> subprocess.CompletedProcess:
    """Run a command without a window. console=True: its output is in the Windows
    console code page (schtasks, taskkill...), not UTF-8 (lms, Python)."""
    enc = "oem" if console and IS_WIN else "utf-8"
    return subprocess.run(argv, capture_output=True, text=True, encoding=enc, errors="replace",
                          timeout=timeout, **({"creationflags": NO_WINDOW} if IS_WIN else {}))
