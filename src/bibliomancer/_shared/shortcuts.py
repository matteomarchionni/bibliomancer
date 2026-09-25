"""Optional desktop icon and Start-menu / app-menu entry, per OS.

    Windows  .lnk files on the Desktop and in Start Menu\\Programs
    macOS    a small launcher bundle ~/Applications/<App>.app (Launchpad,
             Spotlight); the "desktop icon" is an alias to it on ~/Desktop
    Linux    a .desktop entry in ~/.local/share/applications (the app menu),
             and a copy on the Desktop marked as trusted

Installers (Inno Setup, the .dmg, the AppImage) create their own; this module
serves source / pipx installs (`<app> shortcuts --desktop --menu`) and the
first-run wizard.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess

from .paths import IS_MAC, IS_WIN, desktop_dir, run_quiet


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _start_menu() -> str:
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")),
                        "Microsoft", "Windows", "Start Menu", "Programs")


def _targets(app_name: str) -> dict:
    if IS_WIN:
        return {"desktop": os.path.join(desktop_dir(), f"{app_name}.lnk"),
                "menu": os.path.join(_start_menu(), f"{app_name}.lnk")}
    if IS_MAC:
        return {"desktop": os.path.join(desktop_dir(), app_name),
                "menu": os.path.expanduser(f"~/Applications/{app_name}.app")}
    return {"desktop": os.path.join(desktop_dir(), f"{_slug(app_name)}.desktop"),
            "menu": os.path.expanduser(f"~/.local/share/applications/{_slug(app_name)}.desktop")}


def exists(app_name: str) -> dict:
    return {k: os.path.exists(p) for k, p in _targets(app_name).items()}


def _win_lnk(path, argv, workdir, icon, comment):
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); "
          "$s.TargetPath = $env:TGT; $s.Arguments = $env:ARGS; $s.WorkingDirectory = $env:WD; "
          "$s.IconLocation = $env:ICO; $s.Description = $env:DESC; $s.Save()")
    env = dict(os.environ, LNK=path, TGT=argv[0], ARGS=subprocess.list2cmdline(argv[1:]),
               WD=workdir, ICO=f"{icon},0" if icon else "", DESC=comment)
    p = subprocess.run(["powershell", "-NoProfile", "-Command", ps], env=env, capture_output=True,
                       text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        raise OSError(p.stderr.strip() or "could not create the shortcut")


def _mac_app(path, argv, workdir, icon_png, app_name):
    macos = os.path.join(path, "Contents", "MacOS")
    res = os.path.join(path, "Contents", "Resources")
    os.makedirs(macos, exist_ok=True)
    os.makedirs(res, exist_ok=True)
    exe = os.path.join(macos, "launch")
    q = lambda a: "'" + a.replace("'", "'\\''") + "'"  # noqa: E731
    with open(exe, "w", encoding="utf-8") as fh:
        fh.write(f"#!/bin/sh\ncd {q(workdir)}\nexec {' '.join(q(a) for a in argv)}\n")
    os.chmod(exe, os.stat(exe).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    icon_key = ""
    if icon_png and os.path.exists(icon_png):
        try:
            from PIL import Image
            Image.open(icon_png).save(os.path.join(res, "icon.icns"))
            icon_key = "<key>CFBundleIconFile</key><string>icon</string>"
        except Exception:
            pass
    with open(os.path.join(path, "Contents", "Info.plist"), "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<plist version="1.0"><dict>'
                 f"<key>CFBundleName</key><string>{app_name}</string>"
                 f"<key>CFBundleIdentifier</key><string>local.{_slug(app_name)}</string>"
                 "<key>CFBundleExecutable</key><string>launch</string>"
                 "<key>CFBundlePackageType</key><string>APPL</string>"
                 f"{icon_key}</dict></plist>\n")


def _linux_desktop(path, argv, workdir, icon_png, app_name, comment, trusted):
    q = lambda a: '"' + a.replace('"', '\\"') + '"' if " " in a else a  # noqa: E731
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[Desktop Entry]\nType=Application\n"
                 f"Name={app_name}\nComment={comment}\nExec={' '.join(q(a) for a in argv)}\n"
                 f"Path={workdir}\nIcon={icon_png or ''}\nTerminal=false\nCategories=Utility;\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    if trusted:
        run_quiet(["gio", "set", path, "metadata::trusted", "true"])


def create(app_name: str, argv: list[str], workdir: str, icon_png: str = "", icon_ico: str = "",
           desktop: bool = False, menu: bool = True, comment: str = "") -> list[str]:
    """Create the requested shortcuts; returns one message per shortcut."""
    t, msgs = _targets(app_name), []
    for kind, want in (("menu", menu), ("desktop", desktop)):
        if not want:
            continue
        path = t[kind]
        try:
            if IS_WIN:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _win_lnk(path, argv, workdir, icon_ico, comment)
            elif IS_MAC:
                if kind == "menu":
                    _mac_app(path, argv, workdir, icon_png, app_name)
                else:
                    if not os.path.exists(t["menu"]):
                        _mac_app(t["menu"], argv, workdir, icon_png, app_name)
                    if os.path.lexists(path):
                        os.remove(path)
                    os.symlink(t["menu"], path)
            else:
                _linux_desktop(path, argv, workdir, icon_png, app_name, comment,
                               trusted=(kind == "desktop"))
            msgs.append(f"{'Desktop icon' if kind == 'desktop' else 'Menu entry'}: {path}")
        except Exception as exc:
            msgs.append(f"could not create {path}: {exc}")
    return msgs


def remove(app_name: str) -> list[str]:
    msgs = []
    for path in _targets(app_name).values():
        try:
            if os.path.islink(path) or os.path.isfile(path):
                os.remove(path)
                msgs.append(f"removed {path}")
            elif os.path.isdir(path):
                import shutil
                shutil.rmtree(path)
                msgs.append(f"removed {path}")
        except OSError as exc:
            msgs.append(f"could not remove {path}: {exc}")
    return msgs
