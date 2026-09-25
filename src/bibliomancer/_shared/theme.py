"""Colours and fonts of the panel, overridable from a CSS-style theme.css.

Tk has no CSS engine: this is a small reader for CSS custom properties
(`--background: #111111;`), applied once when the panel starts, before any
widget exists. Every colour lives on the module-level object `T` (T.BG,
T.ACCENT, ...); each app passes its own palette to init().

    from ._shared.theme import T, init
    warnings = init(PALETTE, "BiblioMancer", "/path/theme.css")
"""

from __future__ import annotations

import os
import re
import sys

# css name -> (attribute, kind, what it paints, colour it is blended over if translucent)
THEME_VARS = {
    "--background":          ("BG", "color", "window background", None),
    "--sidebar":             ("SIDEBAR", "color", "left sidebar", "BG"),
    "--card":                ("SURFACE", "color", "cards (Run, Status, Activity...)", "BG"),
    "--input":               ("SURFACE2", "color", "text fields, secondary buttons, bar track", "SURFACE"),
    "--border":              ("BORDER", "color", "card and field borders", "BG"),
    "--nav-selected":        ("NAV_SEL", "color", "selected / hovered sidebar item", "SIDEBAR"),
    "--text":                ("TEXT", "color", "main text", "SURFACE"),
    "--text-muted":          ("MUTED", "color", "labels, secondary text", "SURFACE"),
    "--text-faint":          ("FAINT", "color", "hints", "SURFACE"),
    "--accent":              ("ACCENT", "color", "primary buttons, selected options", "SURFACE"),
    "--accent-hover":        ("ACCENT_HOVER", "color", "primary buttons under the mouse", "SURFACE"),
    "--accent-light":        ("ACCENT_HI", "color", "icons, focus outline, sidebar marker", "SURFACE"),
    "--accent-line":         ("LINE", "color", "step headings in the log, RUNNING badge", "SURFACE"),
    "--selection":           ("ACCENT_LO", "color", "selected rows and text", "SURFACE"),
    "--on-accent":           ("ON_ACCENT", "color", "text on primary buttons", "ACCENT"),
    "--danger-hover":        ("DANGER_HOVER", "color", "Stop / Remove under the mouse", "SURFACE"),
    "--row-alt":             ("ROW_ALT", "color", "alternate rows in lists", "SURFACE"),
    "--tile-gradient-start": ("GRAD_A", "color", "stat cards, left", "BG"),
    "--tile-gradient-end":   ("GRAD_B", "color", "stat cards, right", "BG"),
    "--tile-text":           ("TILE_TEXT", "color", "stat card numbers", "GRAD_A"),
    "--tile-title":          ("TILE_TITLE", "color", "stat card titles", "GRAD_A"),
    "--tile-subtitle":       ("TILE_SUB", "color", "stat card small text", "GRAD_A"),
    "--bar-gradient-start":  ("BAR_A", "color", "progress bars, left", "SURFACE2"),
    "--bar-gradient-end":    ("BAR_B", "color", "progress bars, right", "SURFACE2"),
    "--bar-text":            ("BAR_TEXT", "color", "text on progress bars", "SURFACE2"),
    "--success":             ("GREEN", "color", "OK lines, DONE, LM Studio running", "SURFACE"),
    "--warning":             ("AMBER", "color", "warnings, STOPPED", "SURFACE"),
    "--error":               ("RED", "color", "errors, Stop text", "SURFACE"),
    "--font-text":           ("FONT_TEXT", "font", "most text", None),
    "--font-display":        ("FONT_DISPLAY", "font", "page titles, big numbers", None),
    "--font-mono":           ("FONT_MONO", "font", "activity log", None),
    "--font-icons":          ("FONT_ICONS", "font", "icons (leave as is unless icons break)", None),
    "--font-scale":          ("FONT_SCALE", "number", "every font size x this (1.0 = normal)", None),
    "--window-opacity":      ("WINDOW_OPACITY", "number", "whole window, 0.3-1.0 (1.0 = opaque)", None),
}

# Fonts per system; the first installed one wins. Icons use Segoe Fluent/MDL2,
# which only Windows has - elsewhere the panel simply shows no glyphs.
if sys.platform.startswith("win"):
    FONTS = {"FONT_TEXT": ["Segoe UI Variable Text", "Segoe UI"],
             "FONT_DISPLAY": ["Segoe UI Variable Display", "Segoe UI Semibold", "Segoe UI"],
             "FONT_MONO": ["Cascadia Mono", "Cascadia Code", "Consolas"],
             "FONT_ICONS": ["Segoe Fluent Icons", "Segoe MDL2 Assets"]}
elif sys.platform == "darwin":
    FONTS = {"FONT_TEXT": ["SF Pro Text", "Helvetica Neue", "Helvetica"],
             "FONT_DISPLAY": ["SF Pro Display", "Helvetica Neue", "Helvetica"],
             "FONT_MONO": ["SF Mono", "Menlo", "Monaco"],
             "FONT_ICONS": ["Segoe Fluent Icons"]}
else:
    FONTS = {"FONT_TEXT": ["Inter", "Cantarell", "Noto Sans", "DejaVu Sans"],
             "FONT_DISPLAY": ["Inter", "Cantarell", "Noto Sans", "DejaVu Sans"],
             "FONT_MONO": ["JetBrains Mono", "Noto Sans Mono", "DejaVu Sans Mono"],
             "FONT_ICONS": ["Segoe Fluent Icons"]}


class Theme:
    """Attribute bag: T.BG, T.ACCENT, T.FONT_TEXT ... (see THEME_VARS)."""

    def __init__(self):
        self.FONT_SCALE = 1.0
        self.WINDOW_OPACITY = 1.0
        for k, v in FONTS.items():
            setattr(self, k, list(v))

    def get(self, name, default=None):
        return getattr(self, name, default)


T = Theme()
DEFAULT_CSS = ""          # the app's built-in look, filled by init() (for Reset)
PATH = ""                 # theme.css in use
WARNINGS: list[str] = []

_RGBA_RE = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+%?)\s*)?\)",
                      re.I)


def hex_mix(a: str, b: str, t: float) -> str:
    """Colour `t` of the way from a to b (#rrggbb)."""
    a, b = a.lstrip("#"), b.lstrip("#")
    ca = [int(a[i:i + 2], 16) for i in (0, 2, 4)]
    cb = [int(b[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def parse_color(v: str):
    """(#rrggbb, alpha 0-1) from #rgb, #rrggbb, #rrggbbaa, rgb(), rgba(); None if invalid."""
    v = v.strip()
    m = _RGBA_RE.fullmatch(v)
    if m:
        r, g, b = (max(0, min(255, round(float(x)))) for x in m.groups()[:3])
        a = m.group(4)
        alpha = 1.0 if a is None else (float(a[:-1]) / 100 if a.endswith("%") else float(a))
        return f"#{r:02x}{g:02x}{b:02x}", max(0.0, min(1.0, alpha))
    if re.fullmatch(r"#[0-9a-fA-F]{3}", v):
        v = "#" + "".join(c * 2 for c in v[1:])
    if re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        return v.lower(), 1.0
    if re.fullmatch(r"#[0-9a-fA-F]{8}", v):
        return v[:7].lower(), int(v[7:], 16) / 255
    return None


def _css_name(attr: str) -> str:
    return next((n for n, v in THEME_VARS.items() if v[0] == attr), attr)


def default_css(title: str) -> str:
    """theme.css with the current (built-in) values, one commented line each."""
    L = ["/* ==========================================================================",
         f"   {title} - control panel theme",
         "",
         "   Edit a value, save, then press Options > Appearance > Apply (the panel",
         "   restarts). Delete a line to go back to its default; Reset rewrites this",
         "   file with the defaults.",
         "",
         "   Colours:  #rrggbb   #rgb   rgb(90, 95, 209)",
         "   See-through colours: #rrggbbaa  or  rgba(90, 95, 209, 0.6)",
         "     Tk cannot show the desktop through a single element, so a translucent",
         "     colour is blended with what is behind it in the panel (named on each",
         "     line). For a see-through window use --window-opacity.",
         "   Fonts:    a comma-separated list, first one installed wins (as in CSS)",
         "   ========================================================================== */",
         "", ":root {"]
    for name, (attr, kind, what, over) in THEME_VARS.items():
        val = getattr(T, attr, None)
        if val is None:                    # not in this app's palette
            continue
        if kind == "font":
            val = ", ".join(f'"{f}"' for f in val)
        note = what + (f"; if see-through, blends into {_css_name(over)}" if over else "")
        L.append(f"  {name + ':':<24}{str(val) + ';':<46}/* {note} */")
    L += ["}", ""]
    return "\n".join(L)


def init(palette: dict, title: str, path: str) -> list[str]:
    """Set the app's palette, then apply theme.css over it (created if missing).
    Returns human-readable warnings for the activity log."""
    global DEFAULT_CSS, PATH, WARNINGS
    for k, v in palette.items():
        setattr(T, k, list(v) if isinstance(v, list) else v)
    DEFAULT_CSS, PATH = default_css(title), path
    WARNINGS = _apply(path)
    return WARNINGS


def reset_file() -> None:
    with open(PATH, "w", encoding="utf-8") as fh:
        fh.write(DEFAULT_CSS)


def _apply(path: str) -> list[str]:
    if not os.path.exists(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(DEFAULT_CSS)
        except OSError:
            pass
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            css = re.sub(r"/\*.*?\*/", "", fh.read(), flags=re.S)
    except OSError as exc:
        return [f"theme.css not readable: {exc}"]
    found = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", css))
    warnings = [f"theme.css: unknown setting {k} - ignored" for k in found if k not in THEME_VARS]
    colours = {}
    for name, (attr, kind, _what, _over) in THEME_VARS.items():
        if name not in found:
            continue
        raw = found[name].strip()
        if kind == "color":
            c = parse_color(raw)
            if c is None:
                warnings.append(f"theme.css: {name}: '{raw}' is not a colour - default kept")
            else:
                colours[name] = c
        elif kind == "font":
            fams = [f.strip().strip("'\"") for f in raw.split(",") if f.strip().strip("'\"")]
            if fams:
                setattr(T, attr, fams)
        else:
            try:
                lo, hi = (0.5, 3.0) if attr == "FONT_SCALE" else (0.3, 1.0)
                setattr(T, attr, min(hi, max(lo, float(raw))))
            except ValueError:
                warnings.append(f"theme.css: {name}: '{raw}' is not a number - default kept")
    # opaque values first, then blend each translucent one over what sits behind it
    for name, (hexv, _a) in colours.items():
        setattr(T, THEME_VARS[name][0], hexv)
    for name, (hexv, a) in colours.items():
        attr, _k, _w, over = THEME_VARS[name]
        if a < 1.0 and over and getattr(T, over, None):
            setattr(T, attr, hex_mix(getattr(T, over), hexv, a))
    return warnings
