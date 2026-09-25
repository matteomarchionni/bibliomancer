"""Tests of src/<pkg>/_shared - this file is identical in both apps.

Run from the repository root:  python -m pytest tests -q
"""

from __future__ import annotations

import datetime as dt
import importlib
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "tools"))
PKG = next(p for p in ("bibliomancer", "photomancer") if os.path.isdir(os.path.join(ROOT, "src", p)))
shared = importlib.import_module(f"{PKG}._shared")
theme = importlib.import_module(f"{PKG}._shared.theme")
settings = importlib.import_module(f"{PKG}._shared.settings")
schedule = importlib.import_module(f"{PKG}._shared.schedule")
progress = importlib.import_module(f"{PKG}._shared.progress")
embed = importlib.import_module(f"{PKG}._shared.embed")
paths = importlib.import_module(f"{PKG}._shared.paths")


def test_shared_folder_is_stamped():
    """Edited _shared without `python tools/sync_shared.py stamp` (then push to the other app)."""
    import sync_shared
    d = os.path.dirname(shared.__file__)
    assert sync_shared.checksum(d) == sync_shared.recorded(d), \
        "run: python tools/sync_shared.py push   (and commit both apps)"


def test_theme_css_overrides_and_blends(tmp_path):
    css = tmp_path / "theme.css"
    css.write_text(":root { --background: #102030; --card: rgba(255,255,255,0.5); "
                   "--font-scale: 9; --nonsense: 1; --accent: blue; }", encoding="utf-8")
    warns = theme.init({"BG": "#000000", "SURFACE": "#000000", "ACCENT": "#111111"}, "Test", str(css))
    T = theme.T
    assert T.BG == "#102030"
    assert T.SURFACE == theme.hex_mix("#102030", "#ffffff", 0.5)      # blended over the background
    assert T.FONT_SCALE == 3.0                                          # clamped
    assert T.ACCENT == "#111111" and any("not a colour" in w for w in warns)
    assert any("unknown setting --nonsense" in w for w in warns)


def test_theme_default_file_written(tmp_path):
    p = tmp_path / "sub" / "theme.css"
    theme.init({"BG": "#123456"}, "Test", str(p))
    assert "--background:" in p.read_text(encoding="utf-8") and "#123456" in theme.DEFAULT_CSS


def test_update_yaml_keeps_comments(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("# top\nroot: 'a'   # the root\nsec:\n  k: 1  # note\n", encoding="utf-8")
    settings.update_yaml(str(p), {"root": r"C:\it's", "sec.k": 5, "sec.new": True, "top2": "x"})
    txt = p.read_text(encoding="utf-8")
    assert "root: 'C:\\it''s'   # the root" in txt and "  k: 5  # note" in txt
    assert "  new: true" in txt and "top2: 'x'" in txt and txt.startswith("# top")
    cfg = settings.load_yaml(str(p), {"sec": {"k": 0, "other": 2}})
    assert cfg["sec"] == {"k": 5, "other": 2, "new": True} and cfg["root"] == "C:\\it's"


def test_update_yaml_starts_from_template(tmp_path):
    p = tmp_path / "new" / "c.yaml"
    settings.update_yaml(str(p), {"a": 1}, template="# hello\na: 0\n")
    assert p.read_text(encoding="utf-8") == "# hello\na: 1\n"


def test_task_xml_weekly_and_monthly():
    now = dt.datetime(2026, 1, 31, 14, 30)
    w = schedule.task_xml("weekly", ["C:\\py\\pythonw.exe", "C:\\a b\\App.pyw", "scheduled", "run"],
                          "C:\\a b", "desc & more", now)
    assert "<Saturday />" in w and "2026-02-07T14:30:00" in w and "desc &amp; more" in w
    assert '"C:\\a b\\App.pyw" scheduled run' in w and "<LogonType>InteractiveToken" in w
    m = schedule.task_xml("monthly", ["x"], "C:\\", "d", now)
    assert "<Day>28</Day>" in m and "2026-02-28T14:30:00" in m      # every month, February too


def test_schedule_describe():
    now = dt.datetime(2026, 9, 24, 9, 5)
    assert "every Thursday at 09:05" in schedule.describe("weekly", now)
    assert "day 24 of every month" in schedule.describe("monthly", now)


def test_progress_steps_counts_and_finish():
    class P(progress.Progress):
        PLANS = {"run": [("scan", "Scanning", 2), ("tag photos", "Tagging", 0), ("export", "Exporting", 2)]}
        COUNT_STEPS = {"tag photos": ("Photo", 1, 1.0)}

    p = P("run", hint=10)
    assert p.overall() == 0.0 and p.step_label() == "Starting"
    p.feed("[2026-09-25 10:00:00] === scan photo folders ===")
    assert p.step_label() == "Scanning" and p.step_counter() == "Step 1 of 3"
    p.feed("=== tag photos ===")
    p.set_total("tag photos", 10)
    p.set_done("tag photos", 5)
    frac, cap = p.step_fraction()
    assert abs(frac - 0.5) < 1e-9 and cap.startswith("Photo 6 of 10")
    assert 0.1 < p.overall() < 0.9
    p.feed("Report written: C:\\r.html")
    p.feed("=== pipeline finished in 1.0 min ===")
    assert p.overall() == 1.0 and p.report == "C:\\r.html"


def test_relevant_and_rank_folders():
    keys = ["a", "b", "c", "d"]
    vecs = np.eye(4, dtype=np.float32)
    q = np.array([1, 0.9, 0, 0], dtype=np.float32)
    q /= np.linalg.norm(q)
    sc = vecs @ q
    hit = embed.relevant(sc)
    assert hit.tolist() == [True, True, False, False]
    paths_ = {"a": ["x" + os.sep + "1.jpg"], "b": ["x" + os.sep + "2.jpg"], "c": ["y" + os.sep + "3.jpg"],
              "d": ["4.jpg"]}
    res = embed.rank_folders(keys, vecs, sc, q, paths_, 0)
    assert res[0]["folder"] == "x" and res[0]["hits"] == 2 and res[0]["photos"] == 2
    assert {d["folder"] for d in res} == {"x", "y", "(archive root)"}
    assert embed.folder_of("A" + os.sep + "B" + os.sep + "c.jpg", 1) == "A"


def test_paths_helpers():
    assert paths.looks_like_cloud_folder(r"G:\My Drive\Papers")
    assert not paths.looks_like_cloud_folder(r"C:\Users\me\Documents\data")
    home = paths.app_home(os.path.join(ROOT, "src", PKG, "__init__.py"))
    assert os.path.normcase(home) == os.path.normcase(ROOT)
    env = paths.child_env(os.path.join(ROOT, "src", PKG, "__init__.py"))
    assert env["PYTHONPATH"].split(os.pathsep)[0] == os.path.join(ROOT, "src")
