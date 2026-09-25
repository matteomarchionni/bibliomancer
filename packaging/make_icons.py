"""Icons for the installers, from the largest PNG in src/<pkg>/assets.

    python packaging/make_icons.py          -> build/<icon>.icns (macOS), build/<icon>.png (Linux)

The Windows .ico is committed (assets/<icon>.ico). Same script in both apps.
"""
import glob
import os
import re
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    pngs = glob.glob(os.path.join(ROOT, "src", "*", "assets", "*_icon_*.png"))
    if not pngs:
        print("no icon PNGs found")
        return 1
    size = lambda p: int(re.search(r"_(\d+)\.png$", p).group(1))  # noqa: E731
    best = max(pngs, key=size)
    stem = re.sub(r"_\d+\.png$", "", os.path.basename(best))
    out = os.path.join(ROOT, "build")
    os.makedirs(out, exist_ok=True)
    im = Image.open(best).convert("RGBA")
    if im.size[0] < 1024:
        im = im.resize((1024, 1024), Image.Resampling.LANCZOS)
    im.save(os.path.join(out, f"{stem}.icns"))
    im.resize((512, 512), Image.Resampling.LANCZOS).save(os.path.join(out, f"{stem}.png"))
    print(f"{stem}.icns and {stem}.png written to {out} (from {os.path.basename(best)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
