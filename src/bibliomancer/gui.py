"""
gui.py - Control panel of BiblioMancer.

Launch with the "BiblioMancer" shortcut, BiblioMancer.cmd / BiblioMancer.pyw,
or `python -m bibliomancer`. The frame (sidebar, Overview, job runner, progress
bars, schedule, theme) is the one shared with PhotoMancer (_shared/app.py);
this file adds the literature pages:

  Overview   Update now / Check / Refresh scouts / Stop, repeat schedule,
             live progress of the running job, activity log
  Browse     the catalogue: search by meaning, filters, a paper's summary
  Folders    rank the library's folders by how well their papers match a search
  Research   save a top-N reading list, deep research report on the top N
  Options    ingest options, model and folders, maintenance, appearance

Every button runs the same command line as the terminal and the scheduled run.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import threading
import tkinter as tk
from collections import Counter
from tkinter import filedialog, messagebox

from . import config
from ._shared import paths, shortcuts, theme
from ._shared.app import AppSpec, DashboardApp, run_app
from ._shared.progress import Progress
from ._shared.theme import T

# --------------------------------------------------- palette (from the icon) --
PALETTE = {
    "BG": "#111111", "SIDEBAR": "#171430", "SURFACE": "#1b1936", "SURFACE2": "#252246",
    "BORDER": "#2e2a56", "NAV_SEL": "#2a2658",
    "TEXT": "#eceefc", "MUTED": "#9ca3cf", "FAINT": "#666c9c",
    "ACCENT": "#5a5fd1", "ACCENT_HOVER": "#6a70e0", "ACCENT_HI": "#8094d9", "LINE": "#acbcf8",
    "ACCENT_LO": "#2c2a5c",
    "GRAD_A": "#3d37a5", "GRAD_B": "#7d8fdc",          # icon: bottom-left -> top-right
    "BAR_A": "#4a4fc4", "BAR_B": "#8fa6e8",
    "GREEN": "#4cd6a0", "AMBER": "#f2b35a", "RED": "#f07a8a",
    "ON_ACCENT": "#ffffff", "DANGER_HOVER": "#3b2340", "ROW_ALT": "#1f1d3d",
    "TILE_TEXT": "#ffffff", "TILE_TITLE": "#dfe4ff", "TILE_SUB": "#cfd6ff", "BAR_TEXT": "#ffffff",
}
THEME_PATH = os.path.join(config.CONFIG_DIR, "theme.css")

SPEC = AppSpec(
    pkg="bibliomancer", pkg_file=config.PKG_FILE, title="BiblioMancer",
    brand="BiblioMancer", app_id="BiblioMancer.App",
    task_name="BiblioMancer Update", state_name="BiblioMancer", icon="bibliomancer_icon", palette=PALETTE,
    launcher="BiblioMancer.pyw", placeholder="Search your library…", size=(1320, 880),
    schedule_description="Literature knowledge base: ingest new papers, dedup, index, export. "
                         "Managed by BiblioMancer control panel.",
    idle_hint="Press Update now to add new papers.")


def _json(v):
    try:
        return json.loads(v or "[]")
    except ValueError:
        return []


# ============================================================ progress model ===
class KBProgress(Progress):
    """The pipeline's printed lines -> progress. Steps are announced as
    '=== <label> ===' by pipeline.py / research.py."""
    PLANS = {
        "update": [("start LM Studio server", "Starting LM Studio", 1),
                   ("unload any loaded model", "Unloading old models", 1),
                   ("load ", "Loading the model", 4),
                   ("model already loaded", "Model ready", 1),
                   ("refresh scout index", "Reading scout briefs", 2),
                   ("check incoming duplicates", "Checking incoming duplicates", 2),
                   ("ingest new papers", "Reading new papers", 0),
                   ("remove duplicates", "Removing duplicates", 3),
                   ("update semantic index", "Updating semantic search", 5),
                   ("rebuild synthesis matrix", "Rebuilding the synthesis matrix", 1),
                   ("export to synced folder", "Exporting to the synced folder", 6)],
        "research": [("start LM Studio server", "Starting LM Studio", 1),
                     ("find top papers", "Finding the top papers", 1),
                     ("unload any loaded model", "Unloading old models", 1),
                     ("load ", "Loading the model", 3),
                     ("model already loaded", "Model ready", 1),
                     ("read papers", "Reading the papers for your question", 0),
                     ("write report", "Writing the report", 12)]}
    PLANS["rebuild"] = [("back up, then clear", "Backing up and clearing", 3)] + PLANS["update"]
    # one paper weighs about as much as five quick steps
    COUNT_STEPS = {"ingest new papers": ("Paper", 5, 20.0), "read papers": ("Paper", 5, 60.0)}
    LABELS = {"plan": "Listing what an update would process", "scouts": "Reading scout briefs"}
    FINISH = ("pipeline finished", "research finished")
    RE_PAPER = re.compile(r"^\[(\d+)/(\d+)\]\s+(.*)")
    RE_SCAN = re.compile(r"(\d+) new paper\(s\) pending|Limited to (\d+) paper")
    RE_EMBED = re.compile(r"embedded (\d+)/(\d+)")
    RE_VISION = re.compile(r"page (\d+): .* via vision")

    def __init__(self, kind, hint=0, label=""):
        super().__init__(kind, hint, label)
        self.embed = None

    def _count_key(self) -> str:
        k = self.current_key()
        return k if k in self.COUNT_STEPS else next(
            (s[0] for s in self.plan if s[0] in self.COUNT_STEPS), "ingest new papers")

    def parse(self, s):
        m = self.RE_SCAN.search(s)
        if m:
            n = int(m.group(1) or m.group(2))
            for k in self.COUNT_STEPS:
                self.set_total(k, n)
            return
        m = self.RE_PAPER.search(s)
        if m:
            self.item_started(self._count_key(), os.path.basename(m.group(3).strip()),
                              int(m.group(1)), int(m.group(2)))
            return
        if re.match(r"(OK|FAIL|SKIP)\b", s) and self.item_t0 is not None:
            m = re.match(r"OK\s+([\d.]+)s", s)
            self.item_finished(self._count_key(), float(m.group(1)) if m else None, s.startswith("OK"))
            return
        m = self.RE_VISION.search(s)
        if m and self.item:
            self.item_note = f"scanned PDF, reading page {m.group(1)} with vision"
            return
        m = self.RE_EMBED.search(s)
        if m:
            self.embed = (int(m.group(1)), int(m.group(2)))

    def extra_fraction(self, key):
        if key == "update semantic index" and self.embed:
            a, b = self.embed
            return (a / b if b else 1.0), f"Embedded {a} of {b}"
        return super().extra_fraction(key)

    def summary(self):
        n = sum(self.done.values())
        return f"  ·  {n} paper(s) read" if n else ""


SPEC.progress_cls = KBProgress


# ==================================================================== app ===
class App(DashboardApp):
    PAGES = [("overview", "home", "Overview"), ("browse", "browse", "Browse"),
             ("folders", "folder", "Folders"), ("research", "bulb", "Research"),
             ("options", "settings", "Options")]
    FOLDER_LINKS = [("doc", "Notes", "NOTES_DIR"), ("library", "PDF library", "PAPERS_DIR"),
                    ("cloud", "Export folder", "EXPORT_DIR"), ("bulb", "Research", "RESEARCH_DIR"),
                    ("report", "Reports", "REPORT_DIR"), ("log", "Logs", "LOG_DIR")]
    TILES = [("pdfs", "PDFs in library", "library"), ("cat", "Catalogued", "check"),
             ("pending", "Pending", "clock"), ("fail", "Given up", "warn")]

    def __init__(self, root, spec=SPEC):
        super().__init__(root, spec)

    # ---------------------------------------------------------- state --
    def setup_state(self):
        s = self.s
        self._load_kb()
        self.prompt_path = tk.StringVar(value=s.get("prompt", ""))
        self.limit = tk.StringVar(value=str(s.get("limit", 0)))
        self.minutes = tk.StringVar(value=str(s.get("minutes", 0)))
        self.skip_supp = tk.BooleanVar(value=s.get("skip_supp", False))
        self.retry = tk.BooleanVar(value=s.get("retry", True))
        self.vision = tk.BooleanVar(value=s.get("vision", True))
        self.top_n = tk.StringVar(value=str(s.get("top_n", 10)))
        self.query = tk.StringVar()
        self.semantic = tk.BooleanVar(value=True)
        self.field_f = tk.StringVar(value="All fields")
        self.only_fail = tk.BooleanVar(value=False)
        self.fquery = tk.StringVar()
        self.flevel = tk.StringVar(value="Paper folder")
        self.rquery = tk.StringVar()
        self.doc_tokens = tk.StringVar(value=str(self.kb.MAX_DOC_TOKENS))
        self.max_pages = tk.StringVar(value=str(self.kb.MAX_PAGES))
        self.parallel = tk.StringVar(value=str(self.kb.PARALLEL))
        self.papers_dir = tk.StringVar(value=self.kb.PAPERS_DIR)
        self.export_dir = tk.StringVar(value=self.kb.EXPORT_DIR)
        self.data_dir = tk.StringVar(value=self.kb.KB_HOME)
        self.rows: list[dict] = []
        self.row_for_iid: dict[str, dict] = {}
        self.sidx = None
        self.folder_res: list[dict] = []
        self.folder_for_iid: dict[str, dict] = {}
        self.top_rows: dict[str, dict] = {}

    def _load_kb(self):
        """(Re)import the core for the current config.yaml."""
        from . import core
        self.kb = importlib.reload(core)
        from . import embed, research
        self.embed_mod, self.research = embed, research
        self.REPORT_DIR = os.path.join(self.kb.KB_HOME, "Reports")
        if hasattr(self, "subtitle"):
            self._set_subtitle()

    def _set_subtitle(self):
        self.subtitle.config(text=f"Your literature knowledge base  ·  {self.kb.PAPERS_DIR}")
        self.model_lbl.config(text=f"Model  {self.kb.MODEL_NAME}")

    def _build(self):
        super()._build()
        self._set_subtitle()

    def persist_values(self):
        return {"prompt": self.prompt_path.get(), "limit": self.limit.get(),
                "minutes": self.minutes.get(), "skip_supp": self.skip_supp.get(),
                "retry": self.retry.get(), "vision": self.vision.get(),
                "top_n": self.top_n.get(), "question": self._question()}

    def folder_path(self, key):
        return self.REPORT_DIR if key == "REPORT_DIR" else getattr(self.kb, key, "")

    def log_dir(self):
        return self.kb.LOG_DIR

    def lm_base_url(self):
        return self.kb.LM_BASE

    def scheduled_kind(self):
        return "update"

    def busy_buttons(self):
        return [self.btn_update, self.btn_plan, self.btn_scouts, self.btn_rebuild,
                self.btn_unsched, self.btn_research, self.btn_report, self.btn_check,
                self.btn_index]

    def quick_search(self, q):
        self.query.set(q)
        self.show_page("browse")
        self.apply_filter()

    def on_page_shown(self, key):
        if key in ("browse", "folders") and not self.rows:
            self.load_catalog()

    # ---------------------------------------------------------- status --
    def collect_status(self):
        kb, out = self.kb, {}
        kb.init_db()                     # a fresh install has no tables yet
        conn = kb.connect()
        out["cat"] = conn.execute("SELECT COUNT(*) FROM processed_papers").fetchone()[0]
        out["fail"] = conn.execute("SELECT COUNT(*) FROM failures WHERE attempts >= ?",
                                   (kb.MAX_ATTEMPTS,)).fetchone()[0]
        library = bool(kb.PAPERS_DIR) and os.path.isdir(kb.PAPERS_DIR)
        out["pending"] = len(kb.pending_papers(conn)) if library else None
        conn.close()
        out["pdfs"] = len(kb.iter_pdfs()) if library else None
        return out

    def show_counts(self, o):
        pdfs, cat = o.get("pdfs"), o.get("cat")
        self.pending_hint = o.get("pending") or 0
        where = os.path.basename(self.kb.PAPERS_DIR.rstrip("\\/")) if self.kb.PAPERS_DIR else ""
        self.tiles["pdfs"].set(str(pdfs if pdfs is not None else "–"),
                               f"in {where}" if where else "set the PDF folder in Options")
        pct = f"{cat / pdfs * 100:.0f}% of the library" if pdfs and cat is not None else ""
        self.tiles["cat"].set(str(cat if cat is not None else "–"), pct)
        pend = o.get("pending")
        self.tiles["pending"].set(str(pend if pend is not None else "–"),
                                  "read at the next update" if pend else "all caught up")
        fail = o.get("fail")
        self.tiles["fail"].set(str(fail if fail is not None else "–"),
                               "tick Retry failed to try again" if fail else "nothing stuck")

    # --------------------------------------------------------- run card --
    def run_card(self, run):
        self.run_buttons(run, ("Update now", self.do_update, "btn_update"),
                         [("Check", self.do_plan, "btn_plan"),
                          ("Refresh scouts", self.do_scouts, "btn_scouts")])

    # ------------------------------------------------------------ browse --
    def _page_browse(self, host):
        page = tk.Frame(host, bg=T.BG)
        page.columnconfigure(0, weight=7, uniform="br")
        page.columnconfigure(1, weight=4, uniform="br")
        page.rowconfigure(0, weight=1)
        lst = self.card(page, "Library", "library")
        lst.outer.grid(row=0, column=0, sticky="nsew", padx=(0, self.S(14)))
        bar = tk.Frame(lst, bg=T.SURFACE)
        bar.pack(fill="x")
        e = self.entry(bar, self.query, size=11)
        e.pack(side="left", fill="x", expand=True, ipady=self.S(6))
        e.bind("<Return>", lambda ev: self.apply_filter())
        self.field_box = self.combo(bar, self.field_f, ["All fields"], width=24)
        self.field_box.pack(side="left", padx=(self.S(8), 0), ipady=self.S(3))
        self.field_box.bind("<<ComboboxSelected>>", lambda e: self.apply_filter())
        self.button(bar, "Search", self.apply_filter, "primary").pack(side="left", padx=(self.S(8), 0))
        opts = tk.Frame(lst, bg=T.SURFACE)
        opts.pack(fill="x", pady=(self.S(10), self.S(10)))
        self.check(opts, "By meaning (untick for exact words)", self.semantic,
                   self.apply_filter).pack(side="left")
        self.check(opts, "Failed / given up only", self.only_fail, self.apply_filter).pack(
            side="left", padx=(self.S(16), 0))
        self.button(opts, "Reload", self.load_catalog).pack(side="right")
        self.tree = self.make_tree(lst, [("match", 70, "Match"), ("year", 70, "Year"),
                                         ("title", 700, "Title")],
                                   on_select=self.on_select, on_double=self.open_note)
        self.browse_note = self.hint(lst, 'Search by meaning, e.g. "reefs recovering after '
                                          'heatwaves". Double-click opens the note.')
        self.browse_note.pack(anchor="w", fill="x", pady=(self.S(8), 0))

        pv = self.card(page, "Paper", "doc")
        pv.outer.grid(row=0, column=1, sticky="nsew")
        self.pv_title = self.label(pv, "Select a paper.", bold=True, size=11)
        self.pv_title.pack(fill="x")
        self.pv_meta = self.label(pv, "", muted=True, size=9)
        self.pv_meta.pack(fill="x", pady=(self.S(4), 0))
        self.pv_text = self.readonly_text(pv, height=12)
        self.pv_text.pack(fill="both", expand=True, pady=(self.S(8), 0))
        prow = tk.Frame(pv, bg=T.SURFACE)
        prow.pack(fill="x", pady=(self.S(8), 0))
        self.button(prow, "Open note", self.open_note).pack(side="left")
        self.button(prow, "Open PDF", self.open_pdf).pack(side="left", padx=(self.S(8), 0))
        self.button(prow, "Show in folder", self.reveal_pdf).pack(side="left", padx=(self.S(8), 0))
        pv.bind("<Configure>", lambda e: [w.config(wraplength=max(100, e.width - self.S(10)))
                                          for w in (self.pv_title, self.pv_meta)])
        return page

    def load_catalog(self):
        self.browse_note.config(text="Loading the catalogue…", fg=T.MUTED)

        def work():
            rows, err, idx = [], "", None
            try:
                conn = self.kb.connect()
                cat = [dict(r) for r in conn.execute("SELECT * FROM processed_papers")]
                fails = [dict(r) for r in conn.execute("SELECT * FROM failures")]
                conn.close()
                for r in cat:
                    r["_failed"] = False
                    r["_fields"] = _json(r["research_fields"])
                    r["_text"] = " ".join([r["title"] or "", r["authors"] or "", r["keywords"] or "",
                                           r["research_fields"] or "", r["core_takeaway"] or "",
                                           r["rel_path"] or ""]).lower()
                done = {r["file_hash"] for r in cat}
                failed = [{"file_hash": f["file_hash"], "rel_path": f["rel_path"],
                           "title": os.path.basename(f["rel_path"] or ""), "year": "",
                           "_failed": True, "_fields": [], "_text": (f["rel_path"] or "").lower(),
                           "reason": f["reason"], "attempts": f["attempts"]}
                          for f in fails if f["file_hash"] not in done]
                rows = cat + failed
                try:
                    idx = self.embed_mod.SearchIndex({r["file_hash"]: r for r in cat})
                    idx = idx if len(idx) else None
                except Exception as exc:
                    msg = f"search index: {exc}\n"
                    self.q.put(lambda: self.log(msg, "warn"))
            except Exception as exc:
                err = f"Could not load the catalogue: {exc}"
            self.q.put(lambda: self._catalog_loaded(rows, err, idx))
        threading.Thread(target=work, daemon=True).start()

    def _catalog_loaded(self, rows, err, idx):
        self.rows, self.sidx = rows, idx
        fields = Counter(f for r in rows for f in r["_fields"])
        self.field_box["values"] = ["All fields"] + [f for f, _n in fields.most_common(60)]
        if err:
            self.browse_note.config(text=err, fg=T.AMBER)
        self.apply_filter()
        if self.fquery.get().strip():
            self.search_folders()

    def _semantic(self, q):
        """((scores by file_hash, hit set), '') or (None, reason)."""
        if self.sidx is None:
            return None, "semantic index not available"
        try:
            import numpy as np
            from ._shared.embed import relevant
            sc = self.sidx.scores(q)
            hit = relevant(sc)
            return ({h: float(sc[i]) for i, h in enumerate(self.sidx.keys)},
                    {self.sidx.keys[i] for i in np.nonzero(hit)[0]}), ""
        except Exception as exc:
            return None, "LM Studio is off" if "Connection" in type(exc).__name__ else type(exc).__name__

    def apply_filter(self):
        q = self.query.get().strip()
        if q == self.spec.placeholder:
            q = ""
        fld = self.field_f.get()
        sem, why = self._semantic(q) if q and self.semantic.get() else (None, "exact words")
        scores, hits = sem if sem else ({}, set())
        words = re.findall(r"\w+", q.lower())
        out = []
        for r in self.rows:
            if self.only_fail.get() and not r["_failed"]:
                continue
            if fld != "All fields" and fld not in r["_fields"]:
                continue
            if q and sem is not None and r["file_hash"] not in hits:
                continue
            if q and sem is None and not all(w in r["_text"] for w in words):
                continue
            out.append(r)
        if q and sem is not None:
            out.sort(key=lambda r: -scores.get(r["file_hash"], 0.0))
        else:
            out.sort(key=lambda r: (not (r["year"] or "").isdigit(), -int(r["year"])
                                    if (r["year"] or "").isdigit() else 0, (r["title"] or "").lower()))
        self.tree.delete(*self.tree.get_children())
        self.row_for_iid.clear()
        for i, r in enumerate(out):
            tags = ["odd"] if i % 2 else []
            if r["_failed"]:
                tags.append("bad")
            match = f"{scores[r['file_hash']]:.2f}" if r["file_hash"] in scores else ""
            iid = self.tree.insert("", "end", values=(match, r["year"] or ("failed" if r["_failed"] else ""),
                                                      r["title"] or ""), tags=tags)
            self.row_for_iid[iid] = r
        if self.rows:
            how = ("" if not q else "  ·  by meaning, best match first" if sem is not None
                   else f"  ·  exact words ({why})")
            self.browse_note.config(text=f"{len(out)} of {len(self.rows)} papers{how}. "
                                         "Double-click opens the note.",
                                    fg=T.AMBER if q and sem is None and self.semantic.get() else T.FAINT)

    def _selected(self):
        sel = self.tree.selection()
        return self.row_for_iid.get(sel[0]) if sel else None

    def on_select(self, _e=None):
        r = self._selected()
        if not r:
            return
        self.pv_title.config(text=r["title"])
        if r["_failed"]:
            self.pv_meta.config(text=f"not catalogued - {r.get('attempts', 0)} failed attempt(s)",
                                fg=T.AMBER)
            self.set_text(self.pv_text, f"{r.get('reason') or ''}\n\n{r['rel_path']}")
            return
        self.pv_meta.config(text="  ·  ".join(x for x in (r["authors"], r["year"],
                                                        f"DOI {r['doi']}" if r.get("doi") else "")
                                             if x), fg=T.MUTED)
        parts = [r["core_takeaway"], ""] if r.get("core_takeaway") else []
        for title, key in (("Key findings", "key_findings"), ("Limitations", "limitations")):
            items = _json(r.get(key))
            if items:
                parts += [title.upper()] + [f"• {x}" for x in items] + [""]
        parts += ["Fields: " + ", ".join(_json(r["research_fields"])),
                  "Keywords: " + ", ".join(_json(r["keywords"])), "", r["rel_path"] or ""]
        self.set_text(self.pv_text, "\n".join(parts))

    def open_note(self):
        r = self._selected()
        if r and r.get("note_filename"):
            self.open_path(os.path.join(self.kb.NOTES_DIR, r["note_filename"]))

    def open_pdf(self):
        r = self._selected()
        if r:
            self.open_path(os.path.join(self.kb.PAPERS_DIR, r["rel_path"] or ""))

    def reveal_pdf(self):
        r = self._selected()
        if r:
            paths.reveal(os.path.join(self.kb.PAPERS_DIR, r["rel_path"] or ""))

    # ----------------------------------------------------------- folders --
    def _page_folders(self, host):
        page = tk.Frame(host, bg=T.BG)
        page.columnconfigure(0, weight=7, uniform="fo")
        page.columnconfigure(1, weight=4, uniform="fo")
        page.rowconfigure(0, weight=1)
        lst = self.card(page, "Folders by content", "folder")
        lst.outer.grid(row=0, column=0, sticky="nsew", padx=(0, self.S(14)))
        bar = tk.Frame(lst, bg=T.SURFACE)
        bar.pack(fill="x")
        e = self.entry(bar, self.fquery, size=11)
        e.pack(side="left", fill="x", expand=True, ipady=self.S(6))
        e.bind("<Return>", lambda ev: self.search_folders())
        lv = self.combo(bar, self.flevel, ["Paper folder", "Top folder (level 1)", "Level 2"], width=18)
        lv.pack(side="left", padx=(self.S(8), 0), ipady=self.S(3))
        lv.bind("<<ComboboxSelected>>", lambda e: self.search_folders())
        self.button(bar, "Search", self.search_folders, "primary").pack(side="left", padx=(self.S(8), 0))
        self.hint(lst, "Ranks the library's folders by how well the papers inside match the search "
                       "(by meaning). Score = its best papers + the folder as a whole. Matching = "
                       "papers that are a hit / papers in the folder.").pack(
            anchor="w", fill="x", pady=(self.S(8), self.S(8)))
        self.ftree = self.make_tree(lst, [("score", 70, "Score"), ("hits", 110, "Matching"),
                                          ("folder", 600, "Folder")],
                                    on_select=self.on_folder_select, on_double=self.open_folder,
                                    sort=self._sort_folders)
        self.folder_note = self.hint(lst, f"Double-click opens the folder in {paths.FILE_MANAGER}.")
        self.folder_note.pack(anchor="w", fill="x", pady=(self.S(8), 0))
        pv = self.card(page, "Best matches in the folder", "doc")
        pv.outer.grid(row=0, column=1, sticky="nsew")
        self.ftitle = self.label(pv, "", bold=True, size=10)
        self.ftitle.pack(fill="x")
        self.fsummary = self.readonly_text(pv, height=14)
        self.fsummary.pack(fill="both", expand=True, pady=(self.S(6), 0))
        frow = tk.Frame(pv, bg=T.SURFACE)
        frow.pack(fill="x", pady=(self.S(8), 0))
        self.button(frow, "Open folder", self.open_folder).pack(side="left")
        self.button(frow, "Show in Browse", self.folder_to_browse).pack(side="left", padx=(self.S(8), 0))
        pv.bind("<Configure>", lambda e: self.ftitle.config(wraplength=max(100, e.width - 10)))
        return page

    def search_folders(self):
        q = self.fquery.get().strip()
        if not q:
            return
        if self.sidx is None:
            self.folder_note.config(text="Semantic index not available yet (loading, or LM Studio "
                                         "is off) - try again in a moment.", fg=T.AMBER)
            if not self.rows:
                self.load_catalog()
            return
        level = {"Paper folder": 0, "Top folder (level 1)": 1, "Level 2": 2}[self.flevel.get()]
        self.folder_note.config(text="Searching…", fg=T.MUTED)
        idx = self.sidx
        paths_by = {r["file_hash"]: [r["rel_path"]] for r in self.rows if not r["_failed"]}

        def work():
            try:
                from ._shared.embed import rank_folders
                qv = idx.query_vec(q)
                res = rank_folders(idx.keys, idx.vecs, idx.scores(q, qv), qv, paths_by, level,
                                   "(library root)")
                err = ""
            except Exception as exc:
                res, err = [], f"Search failed: {type(exc).__name__}: {exc}"
            self.q.put(lambda: self._folders_found(res, err))
        threading.Thread(target=work, daemon=True).start()

    def _folders_found(self, res, err):
        self.folder_res = res
        self._fill_folders(res)
        self.folder_note.config(text=err or f"{len(res)} folders ranked. Click a column title to "
                                            "sort; double-click opens the folder.",
                                fg=T.AMBER if err else T.FAINT)

    def _fill_folders(self, res):
        self.ftree.delete(*self.ftree.get_children())
        self.folder_for_iid.clear()
        for i, d in enumerate(res):
            iid = self.ftree.insert("", "end", values=(f"{d['score']:.3f}", f"{d['hits']} / {d['photos']}",
                                                       d["folder"]), tags=("odd",) if i % 2 else ())
            self.folder_for_iid[iid] = d
        kids = self.ftree.get_children()
        if kids:
            self.ftree.selection_set(kids[0])

    def _sort_folders(self, col):
        key = {"score": lambda d: -d["score"], "hits": lambda d: (-d["hits"], -d["score"]),
               "folder": lambda d: d["folder"].lower()}[col]
        self._fill_folders(sorted(self.folder_res, key=key))

    def _sel_folder(self):
        sel = self.ftree.selection()
        return self.folder_for_iid.get(sel[0]) if sel else None

    @staticmethod
    def _in_folder(d, rel):
        rel = (rel or "").replace("/", os.sep)
        if d["folder"] == "(library root)":
            return os.sep not in rel
        return rel.startswith(d["folder"] + os.sep)

    def on_folder_select(self, _e=None):
        d = self._sel_folder()
        if not d:
            return
        by = {r["file_hash"]: r for r in self.rows}
        members = [r for r in self.rows if not r["_failed"] and self._in_folder(d, r["rel_path"])]
        fields = Counter(f for r in members for f in r["_fields"])
        kws = Counter(k for r in members for k in _json(r["keywords"]))
        self.ftitle.config(text=d["folder"])
        txt = [f"{d['hits']} of {d['photos']} papers match  ·  score {d['score']:.3f}", "",
               "Best matches:"]
        txt += [f"  {s:.2f}  {by[h]['title'][:90]} ({by[h]['year']})"
                for h, s in zip(d["best"], d["best_scores"]) if h in by]
        txt += ["", "Most common fields: " + ", ".join(f for f, _n in fields.most_common(6)),
                "", "Keywords: " + ", ".join(k for k, _n in kws.most_common(15))]
        self.set_text(self.fsummary, "\n".join(txt))

    def open_folder(self):
        d = self._sel_folder()
        if d:
            self.open_path(self.kb.PAPERS_DIR if d["folder"] == "(library root)"
                           else os.path.join(self.kb.PAPERS_DIR, d["folder"]))

    def folder_to_browse(self):
        """Browse with the same search, limited to the selected folder."""
        d = self._sel_folder()
        if not d:
            return
        self.query.set(self.fquery.get())
        self.show_page("browse")
        self.apply_filter()
        for iid, r in list(self.row_for_iid.items()):
            if not self._in_folder(d, r["rel_path"]):
                self.tree.delete(iid)
                self.row_for_iid.pop(iid)
        self.browse_note.config(text=f"{len(self.row_for_iid)} matching paper(s) in {d['folder']}, "
                                     "best match first.", fg=T.FAINT)

    # ---------------------------------------------------------- research --
    def _page_research(self, host):
        page = tk.Frame(host, bg=T.BG)
        deep = self.card(page, "Deep research", "doc", side="bottom", fill="x", pady=(self.S(16), 0))
        self.label(deep, "Question for the report - added to the research prompt. Leave empty to "
                         "summarise what the top papers say about the topic.", muted=True,
                   size=9).pack(anchor="w", pady=(0, self.S(4)))
        self.question = self.text_box(deep, height=3)
        self.question.insert("1.0", self.s.get("question", ""))
        self.question.pack(fill="x")
        drow = tk.Frame(deep, bg=T.SURFACE)
        drow.pack(fill="x", pady=(self.S(10), 0))
        self.btn_research = self.button(drow, "Deep research on top N", self.do_research, "primary")
        self.btn_research.pack(side="left")
        self.hint(drow, "The model reads each paper's note and text, then writes a cited report into "
                        "Research (about 1-2 min per paper). It opens when done.").pack(
            side="left", fill="x", expand=True, padx=(self.S(14), 0))

        top = self.card(page, "Top papers for a topic", "search", fill="both", expand=True)
        bar = tk.Frame(top, bg=T.SURFACE)
        bar.pack(fill="x")
        e = self.entry(bar, self.rquery, size=11)
        e.pack(side="left", fill="x", expand=True, ipady=self.S(6))
        e.bind("<Return>", lambda ev: self.preview_top())
        self.button(bar, "Preview", self.preview_top, "primary").pack(side="left", padx=(self.S(8), 0))
        opts = tk.Frame(top, bg=T.SURFACE)
        opts.pack(fill="x", pady=(self.S(10), self.S(10)))
        self.check(opts, "By meaning (semantic) - untick for exact words", self.semantic).pack(side="left")
        self.btn_savelist = self.button(opts, "Save top N list", self.do_save_list)
        self.btn_savelist.pack(side="right")
        self.entry(opts, self.top_n, width=5).pack(side="right", padx=(self.S(6), self.S(10)),
                                                   ipady=self.S(4))
        self.label(opts, "Top N", muted=True, size=9).pack(side="right")
        self.rtree = self.make_tree(top, [("score", 80, "Match"), ("year", 70, "Year"),
                                          ("title", 700, "Title")], on_double=self.open_top_note)
        self.research_note = self.hint(top, "The same ranking picks the papers for the list and for "
                                            "the deep research. Double-click opens a note.")
        self.research_note.pack(anchor="w", fill="x", pady=(self.S(8), 0))
        return page

    def _top_n(self) -> int:
        try:
            n = int(self.top_n.get().strip())
        except ValueError:
            raise ValueError("Top N must be a whole number.")
        if not 1 <= n <= 100:
            raise ValueError("Top N must be between 1 and 100.")
        return n

    def _question(self) -> str:
        return self.question.get("1.0", "end").strip() if hasattr(self, "question") else ""

    def preview_top(self):
        q = self.rquery.get().strip()
        if not q:
            return
        try:
            n = self._top_n()
        except ValueError as exc:
            messagebox.showerror("Top N", str(exc))
            return
        semantic = self.semantic.get()
        self.research_note.config(text="Searching…", fg=T.MUTED)

        def work():
            try:
                rows, note = self.research.top_papers(q, n, semantic=semantic)
            except Exception as exc:
                rows, note = [], f"Search failed: {exc}"
            self.q.put(lambda: self._show_top(rows, note))
        threading.Thread(target=work, daemon=True).start()

    def _show_top(self, rows, note):
        self.rtree.delete(*self.rtree.get_children())
        self.top_rows.clear()
        for i, r in enumerate(rows):
            iid = self.rtree.insert("", "end", values=(r["score"], r["year"], r["title"]),
                                    tags=("odd",) if i % 2 else ())
            self.top_rows[iid] = r
        self.research_note.config(text=note or f"Top {len(rows)}. Save the list, or run the deep "
                                                "research on them.", fg=T.AMBER if note else T.FAINT)

    def open_top_note(self):
        sel = self.rtree.selection()
        if sel and self.top_rows.get(sel[0]):
            self.open_path(self.top_rows[sel[0]]["note_path"])

    def do_save_list(self):
        q = self.rquery.get().strip()
        if not q:
            messagebox.showinfo("Save top N list", "Type a topic first.")
            return
        try:
            n = self._top_n()
        except ValueError as exc:
            messagebox.showerror("Top N", str(exc))
            return
        semantic = self.semantic.get()
        self.persist()
        self.research_note.config(text=f"Saving the top {n}…", fg=T.MUTED)

        def work():
            try:
                rows, note = self.research.top_papers(q, n, semantic=semantic)
                if not rows:
                    raise RuntimeError("no papers match this search")
                path = self.research.save_list(q, rows)
                msg, col = f"Saved top {len(rows)}: {path}" + (f"  ({note})" if note else ""), T.GREEN
            except Exception as exc:
                path, msg, col = None, f"Could not save the list: {exc}", T.RED
            self.q.put(lambda: self._list_saved(msg, col, path))
        threading.Thread(target=work, daemon=True).start()

    def _list_saved(self, msg, col, path):
        self.research_note.config(text=msg, fg=col)
        self.log(msg + "\n", "ok" if path else "err")
        if path:
            paths.open_path(path)

    def do_research(self):
        q = self.rquery.get().strip()
        if not q:
            messagebox.showinfo("Deep research", "Type a topic first: it picks the papers.")
            return
        try:
            n = self._top_n()
        except ValueError as exc:
            messagebox.showerror("Top N", str(exc))
            return
        if not self._apply_limits() or not self._model_ok():
            return
        question = self._question()
        if not messagebox.askyesno(
                "Deep research",
                f"The model will read the top {n} papers for\n  \"{q}\"\n\n"
                f"and answer:\n  \"{question or 'what these papers say about the topic'}\"\n\n"
                f"About {n * 2 + 5} minutes; LM Studio is (re)loaded first. The report opens "
                "when it is done. Start?"):
            return
        args = ["research", "--query", q, "--top", str(n)]
        if question:
            args += ["--question", question]
        if not self.semantic.get():
            args.append("--exact")
        self.start(args, "deep research", "research", hint=n)

    # ----------------------------------------------------------- options --
    def _page_options(self, host):
        outer, page = self.scrollable(host)
        page.columnconfigure((0, 1), weight=1, uniform="opt")

        opt = self.card(page, "Ingest options", "settings")
        opt.outer.grid(row=1, column=0, sticky="nsew", padx=(0, self.S(14)))
        grid = tk.Frame(opt, bg=T.SURFACE)
        grid.pack(fill="x")
        for i, (lab, var) in enumerate((("Max papers (0 = all)", self.limit),
                                        ("Time limit, min (0 = none)", self.minutes),
                                        ("Max tokens per document", self.doc_tokens),
                                        ("Max pages per file", self.max_pages),
                                        ("Papers at the same time (1-4)", self.parallel))):
            r, c = divmod(i, 2)
            self.label(grid, lab, muted=True, size=9).grid(row=r * 2, column=c, sticky="w",
                                                           padx=(0 if c == 0 else self.S(12), 0),
                                                           pady=(self.S(10) if r else 0, 0))
            self.entry(grid, var, width=12).grid(row=r * 2 + 1, column=c, sticky="we", ipady=self.S(5),
                                                 padx=(0 if c == 0 else self.S(12), 0),
                                                 pady=(self.S(4), 0))
        grid.columnconfigure((0, 1), weight=1)
        self.limits_hint = self.hint(opt, "")
        self.limits_hint.pack(anchor="w", fill="x", pady=(self.S(6), 0))
        for v in (self.doc_tokens, self.max_pages, self.parallel):
            v.trace_add("write", lambda *a: self._limits_hint())
        self._limits_hint()
        flags = tk.Frame(opt, bg=T.SURFACE)
        flags.pack(fill="x", pady=(self.S(12), 0))
        for text, var in (("Retry failed papers", self.retry),
                          ("Vision OCR for scanned PDFs (a few seconds a page)", self.vision),
                          ("Skip supplementary files", self.skip_supp)):
            self.check(flags, text, var).pack(anchor="w", pady=self.S(2))
        self.hint(opt, "Used by Update now and Rebuild. The limits are saved to config.yaml when a "
                       "run starts, so the scheduled run (4-hour limit) uses them too.").pack(
            anchor="w", fill="x", pady=(self.S(12), 0))

        pc = self.card(page, "Model and folders", "folder")
        pc.outer.grid(row=1, column=1, sticky="nsew")
        self.label(pc, "LM Studio model (reads papers, writes reports)", muted=True,
                   size=9).pack(anchor="w", pady=(0, self.S(4)))
        mrow = tk.Frame(pc, bg=T.SURFACE)
        mrow.pack(fill="x")
        self.model_var = tk.StringVar(value=self.kb.MODEL_NAME)
        self.model_box = self.combo(mrow, self.model_var, readonly=False)
        self.model_box.pack(side="left", fill="x", expand=True, ipady=self.S(3))
        self.model_box.bind("<Button-1>", lambda e: self.refresh_models()
                            if not self.model_box["values"] else None)
        self.button(mrow, "Save", self.save_model, "primary").pack(side="left", padx=(self.S(8), 0))
        self.model_msg = self.hint(pc, "Pick or type a model key, then Save. Used by every run, "
                                       "the scheduled one included.")
        self.model_msg.pack(fill="x", pady=(self.S(3), 0))
        self.path_row(pc, "PDF library", self.papers_dir,
                      lambda: self.pick_dir(self.papers_dir, "PDF library"), "Only ever read.")
        self.path_row(pc, "Export folder (synced)", self.export_dir,
                      lambda: self.pick_dir(self.export_dir, "export folder"),
                      "Rewritten after every run: for other agents, plus a full backup. Empty = none.")
        self.path_row(pc, "Data folder (notes, database, caches)", self.data_dir,
                      lambda: self.pick_dir(self.data_dir, "data folder"),
                      "Keep it on a local disk, not on a cloud drive.")
        self.path_row(pc, "Custom prompt file", self.prompt_path, self.pick_prompt,
                      "Empty = the built-in prompt.")
        r = tk.Frame(pc, bg=T.SURFACE)
        r.pack(fill="x", pady=(self.S(12), 0))
        self.button(r, "Save folders", self.save_paths, "primary").pack(side="left")
        self.button(r, "Edit config.yaml", lambda: paths.edit_file(config.CONFIG_PATH)).pack(
            side="left", padx=(self.S(8), 0))

        mt = self.card(page, "Maintenance", "warn")
        mt.outer.grid(row=0, column=0, columnspan=2, sticky="nsew", pady=(0, self.S(16)))
        self.action_rows(mt, [
            ("Report", "Statistics of the library, failures, reading speed and a CSV of the whole "
                       "catalogue. Opens when ready.", "btn_report", self.do_report, "secondary"),
            ("Check setup", "Folders, LM Studio, the model and the embedding model. Changes nothing.",
             "btn_check", lambda: self.start(["check"], "check setup", "check"), "secondary"),
            ("Update search index", "Embeds new notes for search by meaning (done automatically "
                                    "after every update).", "btn_index",
             lambda: self.start(["index"], "update search index", "index"), "secondary"),
            ("Remove schedule", "Stops the weekly/monthly task. Update now keeps working.",
             "btn_unsched", self.do_unschedule, "secondary"),
            ("Rebuild from scratch…", "Backs up, clears the catalogue and every note, then re-reads "
                                      "all PDFs (2-3 h). Use after changing the prompt.",
             "btn_rebuild", self.do_rebuild, "danger")])
        self.appearance_card(page, 2)
        return outer

    def _read_limits(self) -> tuple[int, int]:
        kb = self.kb
        try:
            tokens, pages = int(self.doc_tokens.get().strip()), int(self.max_pages.get().strip())
        except ValueError:
            raise ValueError("Max tokens per document and max pages per file must be whole numbers.")
        (tlo, thi), (plo, phi) = kb.DOC_TOKENS_RANGE, kb.PAGES_RANGE
        if not tlo <= tokens <= thi:
            raise ValueError(f"Max tokens per document must be between {tlo:,} and {thi:,}.")
        if not plo <= pages <= phi:
            raise ValueError(f"Max pages per file must be between {plo} and {phi}.")
        return tokens, pages

    def _read_parallel(self) -> int:
        try:
            return max(1, min(4, int(self.parallel.get().strip())))
        except (ValueError, AttributeError):
            return 1

    def _limits_hint(self):
        if not hasattr(self, "limits_hint"):
            return
        try:
            tokens, pages = self._read_limits()
        except ValueError as exc:
            self.limits_hint.config(text=str(exc), fg=T.RED)
            return
        chars, ctx = self.kb.derived_limits(tokens)
        par = self._read_parallel()
        txt = (f"≈ {chars:,} characters of text per paper, whichever of the two limits comes first. "
               f"Model loaded with a {ctx * par:,}-token context"
               + (f" ({par} papers at a time, ~1.7x faster with 2)" if par > 1 else "")
               + f". Scanned PDFs: first {min(6, pages)} pages via vision.")
        if ctx * par > self.kb.MIN_CONTEXT_LENGTH:
            txt += " A bigger context needs more GPU memory (49k context x 2 filled a 12 GB GPU)."
        self.limits_hint.config(text=txt, fg=T.FAINT)

    def _apply_limits(self) -> bool:
        """Save changed limits to config.yaml before a run; False if invalid."""
        try:
            tokens, pages = self._read_limits()
        except ValueError as exc:
            messagebox.showerror("Options", str(exc))
            return False
        par = self._read_parallel()
        if par != self.kb.PARALLEL:
            config.update({"parallel": par})
            self._load_kb()
        if (tokens, pages) != (self.kb.MAX_DOC_TOKENS, self.kb.MAX_PAGES):
            config.update({"max_doc_tokens": tokens, "max_pages": pages})
            self._load_kb()
            self.log(f"\nLimits saved: {tokens:,} tokens per document ({self.kb.MAX_CHARS:,} chars), "
                     f"{pages} pages per file, context {self.kb.CONTEXT_LENGTH:,}. Applies to papers "
                     "read from now on; Rebuild re-reads the whole library.\n", "ok")
        return True

    def _opts(self) -> list[str]:
        a = []
        try:
            if int(self.limit.get() or 0) > 0:
                a += ["--limit", str(int(self.limit.get()))]
            if float(self.minutes.get() or 0) > 0:
                a += ["--max-minutes", str(float(self.minutes.get()))]
        except ValueError:
            raise ValueError("Max papers and time limit must be numbers.")
        if self.retry.get():
            a.append("--retry-failed")
        if not self.vision.get():
            a.append("--no-vision")
        if self.skip_supp.get():
            a.append("--skip-supplementary")
        p = self.prompt_path.get().strip()
        if p:
            if not os.path.exists(p):
                raise ValueError(f"Prompt file not found:\n{p}")
            a += ["--system-prompt-file", p]
        return a

    # ----------------------------------------------------------- actions --
    def _pipeline(self):
        from . import pipeline
        return importlib.reload(pipeline)

    def model_check(self):
        return self._pipeline().check_model(self.kb.MODEL_NAME)

    def refresh_models(self):
        models = self._pipeline().installed_models("llm")
        if models is None:
            self.model_msg.config(text="Cannot list models: LM Studio's lms not found.", fg=T.AMBER)
            return
        self.model_box["values"] = [m["modelKey"] for m in models]

    def save_model(self):
        name = self.model_var.get().strip()
        if not name:
            return
        ok, msg = self._pipeline().check_model(name)
        if not ok:
            self.model_msg.config(text=msg.splitlines()[0], fg=T.RED)
            messagebox.showerror("Model not available", msg)
            return
        key = next((m["modelKey"] for m in (self._pipeline().installed_models("llm") or [])
                    if m["modelKey"].lower() == name.lower()), name)
        config.update({"model": key})
        self._load_kb()
        self.model_var.set(key)
        self.model_msg.config(text=f"Saved. {msg}", fg=T.GREEN)
        self.log(f"\nModel set to {key} (config.yaml).\n", "ok")
        self.refresh_status()

    def save_paths(self):
        pap, exp, data = (v.get().strip() for v in (self.papers_dir, self.export_dir, self.data_dir))
        if not os.path.isdir(pap):
            messagebox.showerror("Folders", f"PDF library not found:\n{pap}")
            return
        if paths.looks_like_cloud_folder(data) and not messagebox.askyesno(
                "Data folder", "The data folder looks like a cloud-drive folder. The database there "
                               "can be locked by the sync client mid-run. Use it anyway?"):
            return
        pr = self.prompt_path.get().strip()
        if pr and not os.path.exists(pr):
            messagebox.showerror("Folders", f"Prompt file not found:\n{pr}")
            return
        default_data = config.APP_HOME if config.PORTABLE else ""
        same = default_data and os.path.normcase(data) == os.path.normcase(default_data)
        config.update({"papers_dir": pap, "export_dir": exp, "data_dir": "" if same else data})
        self._load_kb()
        self.persist()
        self.model_msg.config(text="Folders saved to config.yaml.", fg=T.GREEN)
        self.log(f"\nFolders saved: library {pap}, export {exp or '(none)'}, data {self.kb.KB_HOME}\n",
                 "ok")
        self.rows = []
        self.refresh_status()

    def pick_dir(self, var, what):
        p = filedialog.askdirectory(title=f"Select the {what}", initialdir=var.get() or None)
        if p:
            var.set(os.path.normpath(p))

    def pick_prompt(self):
        p = filedialog.askopenfilename(title="Select a system prompt file",
                                       filetypes=[("Text", "*.txt *.md"), ("All files", "*.*")])
        if p:
            self.prompt_path.set(os.path.normpath(p))

    def do_update(self):
        try:
            opts = self._opts()
        except ValueError as exc:
            messagebox.showerror("Options", str(exc))
            return
        if not self._apply_limits() or not self._model_ok():
            return
        self.register_schedule(["update", "--max-minutes", "240"])
        hint = self.pending_hint
        try:
            lim = int(self.limit.get() or 0)
            hint = min(hint, lim) if lim > 0 else hint
        except ValueError:
            pass
        self.start(["update", *opts], "update", "update", hint=hint)

    def do_plan(self):
        self.start(["plan"], "check what an update would process", "plan")

    def do_scouts(self):
        self.start(["scouts"], "refresh scout index", "scouts")

    def do_report(self):
        self.start(["report"], "report", "report")

    def do_rebuild(self):
        try:
            opts = self._opts()
        except ValueError as exc:
            messagebox.showerror("Options", str(exc))
            return
        if not self._apply_limits() or not self._model_ok():
            return
        prompt = self.prompt_path.get().strip()
        if not messagebox.askyesno(
                "Rebuild from scratch",
                "This clears the catalogue and every note, then re-reads all PDFs.\n\n"
                "A backup of the database and Notes is taken first (cache/backups). Your PDFs, "
                "scout briefs and projects file are never touched.\n\n"
                f"Model: {self.kb.MODEL_NAME}\n"
                f"Prompt: {os.path.basename(prompt) if prompt else 'built-in'}\n\n"
                "A full library takes about 2-3 hours. Continue?", icon="warning"):
            return
        self.start(["rebuild", *opts], "rebuild from scratch", "rebuild",
                   hint=len(self.kb.iter_pdfs()))

    def after_job(self, rc):
        self.rows, self.sidx = [], None          # Browse / Folders reload on the next visit


# ================================================================ helpers ===
def _init_theme():
    theme.init(PALETTE, SPEC.title, THEME_PATH)


def launcher_argv() -> list[str]:
    """Command a shortcut runs to open the panel."""
    argv = paths.launcher_command(SPEC.pkg, SPEC.pkg_file, SPEC.launcher)
    return argv if paths.FROZEN or argv[-1] != SPEC.pkg else [*argv, "gui"]


def make_shortcuts(desktop=False, menu=True, remove=False) -> int:
    if remove:
        msgs = shortcuts.remove(SPEC.title)
    else:
        assets = paths.resource(SPEC.pkg_file, "assets")
        msgs = shortcuts.create(SPEC.title, launcher_argv(), paths.app_home(SPEC.pkg_file),
                                icon_png=os.path.join(assets, "bibliomancer_icon_256.png"),
                                icon_ico=os.path.join(assets, "bibliomancer_icon.ico"),
                                desktop=desktop, menu=menu,
                                comment="Local literature knowledge base")
    print("\n".join(msgs) or "nothing to do")
    return 0


def run_wizard() -> int:
    """First-run setup: folders, LM Studio check, shortcuts -> config.yaml."""
    from ._shared import lmstudio
    from ._shared.wizard import Field
    from ._shared.wizard import run_wizard as wizard
    _init_theme()
    docs = paths.documents_dir()

    def lm_check():
        lms = lmstudio.find_lms()
        if not lms:
            return [(False, "LM Studio is not installed (free, from lmstudio.ai). Install it, "
                            "open it once, download qwen/qwen3.5-9b, then press Check again.")]
        ok, msg = lmstudio.check_model(lms, config.DEFAULTS["model"], 24576)
        return [(True, f"LM Studio found: {lms}"),
                (ok, msg if ok else msg.splitlines()[0] + " - download it in LM Studio (Discover).")]
    values = wizard(SPEC.title, "Choose where your PDFs are and where the database should live. "
                                "Everything runs locally with LM Studio; nothing leaves this computer.",
                    [Field("papers_dir", "Your PDF library", hint="Only ever read."),
                     Field("data_dir", "Data folder (notes, database)",
                           default=os.path.join(docs, SPEC.title), local_only=True,
                           hint="On a local disk. Notes are Markdown files you can open anywhere."),
                     Field("export_dir", "Synced export folder", required=False,
                           hint="E.g. a Google Drive / OneDrive folder: export for other AI agents "
                                "and a backup after every run.")],
                    lm_check, icon_png=paths.resource(SPEC.pkg_file, "assets", "bibliomancer_icon_256.png"))
    if not values:
        return 1
    config.update({k: values[k] for k in ("papers_dir", "data_dir", "export_dir")})
    if values["desktop"] or values["menu"]:
        make_shortcuts(values["desktop"], values["menu"])
    return 0


def main():
    if not config.exists() and run_wizard() != 0:
        return
    _init_theme()
    run_app(App, SPEC)


if __name__ == "__main__":
    main()
