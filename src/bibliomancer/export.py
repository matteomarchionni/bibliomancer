"""
kb_export.py - Publish the knowledge base to the synced export folder.

Runs after every update or rebuild and OVERWRITES the previous export. Two
jobs in one pass:

1. A distilled, agent-ready copy of the literature database, so any agent -
   Claude, ChatGPT, or a local model in LM Studio - can use it as context.

       literature_kb.md          THE single file to hand to an agent: a
                                 self-describing header, the research context,
                                 a compact index of every paper, then every
                                 full note. Plain Markdown, no tooling needed.
       literature_kb_index.md    index only: one line per paper. Small enough
                                 for any context window (~40k tokens).
       literature_kb.jsonl       one JSON record per paper, every field.
       literature_kb.sqlite      one self-contained SQLite file: table
                                 `papers` (all fields + the full note text)
                                 and `papers_fts`, an FTS5 full-text index.
       literature_kb.zip         all of the above in one archive.
       README_FOR_AGENTS.md      what each file is and how to use it.

2. A backup of everything irreplaceable, since the working copy now lives on
   local disk only: the pipeline database, the scout briefs (with your
   annotations), the projects file, the scripts and the config.

       pipeline_backup/          manifest.db, Scouts/, Projects/, code/ (src,
                                 tools, launchers), config.yaml, theme.css

   plus Notes/ (mirrored note by note) and kb_doi_index.tsv, which the
   weekly literature scout reads to recognise papers already owned.

    python -m bibliomancer export                 # export to the configured export_dir
    python -m bibliomancer export --dest <folder> # export elsewhere
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import zipfile

from . import core as kb

LIST_FIELDS = ("research_fields", "keywords", "key_findings", "limitations")


# --------------------------------------------------------------------------
def load_papers() -> list[dict]:
    from . import projects as kb_projects
    conn = kb.connect()
    rows = conn.execute(
        "SELECT * FROM processed_papers "
        "ORDER BY (year = 'Unknown'), year DESC, title COLLATE NOCASE").fetchall()
    conn.close()
    out = []
    for r in rows:
        p = dict(r)
        for f in LIST_FIELDS:
            try:
                p[f] = json.loads(p.get(f) or "[]")
            except Exception:
                p[f] = []
        note_path = os.path.join(kb.NOTES_DIR, p.get("note_filename") or "")
        try:
            with open(note_path, encoding="utf-8") as fh:
                p["note_markdown"] = fh.read()
        except OSError:
            p["note_markdown"] = ""
        item = kb_projects.find_match(p.get("doi"), p.get("title"))
        p["scout_projects"] = (item or {}).get("projects") or []
        p["scout_verdict"] = (item or {}).get("user_verdict") or ""
        p["scout_comment"] = (item or {}).get("user_comment") or ""
        out.append(p)
    return out


def _cell(s, n) -> str:
    s = " ".join(str(s or "").split()).replace("|", "/")
    return s if len(s) <= n else s[: n - 1] + "…"


def index_md(papers: list[dict]) -> str:
    L = ["| Year | Title | Fields | Core takeaway | DOI | Note |",
         "|---|---|---|---|---|---|"]
    for p in papers:
        L.append(f"| {p['year']} | {_cell(p['title'], 140)} "
                 f"| {_cell(', '.join(p['research_fields']), 70)} "
                 f"| {_cell(p['core_takeaway'], 240)} | {p.get('doi') or ''} "
                 f"| {p.get('note_filename') or ''} |")
    return "\n".join(L)


def research_context() -> str:
    try:
        with open(os.path.join(kb.PROJECTS_DIR, "projects.json"), encoding="utf-8") as fh:
            pj = json.load(fh)
    except Exception:
        return "_projects.json not available._"
    L = [f"Owner: {pj.get('owner', '')} ({pj.get('affiliation', '')}).", ""]
    for p in pj.get("projects", []):
        L.append(f"- **{p['name']}** ({p.get('status', '')}) - {p.get('one_line', '')}")
    return "\n".join(L)


HOW_TO_USE = """\
## How to use this file (for AI agents)

This is a **distilled literature database**: every paper in the owner's library
was read by a language model and summarised into a structured note with the
same fields - research question, methodology, key findings, limitations of the
study itself, and a one-sentence core takeaway. It is designed to be used as
background knowledge when answering questions, planning analyses, positioning
a paper against prior work, or finding citations.

- Start from the **Index** below to find relevant papers; each row links to the
  full note further down (search for the note filename).
- `Limitations` describe weaknesses of each study itself, not of its field.
- `year` and `doi` come from the PDF metadata where the model was unsure.
- Notes marked `text_source: vision-ocr` were transcribed from scanned PDFs;
  treat exact numbers and names in them with some caution.
- A section **Intersection with personal work** appears on papers that the
  owner's weekly literature scout flagged against the owner's projects. The quoted
  comments there (sometimes in Italian) are the owner's own verdicts and
  outrank the scout's prose.
- Summaries can contain model errors. When a claim matters, check it against
  the original paper (DOI given) before relying on it.
"""


def single_file(papers: list[dict]) -> str:
    n_vision = sum(1 for p in papers if p.get("text_source") == "vision-ocr")
    n_scout = sum(1 for p in papers if p["scout_projects"] or p["scout_comment"])
    head = [
        "# Literature knowledge base",
        "",
        f"_{len(papers)} papers - exported {kb.now_iso()} - "
        f"{n_scout} flagged by the literature scout - {n_vision} from scanned PDFs._",
        "",
        HOW_TO_USE,
        "## Research context (the owner's projects)",
        "",
        research_context(),
        "",
        "## Index",
        "",
        index_md(papers),
        "",
        "## Full notes",
        "",
    ]
    body = []
    for p in papers:
        body += [f"<!-- note: {p.get('note_filename')} -->", "",
                 p["note_markdown"].strip() or f"# {p['title']}", "", "---", ""]
    return "\n".join(head + body)


def write_sqlite(path: str, papers: list[dict]) -> None:
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE papers (
            file_hash TEXT PRIMARY KEY, title TEXT, authors TEXT, year TEXT,
            doi TEXT, research_fields TEXT, keywords TEXT, research_question TEXT,
            methodology TEXT, key_findings TEXT, limitations TEXT,
            core_takeaway TEXT, doc_type TEXT, text_source TEXT,
            scout_projects TEXT, scout_verdict TEXT, scout_comment TEXT,
            relative_path TEXT, note_filename TEXT, note_markdown TEXT,
            processed_at TEXT);
        CREATE VIRTUAL TABLE papers_fts USING fts5(
            title, authors, keywords, research_fields, research_question,
            methodology, key_findings, limitations, core_takeaway,
            content='papers', content_rowid='rowid');
        CREATE TABLE about (key TEXT PRIMARY KEY, value TEXT);
    """)
    for p in papers:
        vals = (p["file_hash"], p["title"], p["authors"], p["year"], p.get("doi"),
                json.dumps(p["research_fields"], ensure_ascii=False),
                json.dumps(p["keywords"], ensure_ascii=False),
                p["research_question"], p["methodology"],
                json.dumps(p["key_findings"], ensure_ascii=False),
                json.dumps(p["limitations"], ensure_ascii=False),
                p["core_takeaway"], p.get("doc_type"), p.get("text_source"),
                json.dumps(p["scout_projects"]), p["scout_verdict"], p["scout_comment"],
                p["rel_path"], p.get("note_filename"), p["note_markdown"],
                p.get("processed_at"))
        c.execute(f"INSERT INTO papers VALUES ({','.join('?' * len(vals))})", vals)
    c.execute("INSERT INTO papers_fts(papers_fts) VALUES ('rebuild')")
    c.executemany("INSERT INTO about VALUES (?, ?)", [
        ("exported_at", kb.now_iso()),
        ("papers", str(len(papers))),
        ("list_columns", "research_fields, keywords, key_findings, limitations, "
                         "scout_projects are JSON arrays"),
        ("full_text_search", "SELECT title, year, doi FROM papers WHERE rowid IN "
                             "(SELECT rowid FROM papers_fts WHERE papers_fts MATCH "
                             "'bleaching AND recovery')"),
    ])
    c.commit()
    c.close()


README_AGENTS = """\
# Literature knowledge base - export

Overwritten after every update of the knowledge base. Do not edit files here;
the working copy lives elsewhere and will replace them.

## Give an agent ONE file

**`literature_kb.md`** - self-describing: instructions, research context, a
compact index of every paper, then every full note. Works with any agent that
can read a file. Prompt example:

> Use `literature_kb.md` as my literature database. Read the "How to use"
> section first. Then answer: which papers in it are closest competitors to
> my <project> project, and what do their limitations sections say?

If the full file is too large for an agent's context window, give it
**`literature_kb_index.md`** first (one line per paper), then the individual
notes it asks for from `Notes/`.

## Other formats

| File | For |
|---|---|
| `literature_kb.sqlite` | Agents that run code or SQL. Table `papers` has every field plus the full note text; `papers_fts` is an FTS5 full-text index. The `about` table documents the schema and gives a query example. |
| `literature_kb.jsonl` | Programmatic use: one JSON object per paper. |
| `literature_kb.zip` | Everything above in one archive, for uploading. |
| `Notes/` | One Markdown note per paper (Obsidian-friendly). |
| `synthesis_matrix.md` | Flat table of all papers. |
| `kb_doi_index.tsv` | DOI, year, title, note - read by the weekly literature scout to recognise papers already in the library. |
| `pipeline_backup/` | Backup of the pipeline itself: `manifest.db`, the scout briefs with annotations, `projects.json`, scripts and config. Enough to restore the working copy on another machine. |

## Fields

`title, authors, year, doi, research_fields, keywords, research_question,
methodology, key_findings, limitations, core_takeaway, doc_type
(paper|supplementary), text_source (pdf-text|vision-ocr), scout_projects,
scout_verdict, scout_comment`.

`limitations` are weaknesses of the study itself, not of its field. `scout_*`
fields are filled only for papers flagged by the weekly literature scout; the
comment is the owner's own verdict.
"""


# --------------------------------------------------------------------------
def _write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _same(a: str, b: str) -> bool:
    try:
        sa, sb = os.stat(a), os.stat(b)
        return sa.st_size == sb.st_size and int(sa.st_mtime) <= int(sb.st_mtime)
    except OSError:
        return False


def mirror(src: str, dst: str, log) -> tuple[int, int]:
    """Make dst a copy of src: copy new/changed files, delete extras."""
    copied = deleted = 0
    os.makedirs(dst, exist_ok=True)
    want = set()
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel = os.path.relpath(root, src)
        os.makedirs(os.path.join(dst, rel), exist_ok=True)
        for f in files:
            if f.endswith((".pyc", ".tmp")):
                continue
            s = os.path.join(root, f)
            d = os.path.join(dst, rel, f)
            want.add(os.path.normcase(os.path.normpath(d)))
            if not _same(s, d):
                shutil.copy2(s, d)
                copied += 1
    for root, _, files in os.walk(dst):
        for f in files:
            d = os.path.join(root, f)
            if os.path.normcase(os.path.normpath(d)) not in want:
                os.remove(d)
                deleted += 1
    return copied, deleted


def export(dest: str, log=print) -> dict:
    os.makedirs(dest, exist_ok=True)
    papers = load_papers()
    log(f"Exporting {len(papers)} paper(s) to {dest}")

    files = {
        "literature_kb.md": single_file(papers),
        "literature_kb_index.md": (f"# Literature knowledge base - index\n\n"
                                   f"_{len(papers)} papers, exported {kb.now_iso()}. "
                                   f"Full notes: literature_kb.md or Notes/._\n\n"
                                   + index_md(papers) + "\n"),
        "literature_kb.jsonl": "\n".join(
            json.dumps({k: v for k, v in p.items() if k != "rel_path"} |
                       {"relative_path": p["rel_path"]}, ensure_ascii=False)
            for p in papers) + "\n",
        "README_FOR_AGENTS.md": README_AGENTS,
        "kb_doi_index.tsv": "doi\tyear\ttitle\tnote_filename\n" + "".join(
            f"{p['doi']}\t{p['year']}\t{' '.join(p['title'].split())}\t"
            f"{p.get('note_filename') or ''}\n" for p in papers if p.get("doi")),
    }
    for name, text in files.items():
        _write(os.path.join(dest, name), text)

    sqlite_path = os.path.join(dest, "literature_kb.sqlite")
    tmp_sqlite = os.path.join(kb.CACHE_DIR, "literature_kb.sqlite.tmp")
    write_sqlite(tmp_sqlite, papers)          # build locally, then copy once
    shutil.copy2(tmp_sqlite, sqlite_path)
    os.remove(tmp_sqlite)

    if os.path.exists(kb.INDEX_PATH):
        shutil.copy2(kb.INDEX_PATH, os.path.join(dest, "synthesis_matrix.md"))

    zip_members = list(files) + ["literature_kb.sqlite", "synthesis_matrix.md"]
    zip_tmp = os.path.join(kb.CACHE_DIR, "literature_kb.zip.tmp")
    with zipfile.ZipFile(zip_tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for m in zip_members:
            p = os.path.join(dest, m)
            if os.path.exists(p) and m != "kb_doi_index.tsv":
                z.write(p, m)
    shutil.copy2(zip_tmp, os.path.join(dest, "literature_kb.zip"))
    os.remove(zip_tmp)

    c, d = mirror(kb.NOTES_DIR, os.path.join(dest, "Notes"), log)
    log(f"  Notes mirrored: {c} copied, {d} removed")

    # ---- backup of the pipeline itself --------------------------------
    bk = os.path.join(dest, "pipeline_backup")
    os.makedirs(bk, exist_ok=True)
    db_tmp = os.path.join(kb.CACHE_DIR, "manifest_export.db")
    src = sqlite3.connect(kb.DB_PATH)
    dst = sqlite3.connect(db_tmp)
    src.backup(dst)                           # consistent snapshot, never a torn copy
    dst.close()
    src.close()
    shutil.copy2(db_tmp, os.path.join(bk, "manifest.db"))
    os.remove(db_tmp)
    for sub in ("Scouts", "Projects"):
        s = os.path.join(kb.KB_HOME, sub)
        if os.path.isdir(s):
            mirror(s, os.path.join(bk, sub), log)
    code = os.path.join(bk, "code")
    for sub in ("src", "tools", "tests"):
        s = os.path.join(kb.APP_HOME, sub)
        if os.path.isdir(s):
            mirror(s, os.path.join(code, sub), log)
    for f in ("BiblioMancer.cmd", "BiblioMancer.pyw", "README.md", "pyproject.toml"):
        s = os.path.join(kb.APP_HOME, f)
        if os.path.exists(s):
            os.makedirs(code, exist_ok=True)
            shutil.copy2(s, os.path.join(code, f))
    for s in (kb.CONFIG_PATH, os.path.join(os.path.dirname(kb.CONFIG_PATH), "theme.css")):
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(bk, os.path.basename(s)))

    sizes = {n: os.path.getsize(os.path.join(dest, n))
             for n in ("literature_kb.md", "literature_kb_index.md", "literature_kb.sqlite",
                       "literature_kb.zip", "literature_kb.jsonl")}
    manifest = {"exported_at": kb.now_iso(), "papers": len(papers),
                "source_kb_home": kb.KB_HOME, "bytes": sizes}
    _write(os.path.join(dest, "export_manifest.json"), json.dumps(manifest, indent=2))
    for n, b in sizes.items():
        log(f"  {n:24} {b / 1024:8.0f} KB")
    log("  pipeline_backup/: manifest.db, Scouts, Projects, code, config")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Export the KB for agents and backup.")
    ap.add_argument("--dest", default=None, help=f"default: {kb.EXPORT_DIR}")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an export that came from another knowledge base")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    log = kb.Logger("export")
    dest = args.dest or kb.EXPORT_DIR
    # An export folder belongs to one knowledge base: a test or second copy
    # must never overwrite (and mirror-delete the notes of) the real one.
    try:
        with open(os.path.join(dest, "export_manifest.json"), encoding="utf-8") as fh:
            owner = json.load(fh).get("source_kb_home") or ""
    except (OSError, ValueError):
        owner = ""
    if owner and os.path.normcase(os.path.abspath(owner)) != os.path.normcase(kb.KB_HOME) \
            and not args.force:
        log(f"EXPORT SKIPPED: {dest} holds the export of another knowledge base ({owner}). "
            "Use --force to replace it.")
        return 1
    try:
        export(dest, log=log)
    except Exception as exc:
        log(f"EXPORT FAILED: {type(exc).__name__}: {exc}")
        return 1
    log("Export complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
