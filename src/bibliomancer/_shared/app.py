"""The control-panel frame shared by both apps.

Everything the two panels have in common lives here: the window (DPI scaling,
icon, dark title bar, ttk styles), the sidebar and header, the Overview page
(stat tiles, Run card, Status card with two progress bars, Activity log), the
job runner (every button runs the app's own command line in a child process
and the bars are built from what it prints), the watcher that follows a
scheduled run started by the OS, the Repeat (weekly / monthly) schedule and
the theme actions.

An app subclasses DashboardApp, passes an AppSpec and provides:
    PAGES, FOLDER_LINKS, TILES           sidebar / overview content
    _page_<key>(host)                    its other pages
    run_card(card)                       buttons of the Run card
    collect_status() / show_counts(o)    numbers for the tiles (worker thread / Tk thread)
    model_check()                        (ok, message) before jobs that need the model
    busy_buttons()                       disabled while a job runs
and optionally quick_search(q), on_page_shown(key), after_job(rc), persist_values().
"""

from __future__ import annotations

import datetime as dt
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import messagebox, ttk

from . import paths, schedule, theme
from .lmstudio import server_up
from .progress import Progress
from .scheduled import END_MARK, LOG_NAME, START_MARK
from .settings import PanelSettings
from .theme import T
from .widgets import G, GradientTile, PillBar, fmt_dur, pick_font


@dataclass
class AppSpec:
    pkg: str                         # python package, e.g. "bibliomancer"
    pkg_file: str                    # the package's __init__.py (locates code and assets)
    title: str                       # window title and product name
    brand: str                       # sidebar name, may contain "\n"
    app_id: str                      # Windows AppUserModelID (taskbar identity)
    task_name: str                   # scheduled task / launchd / systemd name
    state_name: str                  # folder of gui_settings.json
    icon: str                        # assets/<icon>.ico and <icon>_<size>.png
    palette: dict                    # colours, see theme.THEME_VARS
    launcher: str                    # checkout launcher at the repo root, e.g. "BiblioMancer.pyw"
    placeholder: str = "Search…"
    size: tuple[int, int] = (1320, 880)
    min_size: tuple[int, int] = (1080, 700)
    schedule_description: str = ""
    idle_hint: str = "Press the button above to start."
    progress_cls: type = Progress
    assets_dir: str = ""
    extra: dict = field(default_factory=dict)


class DashboardApp:
    PAGES: list[tuple[str, str, str]] = []          # (key, glyph, title)
    FOLDER_LINKS: list[tuple[str, str, str]] = []   # (glyph, text, attribute or key for folder_path)
    TILES: list[tuple[str, str, str]] = []          # (key, title, glyph)
    WATCH_STALE_S = 20 * 60                          # a live run writes at least every few minutes

    # ================================================================ set-up ===
    def __init__(self, root: tk.Tk, spec: AppSpec):
        self.root, self.spec = root, spec
        self.q: queue.Queue = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.running = self.stopping = False
        self.progress: Progress | None = None
        self.watch: dict | None = None
        self.pending_hint = 0
        self.s = PanelSettings(os.path.join(paths.user_state_dir(spec.state_name),
                                            "gui_settings.json"))
        self.assets = spec.assets_dir or paths.resource(spec.pkg_file, "assets")

        self.F = pick_font(root, *T.FONT_TEXT, "TkDefaultFont")
        self.FD = pick_font(root, *T.FONT_DISPLAY, self.F)
        self.FM = pick_font(root, *T.FONT_MONO, "TkFixedFont")
        self.FI = pick_font(root, *T.FONT_ICONS, "") or None
        # Fonts are in points and follow the display scaling; pixel sizes do not.
        self.f = max(1.0, root.winfo_fpixels("1i") / 96.0)
        if T.FONT_SCALE != 1.0:
            root.tk.call("tk", "scaling", float(root.tk.call("tk", "scaling")) * T.FONT_SCALE)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = min(self.S(spec.size[0]), int(sw * 0.92)), min(self.S(spec.size[1]), int(sh * 0.9))
        root.title(spec.title)
        root.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
        root.minsize(min(self.S(spec.min_size[0]), w), min(self.S(spec.min_size[1]), h))
        root.configure(bg=T.BG)
        if T.WINDOW_OPACITY < 1.0:
            root.attributes("-alpha", T.WINDOW_OPACITY)
        self._icon()
        self._dark_titlebar()
        self._styles()
        self.sched_mode = tk.StringVar(value=self.s.get("sched", "oneshot"))

        self.setup_state()
        self._build()
        self.show_page(self.PAGES[0][0])
        self.refresh_status()
        self.log("Ready. Nothing runs until you press a button.\n", "muted")
        for w_ in theme.WARNINGS:
            self.log(w_ + "\n", "warn")
        self.root.after(100, self._drain)
        self.root.after(40, self._animate)
        self.root.after(1000, self._tick)
        root.protocol("WM_DELETE_WINDOW", self._close)

    def setup_state(self) -> None:
        """Create the app's Tk variables (called before the pages are built)."""

    def S(self, px) -> int:
        """Scale a 96-dpi pixel size to this display."""
        return int(round(px * getattr(self, "f", 1.0)))

    def SF(self, px) -> int:
        """Like S, for sizes that must grow with the theme's --font-scale."""
        return self.S(px * T.FONT_SCALE)

    # ---------------------------------------------------------- styling --
    def _icon(self):
        self.logo = None
        base = os.path.join(self.assets, self.spec.icon)
        try:
            if paths.IS_WIN and os.path.exists(base + ".ico"):
                self.root.iconbitmap(default=base + ".ico")
            sizes = [32, 40, 48, 56, 64, 72, 96]
            best = min(sizes, key=lambda s: abs(s - self.S(44)))
            png = f"{base}_{best}.png"
            if os.path.exists(png):
                self.logo = tk.PhotoImage(file=png)
                if os.path.exists(base + "_256.png"):
                    self._big_icon = tk.PhotoImage(file=base + "_256.png")
                    self.root.iconphoto(True, self._big_icon, self.logo)
        except Exception:
            pass

    def _dark_titlebar(self):
        if not paths.IS_WIN:
            return
        try:
            import ctypes
            self.root.update()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            v = ctypes.c_int(1)
            for attr in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE (current, older builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0:
                    break
            r, g, b = (int(T.BG[i:i + 2], 16) for i in (1, 3, 5))
            cap = ctypes.c_int((b << 16) | (g << 8) | r)   # caption colour = BG, as 0x00BBGGRR
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(cap),
                                                       ctypes.sizeof(cap))
        except Exception:
            pass

    def _styles(self):
        st = ttk.Style()
        st.theme_use("clam")
        st.configure(".", background=T.BG, foreground=T.TEXT, fieldbackground=T.SURFACE2,
                     bordercolor=T.BORDER, lightcolor=T.BORDER, darkcolor=T.BORDER,
                     troughcolor=T.SURFACE, font=(self.F, 10))
        st.configure("Vertical.TScrollbar", background=T.SURFACE2, troughcolor=T.SURFACE,
                     bordercolor=T.SURFACE, arrowcolor=T.MUTED, gripcount=0,
                     lightcolor=T.SURFACE2, darkcolor=T.SURFACE2)
        st.map("Vertical.TScrollbar", background=[("active", T.BORDER)])
        st.configure("Treeview", background=T.SURFACE, fieldbackground=T.SURFACE,
                     foreground=T.TEXT, bordercolor=T.SURFACE, lightcolor=T.SURFACE,
                     darkcolor=T.SURFACE, rowheight=self.SF(29), font=(self.F, 10))
        st.map("Treeview", background=[("selected", T.ACCENT_LO)],
               foreground=[("selected", T.TEXT)])
        st.configure("Treeview.Heading", background=T.SURFACE, foreground=T.FAINT,
                     bordercolor=T.BORDER, lightcolor=T.SURFACE, darkcolor=T.SURFACE,
                     font=(self.F, 9, "bold"), relief="flat", padding=6)
        st.map("Treeview.Heading", background=[("active", T.SURFACE)])
        st.configure("Dark.TCombobox", fieldbackground=T.SURFACE2, background=T.SURFACE2,
                     foreground=T.TEXT, arrowcolor=T.MUTED, bordercolor=T.BORDER,
                     lightcolor=T.SURFACE2, darkcolor=T.SURFACE2, padding=4)
        st.map("Dark.TCombobox", fieldbackground=[("readonly", T.SURFACE2)],
               foreground=[("readonly", T.TEXT)], bordercolor=[("focus", T.ACCENT_HI)])
        for opt, val in (("background", T.SURFACE2), ("foreground", T.TEXT),
                         ("selectBackground", T.ACCENT), ("selectForeground", T.ON_ACCENT)):
            self.root.option_add(f"*TCombobox*Listbox.{opt}", val)

    # --------------------------------------------------------- widgets --
    def card(self, parent, title=None, glyph=None, **pack):
        outer = tk.Frame(parent, bg=T.BORDER)
        if pack:
            outer.pack(**pack)
        inner = tk.Frame(outer, bg=T.SURFACE, padx=self.S(18), pady=self.S(14))
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        if title:
            head = tk.Frame(inner, bg=T.SURFACE)
            head.pack(fill="x", pady=(0, self.S(10)))
            if glyph and self.FI:
                tk.Label(head, text=G[glyph], bg=T.SURFACE, fg=T.ACCENT_HI,
                         font=(self.FI, 10)).pack(side="left", padx=(0, self.S(8)))
            tk.Label(head, text=title.upper(), bg=T.SURFACE, fg=T.MUTED,
                     font=(self.F, 9, "bold")).pack(side="left")
            inner.head = head
        inner.outer = outer
        return inner

    def button(self, parent, text, cmd, kind="secondary"):
        pal = {"primary": (T.ACCENT, T.ON_ACCENT, T.ACCENT_HOVER),
               "secondary": (T.SURFACE2, T.TEXT, T.BORDER),
               "danger": (T.SURFACE2, T.RED, T.DANGER_HOVER),
               "ghost": (T.SIDEBAR, T.MUTED, T.NAV_SEL)}[kind]
        b = tk.Button(parent, text=text, command=cmd, bg=pal[0], fg=pal[1],
                      activebackground=pal[2], activeforeground=pal[1], relief="flat",
                      bd=0, padx=self.S(12), pady=self.S(6), cursor="hand2",
                      highlightthickness=0, disabledforeground=T.FAINT,
                      font=(self.F, 10, "bold" if kind == "primary" else "normal"))
        b.bind("<Enter>", lambda e: b.config(bg=pal[2]) if str(b["state"]) != "disabled" else None)
        b.bind("<Leave>", lambda e: b.config(bg=pal[0]))
        return b

    def label(self, parent, text, muted=False, size=10, bold=False, bg=None, fg=None):
        return tk.Label(parent, text=text, bg=bg or T.SURFACE, fg=fg or (T.MUTED if muted else T.TEXT),
                        font=(self.F, size, "bold" if bold else "normal"), anchor="w",
                        justify="left")

    def hint(self, parent, text, size=8):
        """Small faint text that wraps to its container's width."""
        lb = self.label(parent, text, muted=True, size=size, fg=T.FAINT)
        lb.bind("<Configure>", lambda e: lb.config(wraplength=max(120, e.width - 4)))
        return lb

    def entry(self, parent, var, width=None, size=10):
        e = tk.Entry(parent, textvariable=var, bg=T.SURFACE2, fg=T.TEXT, insertbackground=T.TEXT,
                     relief="flat", bd=0, highlightthickness=1, highlightbackground=T.BORDER,
                     highlightcolor=T.ACCENT_HI, font=(self.F, size),
                     selectbackground=T.ACCENT, selectforeground=T.ON_ACCENT)
        if width:
            e.config(width=width)
        return e

    def text_box(self, parent, height=3):
        return tk.Text(parent, height=height, bg=T.SURFACE2, fg=T.TEXT, insertbackground=T.TEXT,
                       relief="flat", bd=0, highlightthickness=1, highlightbackground=T.BORDER,
                       highlightcolor=T.ACCENT_HI, font=(self.F, 10), wrap="word",
                       padx=self.S(8), pady=self.S(6), selectbackground=T.ACCENT,
                       selectforeground=T.ON_ACCENT)

    def check(self, parent, text, var, cmd=None):
        return tk.Checkbutton(parent, text=text, variable=var, bg=T.SURFACE, fg=T.TEXT,
                              selectcolor=T.SURFACE2, activebackground=T.SURFACE, command=cmd,
                              activeforeground=T.TEXT, highlightthickness=0, bd=0,
                              font=(self.F, 10), cursor="hand2")

    def segmented(self, parent, var, options, cmd=None):
        seg = tk.Frame(parent, bg=T.SURFACE2)
        for val, txt in options:
            tk.Radiobutton(seg, text=txt, value=val, variable=var, indicatoron=0,
                           bg=T.SURFACE2, fg=T.TEXT, selectcolor=T.ACCENT, activebackground=T.BORDER,
                           activeforeground=T.TEXT, relief="flat", bd=0, padx=self.S(8),
                           pady=self.S(6), font=(self.F, 10), cursor="hand2", offrelief="flat",
                           highlightthickness=0, command=cmd).pack(side="left", fill="x", expand=True)
        return seg

    def combo(self, parent, var, values=(), readonly=True, width=None):
        cb = ttk.Combobox(parent, textvariable=var, values=list(values), font=(self.F, 10),
                          style="Dark.TCombobox", state="readonly" if readonly else "normal")
        if width:
            cb.config(width=width)
        return cb

    def path_row(self, parent, label, var, browse, hint=""):
        self.label(parent, label, muted=True, size=9).pack(anchor="w", pady=(self.S(12), self.S(4)))
        row = tk.Frame(parent, bg=T.SURFACE)
        row.pack(fill="x")
        self.entry(row, var).pack(side="left", fill="x", expand=True, ipady=self.S(5))
        self.button(row, "Browse", browse).pack(side="left", padx=(self.S(8), 0))
        if hint:
            self.hint(parent, hint).pack(anchor="w", fill="x", pady=(self.S(3), 0))

    def make_tree(self, parent, columns, on_select=None, on_double=None, sort=None):
        """Treeview with a scrollbar. columns: [(key, width, title)]."""
        box = tk.Frame(parent, bg=T.SURFACE)
        box.pack(fill="both", expand=True)
        tv = ttk.Treeview(box, columns=[c[0] for c in columns], show="headings", selectmode="browse")
        last = columns[-1][0]
        for c, w, t in columns:
            tv.heading(c, text=t, anchor="w", command=(lambda c=c: sort(c)) if sort else "")
            tv.column(c, width=self.S(w), anchor="w", stretch=(c == last))
        sb = ttk.Scrollbar(box, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        tv.pack(fill="both", expand=True)
        tv.tag_configure("odd", background=T.ROW_ALT)
        tv.tag_configure("bad", foreground=T.AMBER)
        if on_select:
            tv.bind("<<TreeviewSelect>>", on_select)
        if on_double:
            tv.bind("<Double-1>", lambda e: on_double())
        return tv

    def readonly_text(self, parent, height=10):
        return tk.Text(parent, bg=T.SURFACE, fg=T.MUTED, relief="flat", bd=0, height=height,
                       font=(self.F, 9), wrap="word", highlightthickness=0)

    @staticmethod
    def set_text(widget, text):
        widget.config(state="normal")
        widget.delete("1.0", "end")
        widget.insert("end", text)
        widget.config(state="disabled")

    def scrollable(self, host):
        """(outer frame to return as the page, inner frame to fill) with a scrollbar."""
        outer = tk.Frame(host, bg=T.BG)
        canvas = tk.Canvas(outer, bg=T.BG, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=T.BG)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        def wheel(e):
            if outer.winfo_ismapped():
                step = -e.delta / (120 if paths.IS_WIN else 1) if e.delta else (-1 if e.num == 4 else 1)
                canvas.yview_scroll(int(step), "units")
        canvas.bind_all("<MouseWheel>", wheel, add="+")
        canvas.bind_all("<Button-4>", wheel, add="+")
        canvas.bind_all("<Button-5>", wheel, add="+")
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        return outer, inner

    def action_rows(self, card, rows):
        """Maintenance-style rows: [(text, hint, attr, cmd, kind)] -> button + explanation."""
        for text, hint, attr, cmd, kind in rows:
            r = tk.Frame(card, bg=T.SURFACE)
            r.pack(fill="x", pady=self.S(4))
            b = self.button(r, text, cmd, kind)
            b.config(width=22)
            b.pack(side="left")
            setattr(self, attr, b)
            lb = self.label(r, hint, muted=True, size=9)
            lb.pack(side="left", fill="x", expand=True, padx=(self.S(14), 0))
            lb.bind("<Configure>", lambda e, lb=lb: lb.config(wraplength=max(150, e.width - 4)))

    # ---------------------------------------------------------- layout --
    def _build(self):
        self._build_sidebar()
        main = tk.Frame(self.root, bg=T.BG, padx=self.S(28), pady=self.S(18))
        main.pack(side="left", fill="both", expand=True)
        self._build_header(main)
        self.pages_host = tk.Frame(main, bg=T.BG)
        self.pages_host.pack(fill="both", expand=True, pady=(self.S(16), 0))
        self.pages = {key: getattr(self, f"_page_{key}")(self.pages_host) for key, _g, _t in self.PAGES}
        self.btn_stop.config(state="disabled")

    def _build_sidebar(self):
        side = tk.Frame(self.root, bg=T.SIDEBAR, width=self.S(232), padx=self.S(14), pady=self.S(18))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        brand = tk.Frame(side, bg=T.SIDEBAR)
        brand.pack(fill="x", padx=self.S(6), pady=(0, self.S(26)))
        if self.logo:
            tk.Label(brand, image=self.logo, bg=T.SIDEBAR).pack(side="left")
        tk.Label(brand, text=self.spec.brand, bg=T.SIDEBAR, fg=T.TEXT, justify="left",
                 font=(self.FD, 12, "bold")).pack(side="left", padx=(self.S(12), 0))
        self.nav = {}
        for key, glyph, text in self.PAGES:
            self.nav[key] = self._nav_item(side, glyph, text, lambda k=key: self.show_page(k))
        if self.FOLDER_LINKS:
            tk.Label(side, text="FOLDERS", bg=T.SIDEBAR, fg=T.FAINT, font=(self.F, 8, "bold")
                     ).pack(anchor="w", padx=self.S(10), pady=(self.S(26), self.S(6)))
            for glyph, text, key in self.FOLDER_LINKS:
                self._nav_item(side, glyph, text, lambda k=key: self.open_folder_link(k), small=True)
        bottom = tk.Frame(side, bg=T.SIDEBAR)
        bottom.pack(side="bottom", fill="x")
        self.pill = tk.Label(bottom, text="LM Studio …", bg=T.NAV_SEL, fg=T.MUTED, anchor="w",
                             font=(self.F, 9, "bold"), padx=self.S(10), pady=self.S(7))
        self.pill.pack(fill="x")
        self.model_lbl = tk.Label(bottom, text="", bg=T.SIDEBAR, fg=T.FAINT, anchor="w",
                                  font=(self.F, 8), wraplength=self.S(200), justify="left")
        self.model_lbl.pack(fill="x", pady=(self.S(6), 0))

    def _nav_item(self, parent, glyph, text, cmd, small=False):
        row = tk.Frame(parent, bg=T.SIDEBAR, cursor="hand2")
        row.pack(fill="x", pady=1)
        bar = tk.Frame(row, bg=T.SIDEBAR, width=self.S(3))
        bar.pack(side="left", fill="y")
        inner = tk.Frame(row, bg=T.SIDEBAR, padx=self.S(10), pady=self.S(7 if small else 10))
        inner.pack(side="left", fill="x", expand=True)
        parts = [row, inner]
        if self.FI:
            g = tk.Label(inner, text=G[glyph], bg=T.SIDEBAR, fg=T.MUTED,
                         font=(self.FI, 10 if small else 11))
            g.pack(side="left", padx=(0, self.S(12)))
            parts.append(g)
        t = tk.Label(inner, text=text, bg=T.SIDEBAR, fg=T.MUTED if small else T.TEXT,
                     font=(self.F, 9 if small else 10, "normal" if small else "bold"))
        t.pack(side="left")
        parts.append(t)
        item = {"row": row, "bar": bar, "parts": parts, "sel": False}

        def paint(bg):
            for p in parts:
                p.config(bg=bg)
        item["paint"] = paint
        for p in parts:
            p.bind("<Button-1>", lambda e: cmd())
            p.bind("<Enter>", lambda e: paint(T.NAV_SEL) if not item["sel"] else None)
            p.bind("<Leave>", lambda e: paint(T.SIDEBAR) if not item["sel"] else None)
        return item

    def show_page(self, key):
        for page in self.pages.values():
            page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for k, item in self.nav.items():
            item["sel"] = k == key
            item["paint"](T.NAV_SEL if k == key else T.SIDEBAR)
            item["bar"].config(bg=T.ACCENT_HI if k == key else T.SIDEBAR)
        self.page_title.config(text=next(t for k, _g, t in self.PAGES if k == key))
        self.on_page_shown(key)

    def on_page_shown(self, key) -> None:
        pass

    def _build_header(self, main):
        head = tk.Frame(main, bg=T.BG)
        head.pack(fill="x")
        box = tk.Frame(head, bg=T.SURFACE2, highlightthickness=1, highlightbackground=T.BORDER)
        box.pack(side="right", anchor="n", ipady=self.S(2), padx=(self.S(16), 0))
        left = tk.Frame(head, bg=T.BG)
        left.pack(side="left", fill="x", expand=True)
        self.page_title = tk.Label(left, text="", bg=T.BG, fg=T.TEXT, font=(self.FD, 20, "bold"))
        self.page_title.pack(anchor="w")
        self.subtitle = tk.Label(left, text="", bg=T.BG, fg=T.MUTED, font=(self.F, 9), anchor="w",
                                 justify="left")
        self.subtitle.pack(anchor="w", fill="x")
        left.bind("<Configure>", lambda e: self.subtitle.config(wraplength=max(200, e.width)))
        self.hq = tk.StringVar(value=self.spec.placeholder)
        self.hq_entry = tk.Entry(box, textvariable=self.hq, bg=T.SURFACE2, fg=T.FAINT, relief="flat",
                                 bd=0, insertbackground=T.TEXT, font=(self.F, 10), width=34,
                                 highlightthickness=0)
        self.hq_entry.pack(side="left", padx=(self.S(12), 0), pady=self.S(6))
        if self.FI:
            tk.Label(box, text=G["search"], bg=T.SURFACE2, fg=T.MUTED, font=(self.FI, 10)
                     ).pack(side="left", padx=self.S(10))
        ph = self.spec.placeholder
        self.hq_entry.bind("<FocusIn>", lambda e: (self.hq.set(""), self.hq_entry.config(fg=T.TEXT))
                           if self.hq.get() == ph else None)
        self.hq_entry.bind("<FocusOut>", lambda e: (self.hq.set(ph), self.hq_entry.config(fg=T.FAINT))
                           if not self.hq.get().strip() else None)
        self.hq_entry.bind("<Return>", self._hq_go)

    def _hq_go(self, _e):
        q = self.hq.get().strip()
        if q and q != self.spec.placeholder:
            self.quick_search(q)

    def quick_search(self, q: str) -> None:
        pass

    # --------------------------------------------------------- overview --
    def _page_overview(self, host):
        page = tk.Frame(host, bg=T.BG)
        tiles = tk.Frame(page, bg=T.BG)
        tiles.pack(fill="x")
        fonts = ((self.F, 8, "bold"), (self.FD, 20, "bold"), (self.F, 8),
                 (self.FI, 26) if self.FI else None)
        self.tiles = {}
        for i, (key, name, glyph) in enumerate(self.TILES):
            t = GradientTile(tiles, name, G[glyph], fonts, self.SF, T.BG)
            t.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else self.S(14), 0))
            tiles.columnconfigure(i, weight=1, uniform="tiles")
            self.tiles[key] = t

        mid = tk.Frame(page, bg=T.BG)
        mid.pack(fill="x", pady=(self.S(16), 0))
        mid.columnconfigure(0, weight=5, uniform="mid")
        mid.columnconfigure(1, weight=7, uniform="mid")

        run = self.card(mid, "Run", "play")
        run.outer.grid(row=0, column=0, sticky="nsew", padx=(0, self.S(14)))
        self.run_card(run)
        self.label(run, "Repeat", muted=True, size=9).pack(anchor="w", pady=(self.S(12), self.S(5)))
        self.segmented(run, self.sched_mode, (("oneshot", "One-shot"), ("weekly", "Weekly"),
                                              ("monthly", "Monthly")), self._sched_hint).pack(fill="x")
        self.sched_hint = self.label(run, "", muted=True, size=8)
        self.sched_hint.pack(anchor="w", pady=(self.S(5), 0))
        self.sched_now = self.label(run, "", size=9)
        self.sched_now.pack(anchor="w", pady=(self.S(8), 0))
        self._sched_hint()

        stc = self.card(mid, "Status", "sync")
        stc.outer.grid(row=0, column=1, sticky="nsew")
        self.badge = tk.Label(stc.head, text="IDLE", bg=T.SURFACE2, fg=T.MUTED,
                              font=(self.F, 8, "bold"), padx=self.S(8), pady=self.S(2))
        self.badge.pack(side="right")
        self.elapsed = tk.Label(stc.head, text="", bg=T.SURFACE, fg=T.FAINT, font=(self.F, 9))
        self.elapsed.pack(side="right", padx=self.S(10))
        self.state_lbl = tk.Label(stc, text="Nothing running", bg=T.SURFACE, fg=T.TEXT, anchor="w",
                                  font=(self.FD, 14, "bold"))
        self.state_lbl.pack(fill="x")
        self.step_lbl = self.label(stc, self.spec.idle_hint, muted=True, size=9)
        self.step_lbl.pack(fill="x", pady=(self.S(2), self.S(10)))
        self.label(stc, "Overall", muted=True, size=8, fg=T.FAINT).pack(anchor="w")
        self.bar_all = PillBar(stc, self.SF(26), T.SURFACE, (self.F, 9, "bold"))
        self.bar_all.pack(fill="x", pady=(self.S(3), self.S(10)))
        self.label(stc, "Current step", muted=True, size=8, fg=T.FAINT).pack(anchor="w")
        self.bar_step = PillBar(stc, self.SF(18), T.SURFACE, (self.F, 8), show_pct=False)
        self.bar_step.pack(fill="x", pady=(self.S(3), self.S(6)))
        self.item_lbl = self.label(stc, "", muted=True, size=8, fg=T.FAINT)
        self.item_lbl.pack(fill="x")
        self.bar_all.set(0, "")
        self.bar_step.set(0, "")

        act = self.card(page, "Activity", "log", fill="both", expand=True, pady=(self.S(16), 0))
        clr = self.button(act.head, "Clear", lambda: self.logw.delete("1.0", "end"))
        clr.config(pady=self.S(1), font=(self.F, 9))
        clr.pack(side="right")
        body = tk.Frame(act, bg=T.SURFACE)
        body.pack(fill="both", expand=True)
        self.logw = tk.Text(body, bg=T.SURFACE, fg=T.TEXT, insertbackground=T.TEXT, relief="flat",
                            bd=0, font=(self.FM, 9), wrap="none", highlightthickness=0,
                            selectbackground=T.ACCENT_LO, height=6)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.logw.yview)
        self.logw.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.logw.pack(fill="both", expand=True)
        for tag, col in (("muted", T.MUTED), ("step", T.LINE), ("ok", T.GREEN),
                         ("warn", T.AMBER), ("err", T.RED)):
            self.logw.tag_config(tag, foreground=col)
        self.logw.tag_config("step", font=(self.FM, 9, "bold"))
        return page

    def run_buttons(self, card, primary, secondary):
        """Primary button + a row of secondary buttons ending with Stop.
        primary = (text, cmd, attr); secondary = [(text, cmd, attr)]."""
        text, cmd, attr = primary
        b = self.button(card, text, cmd, "primary")
        b.config(pady=self.S(10), font=(self.F, 11, "bold"))
        b.pack(fill="x")
        setattr(self, attr, b)
        row = tk.Frame(card, bg=T.SURFACE)
        row.pack(fill="x", pady=(self.S(8), 0))
        for i, (text, cmd, attr) in enumerate(secondary):
            b = self.button(row, text, cmd)
            b.pack(side="left", fill="x", expand=True, padx=(0, self.S(8)))
            setattr(self, attr, b)
        self.btn_stop = self.button(row, "Stop", self.stop_job, "danger")
        self.btn_stop.pack(side="left", fill="x", expand=True)

    def appearance_card(self, page, row):
        ap = self.card(page, "Appearance", "settings")
        ap.outer.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(self.S(16), 0))
        self.label(ap, "Colours, transparency and fonts live in a CSS-style file. Edit it, save, "
                       "then press Apply.", muted=True, size=9).pack(anchor="w")
        self.label(ap, theme.PATH, size=9, fg=T.FAINT).pack(anchor="w", pady=(self.S(2), self.S(8)))
        r = tk.Frame(ap, bg=T.SURFACE)
        r.pack(fill="x")
        self.button(r, "Edit theme", self.edit_theme, "primary").pack(side="left")
        self.button(r, "Apply (restarts the panel)", self.restart_panel).pack(side="left",
                                                                              padx=(self.S(8), 0))
        self.button(r, "Reset to default", self.reset_theme).pack(side="left", padx=(self.S(8), 0))
        return ap

    # ------------------------------------------------------------ status --
    def refresh_status(self):
        def work():
            out = {}
            try:
                out.update(self.collect_status())
            except Exception as exc:
                out["err"] = f"{type(exc).__name__}: {exc}"
            try:
                out.setdefault("lm", server_up(self.lm_base_url())[0])
            except Exception:
                out["lm"] = False
            out["sched"] = schedule.status(self.spec.task_name)
            self.q.put(lambda: self._show_status(out))
        threading.Thread(target=work, daemon=True).start()

    def collect_status(self) -> dict:
        """(Worker thread) numbers for the tiles."""
        return {}

    def show_counts(self, o: dict) -> None:
        """(Tk thread) fill the tiles from collect_status()'s dict."""

    def lm_base_url(self) -> str:
        return "http://127.0.0.1:1234/v1"

    def embedding_target(self) -> tuple[str, str]:
        """(lms path, embedding model) for search by meaning; ('', '') = not used."""
        return "", ""

    def ensure_semantic(self, then=None) -> None:
        """Search by meaning needs LM Studio: start its server and load the
        embedding model in the background (a fresh start has neither), then call
        then(ok, reason) on the Tk thread. Concurrent calls share one attempt."""
        waiting = getattr(self, "_sem_wait", None)
        if waiting is not None:
            if then:
                waiting.append(then)
            return
        self._sem_wait = [then] if then else []
        lms, model = self.embedding_target()

        def work():
            from .lmstudio import ensure_embedding
            try:
                ok, why = ensure_embedding(lms, self.lm_base_url(), model) if model else (False, "")
            except Exception as exc:
                ok, why = False, f"{type(exc).__name__}: {exc}"

            def done():
                calls, self._sem_wait = self._sem_wait, None
                if ok:
                    self.pill.config(text="●  LM Studio running", fg=T.GREEN)
                elif why:
                    self.log(f"search by meaning unavailable: {why}\n", "warn")
                for f in calls:
                    f(ok, why)
            self.q.put(done)
        threading.Thread(target=work, daemon=True).start()

    def semantic_retry(self, query: str, exc: Exception) -> str:
        """A query could not be embedded (LM Studio off, or its embedding model
        still loading): make sure LM Studio is ready, then run apply_filter() again.
        Once per query, so a persistent error cannot loop. Returns the reason to show."""
        if getattr(self, "_sem_retry", None) == query and getattr(self, "_sem_wait", None) is None:
            return "LM Studio is off" if "Connection" in type(exc).__name__ else type(exc).__name__
        self._sem_retry = query
        self.ensure_semantic(lambda ok, _why: ok and self.apply_filter())
        return "starting LM Studio - searching by meaning in a moment"

    def _show_status(self, o):
        self.show_counts(o)
        s = o.get("sched", "none")
        if s in ("none", "unknown", ""):
            self.sched_now.config(text="No automatic schedule.", fg=T.MUTED)
        else:
            kind, _, nxt = s.partition("|")
            self.sched_now.config(text=f"{kind.title()} schedule  ·  next run {nxt}", fg=T.GREEN)
        on = o.get("lm")
        self.pill.config(text="●  LM Studio running" if on else "●  LM Studio off",
                         fg=T.GREEN if on else T.AMBER)
        if "err" in o:
            self.log(f"status: {o['err']}\n", "err")
        self._check_watch()

    # ---------------------------------------------- scheduled-run watcher --
    def log_dir(self) -> str:
        raise NotImplementedError

    def _check_watch(self):
        """If the scheduled task is running right now, follow its log so the
        progress bars work for it too. Its process belongs to the OS scheduler,
        so the panel only watches it; the run buttons stay locked until it ends."""
        if self.running or self.watch:
            return
        path = os.path.join(self.log_dir(), LOG_NAME)
        try:
            st = os.stat(path)
            if time.time() - st.st_mtime > self.WATCH_STALE_S:
                return
            with open(path, "rb") as fh:
                fh.seek(max(0, st.st_size - 4_000_000))
                data = fh.read().decode("utf-8", "replace")
        except OSError:
            return
        i = data.rfind(START_MARK)
        if i < 0 or END_MARK in data[i:]:
            return
        self.watch = {"path": path, "pos": st.st_size, "buf": b""}
        self.running = True
        self._lock(True)
        self.progress = self.spec.progress_cls(self.scheduled_kind(), self.pending_hint)
        m = re.search(r"(\d{4})-(\d\d)-(\d\d) (\d\d):(\d\d):(\d\d)", data[max(0, i - 40):i])
        if m:
            try:
                self.progress.t0 = dt.datetime(*map(int, m.groups())).timestamp()
            except ValueError:
                pass
        for ln in data[i:].splitlines()[1:]:
            self.progress.feed(ln)
        self.badge.config(text="SCHEDULED RUN", fg=T.LINE)
        self.log(f"\nA scheduled run is going on - following {path}. "
                 "The run buttons are locked until it ends.\n", "step")
        self._render_progress()
        self.root.after(2000, self._watch_poll)

    def scheduled_kind(self) -> str:
        return "run"

    def _watch_poll(self):
        w = self.watch
        if not w:
            return
        try:
            st = os.stat(w["path"])
            with open(w["path"], "rb") as fh:
                fh.seek(w["pos"])
                chunk = fh.read()
            w["pos"] += len(chunk)
        except OSError:
            chunk, st = b"", None
        lines = (w["buf"] + chunk).split(b"\n")
        w["buf"] = lines.pop()
        rc = None
        for raw in lines:
            text = raw.decode("utf-8", "replace").rstrip("\r") + "\n"
            self.q.put(text)
            m = re.search(END_MARK + r" (-?\d+)", text)
            if m:
                rc = int(m.group(1))
        stale = st is None or time.time() - st.st_mtime > self.WATCH_STALE_S
        if rc is None and not stale:
            self.root.after(2000, self._watch_poll)
            return
        self.watch = None
        self.running = False
        if rc is None:
            self.q.put("\n--- the scheduled run stopped writing to its log (PC restarted?) - "
                       "start it again from the panel to resume ---\n")
        self.q.put(lambda: self._done(1 if rc is None else rc))

    # ---------------------------------------------------------- progress --
    def _render_progress(self):
        p = self.progress
        if not p:
            return
        self.bar_all.set(p.overall(), p.step_counter())
        frac, cap = p.step_fraction()
        if p.finished:
            self.bar_step.set(1.0, "")
        elif frac is None:
            self.bar_step.set(0, cap, indeterminate=True)
        else:
            self.bar_step.set(frac, cap)
        if not p.finished:
            self.state_lbl.config(text="In progress…")
            self.step_lbl.config(text=p.step_label())
            self.item_lbl.config(text=p.item_text())

    def _finish_progress(self, rc):
        p = self.progress
        if p:
            if rc == 0 and not self.stopping:
                p.finished = True
            self._render_progress()
            took = fmt_dur(time.time() - p.t0)
            if self.stopping:
                state, badge, col = "Stopped", "STOPPED", T.AMBER
                self.bar_step.set(0, "")
            elif rc == 0:
                state, badge, col = "Done", "DONE", T.GREEN
            else:
                state, badge, col = f"Finished with errors (exit {rc})", "ERROR", T.RED
            self.state_lbl.config(text=state)
            extra = p.summary()
            self.step_lbl.config(text=f"Took {took}{extra}. Details in the activity log.")
            if p.report and os.path.exists(p.report) and not self.stopping:
                self.step_lbl.config(text=f"Took {took}{extra}. Report: {os.path.basename(p.report)}")
                paths.open_path(p.report)
            self.item_lbl.config(text="")
            self.badge.config(text=badge, fg=col)
            self.elapsed.config(text="")
        self.progress = None

    def _animate(self):
        self.bar_step.tick()
        self.bar_all.tick()
        self.root.after(40, self._animate)

    def _tick(self):
        self._ticks = getattr(self, "_ticks", 0) + 1
        if self._ticks % 300 == 0 and not self.running:
            self.refresh_status()        # also notices a scheduled run that started
        if self.progress:
            self.elapsed.config(text=fmt_dur(time.time() - self.progress.t0))
            self._render_progress()
        self.root.after(1000, self._tick)

    # --------------------------------------------------------------- log --
    def log(self, text, tag=None):
        self.logw.insert("end", text, tag)
        self.logw.see("end")

    @staticmethod
    def line_tag(line: str) -> str | None:
        s = line.strip()
        if s.startswith("===") or " === " in s or s.endswith("==="):
            return "step"
        if " OK " in line or s.startswith("OK") or "Report written" in line or "complete" in s.lower():
            return "ok"
        if "FAIL" in line or "ERROR" in line or "Traceback" in line:
            return "err"
        if "SKIP" in line or "REVIEW" in line or "WARNING" in line:
            return "warn"
        return None

    def _drain(self):
        fed = False
        try:
            while True:
                line = self.q.get_nowait()
                # Worker threads never touch Tk: they queue a callable and the
                # main thread runs it here. Tk is not thread-safe.
                if callable(line):
                    line()
                    continue
                if self.progress:
                    self.progress.feed(line)
                    fed = True
                self.log(line if line.endswith("\n") else line + "\n", self.line_tag(line))
        except queue.Empty:
            pass
        if fed:
            self._render_progress()
        self.root.after(100, self._drain)

    # -------------------------------------------------------------- jobs --
    def cmd(self, *args) -> list[str]:
        """argv running this app's command line with `args`."""
        return [*paths.self_command(self.spec.pkg, self.spec.pkg_file), *args]

    def job_env(self) -> dict:
        return paths.child_env(self.spec.pkg_file)

    def busy_buttons(self) -> list:
        return []

    def _lock(self, busy: bool):
        for b in self.busy_buttons():
            b.config(state="disabled" if busy else "normal")

    def start(self, args, label, kind="run", hint=0):
        """Run `<app> <args>` as a job; its output feeds the log and the bars."""
        if self.running:
            messagebox.showinfo("Busy", "A job is already running.")
            return
        self.persist()
        self.running, self.stopping = True, False
        self._lock(True)
        self.btn_stop.config(state="normal")
        self.progress = self.spec.progress_cls(kind, hint, label[:1].upper() + label[1:])
        self.badge.config(text="RUNNING", fg=T.LINE)
        self.state_lbl.config(text="In progress…")
        self.step_lbl.config(text=self.progress.step_label())
        self._render_progress()
        self.show_page(self.PAGES[0][0])
        self.q.put(f"\n=== {label} ===\n")
        argv, env, cwd = self.cmd(*args), self.job_env(), paths.app_home(self.spec.pkg_file)

        def work():
            rc = -1
            try:
                self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                             errors="replace", bufsize=1, **paths.popen_kwargs())
                for line in self.proc.stdout:
                    self.q.put(line)
                rc = self.proc.wait()
                self.q.put(f"\n=== finished (exit {rc}) ===\n")
            except Exception as exc:
                self.q.put(f"\nERROR {type(exc).__name__}: {exc}\n")
            finally:
                self.proc = None
                self.running = False
                self.q.put(lambda: self._done(rc))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, rc=0):
        self._lock(False)
        self.btn_stop.config(state="disabled")
        self._finish_progress(rc)
        self.after_job(rc)
        self.refresh_status()

    def after_job(self, rc: int) -> None:
        pass

    def stop_job(self):
        if self.proc:
            self.stopping = True
            self.q.put("\n--- stopping: the item in progress is abandoned, everything already "
                       "saved is kept ---\n")
            paths.kill_tree(self.proc.pid)       # the job may have started child processes

    def model_check(self) -> tuple[bool, str]:
        return True, ""

    def _model_ok(self) -> bool:
        """Checked before any job that needs the model; explains and stops if not."""
        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            ok, msg = self.model_check()
        finally:
            self.root.config(cursor="")
        if not ok:
            messagebox.showerror("Model not available", msg + "\n\nChoose one under Options.")
            self.log(f"\n{msg}\n", "err")
        return ok

    # ------------------------------------------------------------ schedule --
    def _sched_hint(self):
        self.sched_hint.config(text=schedule.describe(self.sched_mode.get()))

    def register_schedule(self, args: list[str]) -> None:
        """If Repeat is Weekly/Monthly, (re)register the OS task running `<app> scheduled <args>`."""
        mode = self.sched_mode.get()
        if mode not in ("weekly", "monthly"):
            return
        argv = paths.launcher_command(self.spec.pkg, self.spec.pkg_file, self.spec.launcher)
        ok, msg = schedule.register(self.spec.task_name, mode, [*argv, "scheduled", *args],
                                    paths.app_home(self.spec.pkg_file),
                                    self.spec.schedule_description)
        self.log(f"\nSchedule ({mode}): {'registered' if ok else 'FAILED'} - {msg}\n",
                 "ok" if ok else "err")

    def do_unschedule(self):
        if not messagebox.askyesno("Remove schedule",
                                   f"Remove the scheduled task '{self.spec.task_name}'?"):
            return
        ok, msg = schedule.remove(self.spec.task_name)
        self.log(f"\nRemove schedule: {msg}\n", "ok" if ok else "warn")
        self.refresh_status()

    # ---------------------------------------------------------- appearance --
    def edit_theme(self):
        if not os.path.exists(theme.PATH):
            theme.reset_file()
        paths.edit_file(theme.PATH)

    def restart_panel(self):
        if self.running and not self.watch:
            messagebox.showinfo("Apply theme", "A job is running. Apply the theme when it has "
                                               "finished (or press Stop first).")
            return
        self.persist()
        argv = paths.launcher_command(self.spec.pkg, self.spec.pkg_file, self.spec.launcher)
        if not paths.FROZEN and argv[-1] == self.spec.pkg:
            argv = [*argv, "gui"]
        subprocess.Popen(argv, cwd=paths.app_home(self.spec.pkg_file),
                         env=paths.child_env(self.spec.pkg_file))
        self.root.destroy()

    def reset_theme(self):
        if not messagebox.askyesno("Reset theme", "Overwrite theme.css with the default look and "
                                                  "restart the panel?"):
            return
        theme.reset_file()
        self.restart_panel()

    # --------------------------------------------------------------- misc --
    def folder_path(self, key: str) -> str:
        """Path behind a sidebar folder link."""
        return ""

    def open_folder_link(self, key):
        self.open_path(self.folder_path(key))

    def open_path(self, path: str):
        if path and os.path.exists(path):
            paths.open_path(path)
        else:
            messagebox.showinfo("Not found", f"{path}\ndoes not exist yet.")

    def persist_values(self) -> dict:
        return {}

    def persist(self):
        self.s.save(sched=self.sched_mode.get(), **self.persist_values())

    def _close(self):
        if self.watch:                  # the scheduled run carries on by itself
            self.persist()
            self.root.destroy()
            return
        if self.running and not messagebox.askyesno(
                "Job running", "A job is running. Close the window anyway?\n"
                               "(The job is stopped; everything already saved is kept.)"):
            return
        self.stop_job()
        self.persist()
        self.root.destroy()


def run_app(app_cls, spec: AppSpec) -> None:
    """Create the window and run the panel (sharp on high-DPI, own taskbar icon)."""
    if paths.IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(spec.app_id)
        except Exception:
            pass
    root = tk.Tk()
    app_cls(root, spec)
    root.mainloop()
