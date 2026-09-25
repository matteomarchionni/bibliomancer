"""Custom Tk widgets of the panels: gradient progress bar and stat tile."""

from __future__ import annotations

import tkinter as tk

from .theme import T, hex_mix

# Segoe Fluent Icons / MDL2 glyphs (Windows 10/11 system fonts); the union of
# what both apps use. Without that font (macOS, Linux) glyphs are just skipped.
G = {"home": "", "search": "", "settings": "", "folder": "",
     "library": "", "check": "", "clock": "", "warn": "",
     "play": "", "stop": "", "sync": "", "cloud": "",
     "doc": "", "log": "", "pdf": "", "dot": "",
     "bulb": "", "photo": "", "tag": "", "flask": "",
     "report": "", "browse": "", "star": "", "list": ""}


def pick_font(root, *names):
    """First installed family of `names` (the last one is the fallback)."""
    import tkinter.font as tkf
    have = set(tkf.families(root))
    return next((n for n in names if n in have), names[-1])


def fmt_dur(s: float) -> str:
    s = int(max(0, s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    return f"{s // 3600} h {s % 3600 // 60:02d} min"


class PillBar(tk.Canvas):
    """Rounded progress bar with a gradient fill, a centred caption and an
    indeterminate 'sweep' for steps whose length is unknown."""

    def __init__(self, parent, height, bg, text_font, show_pct=True):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, bd=0)
        self.h, self.frac, self.text, self.font = height, 0.0, "", text_font
        self.show_pct, self.indet, self.phase = show_pct, False, 0.0
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, frac, text="", indeterminate=False):
        self.frac = max(0.0, min(1.0, frac or 0.0))
        self.text, self.indet = text, indeterminate
        self.draw()

    def tick(self):
        if self.indet:
            self.phase = (self.phase + 0.018) % 1.6
            self.draw()

    def _span(self, x, w, r):
        """Vertical extent of the pill at column x (rounded ends)."""
        h = self.h
        if x < r:
            d = r - x - 0.5
        elif x > w - r:
            d = x - (w - r) + 0.5
        else:
            return 0, h
        if d >= r:
            return None
        off = r - (r * r - d * d) ** 0.5
        return off, h - off

    def draw(self):
        self.delete("all")
        w = self.winfo_width()
        if w < 4:
            return
        r = self.h / 2
        if self.indet:
            seg = 0.28
            start = (self.phase - seg) * w
            x0, x1 = int(max(0, start)), int(min(w, start + seg * w))
        else:
            x0, x1 = 0, int(w * self.frac)
        for x in range(w):
            span = self._span(x, w, r)
            if span is None:
                continue
            col = hex_mix(T.BAR_A, T.BAR_B, x / max(1, w - 1)) if x0 <= x < x1 else T.SURFACE2
            self.create_line(x, span[0], x, span[1], fill=col)
        cap = self.text
        if self.show_pct and not self.indet:
            cap = f"{self.frac * 100:.0f}%" + (f"  ·  {cap}" if cap else "")
        if cap:
            self.create_text(w / 2, self.h / 2, text=cap, fill=T.BAR_TEXT, font=self.font)


class GradientTile(tk.Canvas):
    """Stat card in the icon's gradient, with a faint glyph."""

    def __init__(self, parent, title, glyph, fonts, S, bg):
        super().__init__(parent, height=S(104), bg=bg, highlightthickness=0, bd=0)
        self.title, self.glyph, self.fonts, self.S = title, glyph, fonts, S
        self.value, self.sub = "…", ""
        self.bind("<Configure>", lambda e: self.draw())

    def set(self, value, sub=""):
        self.value, self.sub = value, sub
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        r = self.S(14)
        for x in range(w):       # horizontal gradient, clipped to a rounded rectangle
            d = r - x - 0.5 if x < r else x - (w - r) + 0.5 if x > w - r else 0
            off = r - (r * r - d * d) ** 0.5 if d > 0 else 0
            self.create_line(x, off, x, h - off, fill=hex_mix(T.GRAD_A, T.GRAD_B, x / (w - 1) * 0.85))
        f_title, f_value, f_sub, f_glyph = self.fonts
        pad = self.S(18)
        if f_glyph:
            self.create_text(w - pad, h / 2, text=self.glyph, anchor="e",
                             fill=hex_mix(T.GRAD_B, T.TILE_TEXT, 0.25), font=f_glyph)
        self.create_text(pad, self.S(16), text=self.title.upper(), anchor="nw",
                         fill=T.TILE_TITLE, font=f_title)
        self.create_text(pad, self.S(38), text=self.value, anchor="nw", fill=T.TILE_TEXT,
                         font=f_value)
        if self.sub:
            self.create_text(pad, h - self.S(14), text=self.sub, anchor="sw", fill=T.TILE_SUB,
                             font=f_sub)
