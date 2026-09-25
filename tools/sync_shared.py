"""Keep src/<pkg>/_shared identical in BiblioMancer and PhotoMancer.

The two apps are independent (each installs on its own), so the common code
is copied, not imported from a third package. This script is the same file in
both repositories.

    python tools/sync_shared.py check    compare with the other app (exit 1 if different)
    python tools/sync_shared.py stamp    after editing _shared here: bump VERSION, write CHECKSUM
    python tools/sync_shared.py push     stamp, then copy this app's _shared to the other app

The other app's repository is found next to this one (a folder named like the
app, e.g. bibliomancer/ and photomancer/), or set SIBLING_APP=<path to it>.
A test in each app fails when _shared was edited without `stamp`.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# package -> folder names the repository may have (a clone, or the author's older checkouts)
APPS = {"bibliomancer": ("bibliomancer", "BiblioMancer", "Literature_database"),
        "photomancer": ("photomancer", "PhotoMancer", "Image_tagger")}


def shared_dir(repo: str) -> str | None:
    for pkg in APPS:
        p = os.path.join(repo, "src", pkg, "_shared")
        if os.path.isdir(p):
            return p
    return None


def files(d: str) -> list[str]:
    out = []
    for base, dirs, names in os.walk(d):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        out += [os.path.relpath(os.path.join(base, n), d).replace("\\", "/")
                for n in names if not n.endswith(".pyc") and n != "CHECKSUM"]
    return sorted(out)


def checksum(d: str) -> str:
    """sha256 over file names + contents (line endings normalised)."""
    h = hashlib.sha256()
    for f in files(d):
        h.update(f.encode())
        with open(os.path.join(d, f), "rb") as fh:
            h.update(fh.read().replace(b"\r\n", b"\n"))
    return h.hexdigest()


def recorded(d: str) -> str:
    try:
        with open(os.path.join(d, "CHECKSUM"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def sibling() -> str:
    if os.environ.get("SIBLING_APP"):
        return os.environ["SIBLING_APP"]
    mine = shared_dir(HERE)
    pkg = os.path.basename(os.path.dirname(mine))
    names = next(v for k, v in APPS.items() if k != pkg)
    for name in names:
        cand = os.path.join(os.path.dirname(HERE), name)
        if shared_dir(cand):
            return cand
    return os.path.join(os.path.dirname(HERE), names[0])


def stamp(d: str) -> None:
    init = os.path.join(d, "__init__.py")
    with open(init, encoding="utf-8") as fh:
        src = fh.read()
    if checksum(d) != recorded(d):
        m = re.search(r'VERSION = "(\d+)\.(\d+)"', src)
        new = f'VERSION = "{m.group(1)}.{int(m.group(2)) + 1}"'
        src = src.replace(m.group(0), new)
        with open(init, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(src)
        print(f"_shared {new.split('=')[1].strip()}")
    with open(os.path.join(d, "CHECKSUM"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(checksum(d) + "\n")


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    mine = shared_dir(HERE)
    other_repo = sibling()
    theirs = shared_dir(other_repo)
    if cmd == "stamp":
        stamp(mine)
        return 0
    if cmd == "push":
        stamp(mine)
        if not theirs:
            print(f"other app not found at {other_repo}")
            return 1
        shutil.rmtree(theirs)
        shutil.copytree(mine, theirs, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        print(f"copied {mine} -> {theirs}")
        return 0
    if not theirs:
        print(f"other app not found at {other_repo} (set SIBLING_APP)")
        return 1
    a, b = set(files(mine)), set(files(theirs))
    diff = sorted(a ^ b) + sorted(f for f in a & b if open(os.path.join(mine, f), "rb").read()
                                  .replace(b"\r\n", b"\n") != open(os.path.join(theirs, f), "rb")
                                  .read().replace(b"\r\n", b"\n"))
    if diff:
        print("different: " + ", ".join(diff))
        return 1
    print(f"identical ({len(a)} files, checksum {checksum(mine)[:12]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
