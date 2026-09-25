"""
core.py - Shared core for the Papers knowledge base.

Everything that both ingestion paths need lives here:
  * paths / configuration
  * SQLite manifest (schema, upsert, failure tracking, stat-based scan cache)
  * PDF text extraction (cached)
  * note rendering (YAML-safe)
  * synthesis matrix regeneration (rebuilt from the DB, never appended)
  * LM Studio client (structured output; reasoning off via reasoning_effort)

Two ingestion paths write through the same functions:
  * bibliomancer update -> scheduled runs, local model in LM Studio (source='qwen')
  * tools/kb_batch.py   -> bulk seeding driven by Claude Code (source='claude')
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

# --------------------------------------------------------------------------
# Paths
#
# Three locations, deliberately separate:
#   KB_HOME     everything derived (DB, notes, caches, logs): config data_dir.
#               LOCAL disk - a run died on 2026-09-22 when a file on the
#               Google Drive mount became briefly unwritable.
#   PAPERS_DIR  the source PDFs. May stay on Google Drive; only ever read.
#   EXPORT_DIR  a synced snapshot for other machines and other agents,
#               overwritten after every run by export.py.
#
# All three come from config.yaml (see config.py); the BIBLIOMANCER_HOME,
# BIBLIOMANCER_PAPERS_DIR and BIBLIOMANCER_EXPORT_DIR environment variables
# override them.
# --------------------------------------------------------------------------
from . import config as _config        # noqa: E402

CONFIG = _config.load()
CONFIG_PATH = _config.CONFIG_PATH
APP_HOME    = _config.APP_HOME
KB_HOME     = os.path.abspath(os.environ.get("BIBLIOMANCER_HOME") or CONFIG["data_dir"])

BASE_DIR     = KB_HOME                            # kept for older scripts
PAPERS_DIR   = os.environ.get("BIBLIOMANCER_PAPERS_DIR") or CONFIG["papers_dir"]
EXPORT_DIR   = os.environ.get("BIBLIOMANCER_EXPORT_DIR") or CONFIG["export_dir"]
NOTES_DIR    = os.path.join(KB_HOME, "Notes")
SCOUTS_DIR   = os.path.join(KB_HOME, "Scouts")
PROJECTS_DIR = os.path.join(KB_HOME, "Projects")
CACHE_DIR    = os.path.join(KB_HOME, "cache")

TEXT_CACHE  = os.path.join(CACHE_DIR, "text")      # extracted PDF text, keyed by hash
LLM_CACHE   = os.path.join(CACHE_DIR, "llm")       # raw model responses, for audit/retry
BATCH_DIR   = os.path.join(CACHE_DIR, "batches")   # Claude-mode seeding batches
EMBED_DIR   = os.path.join(CACHE_DIR, "embeddings")
LOG_DIR     = os.path.join(CACHE_DIR, "logs")
RESEARCH_DIR = os.path.join(KB_HOME, "Research")    # top-N lists and deep-research reports

DB_PATH     = os.path.join(CACHE_DIR, "manifest.db")
INDEX_PATH  = os.path.join(KB_HOME, "synthesis_matrix.md")

# --------------------------------------------------------------------------
# LM Studio
# --------------------------------------------------------------------------
# 127.0.0.1, not localhost: on Windows 'localhost' tries IPv6 first and every new
# connection waits ~2 s (measured by PhotoMancer: 2.07 s vs 0.03 s per request)
LM_BASE         = os.environ.get("BIBLIOMANCER_LM_BASE") or CONFIG["base_url"]
LMS_PATH        = CONFIG["lms_path"]      # '' = found automatically (see _shared.lmstudio)
PARALLEL        = max(1, min(4, int(os.environ.get("BIBLIOMANCER_PARALLEL") or CONFIG.get("parallel") or 1)))
MODEL_NAME      = os.environ.get("BIBLIOMANCER_MODEL") or CONFIG["model"]
EMBED_MODEL     = os.environ.get("BIBLIOMANCER_EMBED_MODEL") or CONFIG["embedding_model"]
REQUEST_TIMEOUT = int(os.environ.get("BIBLIOMANCER_TIMEOUT", "1800"))
REASONING_EFFORT = CONFIG.get("reasoning_effort", "none")   # see note below
MAX_TOKENS      = 12000  # must cover reasoning + JSON; see note below
# Reasoning: switched off with reasoning_effort="none" (kb_config.json key
# "reasoning_effort"; "" = model default). A/B on 10 papers, 2026-09-25:
# median 15 s vs 82 s per paper, 10/10 vs 9/10 valid (one reasoning run used
# all 12000 tokens and was cut off), and richer methodology/findings.
# chat_template_kwargs={'enable_thinking':False}, a /no_think suffix and
# reasoning={'enabled':False} do NOT stop Qwen3.5 reasoning here (2026-09-21).
# Deep research (research.py) still reasons, hence MAX_TOKENS stays large.
# Measured reasoning spend: 2228-3422 tok (27B), ~4254 tok (9B). At the old
# 4000 cap the 9B truncated on every paper and the 27B peaked at 3952/4000,
# i.e. one longer paper from silent failure. 8000 was still not enough: the 9B
# at Q4 truncated 1 paper in 5. Largest prompt measured is 10904 tok, so
# 11.3k + 12000 = 23.3k still fits the 24576 window below.
# Keep CONTEXT_LENGTH >= (MAX_CHARS as tokens) + MAX_TOKENS.

# --------------------------------------------------------------------------
# Extraction limits
#   30000 chars is up to ~11k prompt tokens (measured max 10904). With
#   MAX_TOKENS=12000 the model must therefore be loaded at >= 24576 context.
#   LM Studio's just-in-time loading uses its own default (8192 observed),
#   which makes every request fail with HTTP 400 - so run_weekly.cmd always
#   unloads and then loads explicitly at CONTEXT_LENGTH.
#
#   Two limits are user settings (kb_config.json, set from the control panel):
#     max_doc_tokens  how much of a paper's text the model reads
#     max_pages       how many pages are extracted from each PDF
#   whichever is reached first applies. The character budget and the context
#   window the model is loaded with are derived from max_doc_tokens.
# --------------------------------------------------------------------------
CHARS_PER_TOKEN        = 3.2    # typical paper text (30000 chars ~ 9.4k tokens here)
CHARS_PER_TOKEN_WORST  = 2.75   # dense tables/numbers; sizes the context safely
PROMPT_OVERHEAD_TOKENS = 1600   # system prompt, schema, wrapper
MIN_CONTEXT_LENGTH     = 24576  # never load below this (measured-safe value)
DEFAULT_DOC_TOKENS     = 9400   # -> 30080 chars, context 24576: the tuned setup
DEFAULT_MAX_PAGES      = 12
DOC_TOKENS_RANGE       = (2000, 200000)
PAGES_RANGE            = (1, 1000)


def derived_limits(doc_tokens: int) -> tuple[int, int]:
    """(max_chars, context_length) for a paper-text budget of `doc_tokens`."""
    chars = int(doc_tokens * CHARS_PER_TOKEN)
    need = chars / CHARS_PER_TOKEN_WORST + PROMPT_OVERHEAD_TOKENS + MAX_TOKENS
    ctx = max(MIN_CONTEXT_LENGTH, -(-int(need) // 1024) * 1024)
    return chars, ctx


def _int_setting(env: str, key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(env) or CONFIG.get(key) or default)
    except (TypeError, ValueError):
        v = default
    return min(hi, max(lo, v))


MAX_DOC_TOKENS = _int_setting("BIBLIOMANCER_MAX_DOC_TOKENS", "max_doc_tokens", DEFAULT_DOC_TOKENS,
                              *DOC_TOKENS_RANGE)
MAX_PAGES      = _int_setting("BIBLIOMANCER_MAX_PAGES", "max_pages", DEFAULT_MAX_PAGES, *PAGES_RANGE)
MAX_CHARS, CONTEXT_LENGTH = derived_limits(MAX_DOC_TOKENS)
MIN_USABLE_CHARS = 600     # below this a PDF is treated as scanned / image-only
MAX_ATTEMPTS     = 3       # give up on a file after this many failed runs

SCHEMA_VERSION = 1

# Fields the model must return, in note order.
RECORD_FIELDS = (
    "title", "authors", "year", "research_fields", "keywords",
    "research_question", "methodology", "key_findings",
    "limitations", "core_takeaway",
)
LIST_FIELDS = ("research_fields", "keywords", "key_findings", "limitations")


# Supplementary/SI files are summarised as if they were the parent paper (the
# model has no way to know otherwise), which yields near-duplicate titles. They
# are cheap to spot by filename, so tag them instead.
SUPP_RE = re.compile(
    r"(suppl?(ement(ary|al)?)?|_si[_.\-]|MOESM|mmc\d|[_\- ]SM[_.\-]|supp_?info"
    r"|extended[_ \-]?(data|view|image|figure)s?|[_ \-]ESM([_ \-.]|$))", re.IGNORECASE
)


DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


def pdf_facts(filepath: str, text: str = "") -> dict:
    """
    Metadata recoverable from the PDF without asking a model. Never raises.

    Measured coverage over 40 library PDFs (2026-09-22):
        creationDate year  96%   DOI in text  78%   usable title  85%
    An LLM asked for the year returned 'Unknown' about half the time, so this
    is both cheaper and more reliable. creationDate is the typesetting date,
    which for publisher PDFs is effectively the publication year - but a PDF
    re-saved by a reference manager can carry a later date, so the model's
    answer wins when it has one and this is only the fallback.
    """
    out = {"doi": None, "meta_year": None, "meta_title": None}
    md = {}
    try:
        from .pdf import open_pdf
        with open_pdf(filepath) as doc:
            md = doc.metadata() or {}
    except Exception:
        pass

    m = re.search(r"D:((?:19|20)\d{2})", md.get("creationDate") or "")
    if m:
        out["meta_year"] = m.group(1)

    title = (md.get("title") or "").strip()
    if (len(title) > 15 and not title.lower().endswith(".pdf")
            and "untitled" not in title.lower()):
        out["meta_title"] = title

    # A paper's own DOI sits on its first page. Searching the whole document
    # picks up DOIs from the reference list instead - verified on
    # Gleitz_NicheSphere_supp, which yielded a cited paper's DOI. Supplementary
    # files rarely carry their own DOI at all, so they are skipped entirely.
    if classify_doc(filepath) != "supplementary":
        # Scope by real page boundaries, not a character count: a fixed 4000-char
        # window cut off the DOI on papers with a long first page (verified on
        # Lievin_AMIE), while whole-document search picked up reference-list DOIs.
        head = text or ""
        cut = head.find("--- Page 3 ---")
        if cut != -1:
            head = head[:cut]
        head = _clean_for_doi(head)
        for m in DOI_RE.finditer(head):
            d = _canonical_doi(m.group(0).rstrip(".,;)"))
            if doi_is_plausible(d):
                out["doi"] = d
                break
    return out


_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
# Registries that mint DOIs for datasets and code, never for the paper itself:
# a preprint manuscript that cites its Zenodo archive on page 1 would
# otherwise be catalogued under the dataset's DOI.
DATA_DOI_PREFIXES = ("10.5281/", "10.6084/", "10.5061/", "10.17632/", "10.7910/")


def _clean_for_doi(text: str) -> str:
    """
    Undo typesetting that breaks DOIs in extracted text. All three are in the
    library; Ma 2022 (Nature Reviews) prints
        https://doi.org/10.1038/ <newline> s41580-022-00466-<U+200B>x
      - zero-width spaces used as line-break hints
      - a line break right after the registrant slash
      - a line break after a hyphen inside the suffix
    """
    t = text.translate(_ZERO_WIDTH)
    t = re.sub(r"(10\.\d{4,9}/)\s+(?=[A-Za-z0-9])", r"\1", t)
    t = re.sub(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]*-)\s+(?=[A-Za-z0-9])", r"\1", t)
    return t


def _canonical_doi(d: str) -> str:
    """OUP advance-access URLs append an article number: '.../btac582/6675456'."""
    m = re.match(r"^(10\.1093/[a-z]+/[a-z]+\d+)/\d{5,}$", d, re.I)
    return m.group(1) if m else d


def doi_is_plausible(d: str) -> bool:
    """
    Reject DOIs that are clearly not a paper's own identifier. Seen in the
    library: journal-template placeholders ("10.1093/bioinformatics/xxxxx",
    shared by three unrelated papers), a figshare prefix with no suffix
    ("10.6084/m9"), and dataset DOIs from Zenodo and similar registries.
    Any of these would make unrelated papers look like duplicates.
    """
    suffix = d.split("/", 1)[1] if "/" in d else ""
    if len(suffix) < 5 or re.search(r"x{3,}", suffix, re.I):
        return False
    if d.lower().startswith(DATA_DOI_PREFIXES):
        return False
    return not d.endswith(("-", ".", "_"))


def classify_doc(rel_path: str) -> str:
    return "supplementary" if SUPP_RE.search(os.path.basename(rel_path)) else "paper"


def ensure_dirs() -> None:
    for d in (NOTES_DIR, CACHE_DIR, TEXT_CACHE, LLM_CACHE, BATCH_DIR, LOG_DIR):
        os.makedirs(d, exist_ok=True)


# ==========================================================================
# Logging
# ==========================================================================
class Logger:
    """Prints to stdout and appends to a dated log file under cache/logs."""

    def __init__(self, name: str):
        ensure_dirs()
        stamp = datetime.now().strftime("%Y%m%d")
        self.path = os.path.join(LOG_DIR, f"{name}_{stamp}.log")

    def __call__(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        try:
            print(line, flush=True)
        except Exception:
            pass  # pythonw / redirected console
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass


# ==========================================================================
# Database
# ==========================================================================
def connect() -> sqlite3.Connection:
    """
    Open the manifest.

    The DB lives in the Google-Drive-synced cache folder, so we deliberately
    avoid WAL (which adds -wal/-shm sidecar files that Drive syncs out of step
    with the main file) and keep connections short-lived.
    """
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    ensure_dirs()
    if PAPERS_DIR:
        os.makedirs(PAPERS_DIR, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS processed_papers (
                file_hash        TEXT PRIMARY KEY,
                rel_path         TEXT,
                filename         TEXT,
                processed_at     TEXT,
                source           TEXT,
                note_filename    TEXT,
                title            TEXT,
                year             TEXT,
                authors          TEXT,
                research_fields  TEXT,
                keywords         TEXT,
                research_question TEXT,
                methodology      TEXT,
                key_findings     TEXT,
                limitations      TEXT,
                core_takeaway    TEXT,
                doc_type         TEXT DEFAULT 'paper',
                doi              TEXT,
                meta_year        TEXT
            );

            CREATE TABLE IF NOT EXISTS failures (
                file_hash    TEXT PRIMARY KEY,
                rel_path     TEXT,
                last_attempt TEXT,
                attempts     INTEGER DEFAULT 0,
                reason       TEXT
            );

            -- Avoids re-hashing 500+ files over the Drive mount every week.
            CREATE TABLE IF NOT EXISTS scan_cache (
                rel_path  TEXT PRIMARY KEY,
                size      INTEGER,
                mtime     REAL,
                file_hash TEXT
            );

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            -- Which setup (model, prompt, limits, reasoning) wrote each note,
            -- like PhotoMancer's per-configuration results.
            CREATE TABLE IF NOT EXISTS paper_setup (
                file_hash  TEXT PRIMARY KEY,
                setup_key  TEXT,
                setup_json TEXT
            );

            -- Fingerprint of every note as this program last wrote it: a note
            -- that no longer matches was edited by hand and is never overwritten.
            CREATE TABLE IF NOT EXISTS notes_written (
                note_filename TEXT PRIMARY KEY,
                sha           TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_papers_year  ON processed_papers(year);
            CREATE INDEX IF NOT EXISTS idx_papers_path  ON processed_papers(rel_path);
            """
        )
        # Migrations for databases created by an earlier revision.
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(processed_papers)")}
        for col in ("doi", "meta_year", "text_source"):
            if col not in cols:
                conn.execute(f"ALTER TABLE processed_papers ADD COLUMN {col} TEXT")
        # Indexed only after the migration: on a pre-existing database the
        # column does not exist yet when the CREATE TABLE script runs.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_papers_doi ON processed_papers(doi)")
        if "doc_type" not in cols:
            conn.execute("ALTER TABLE processed_papers ADD COLUMN doc_type TEXT DEFAULT 'paper'")
            for r in conn.execute("SELECT file_hash, rel_path FROM processed_papers").fetchall():
                conn.execute(
                    "UPDATE processed_papers SET doc_type = ? WHERE file_hash = ?",
                    (classify_doc(r["rel_path"] or ""), r["file_hash"]),
                )

        # First run with note protection: fingerprint the notes as they are, so
        # only edits made from now on count as hand edits.
        if not conn.execute("SELECT 1 FROM notes_written LIMIT 1").fetchone():
            rows = [(r["note_filename"],) for r in conn.execute(
                "SELECT note_filename FROM processed_papers WHERE note_filename IS NOT NULL")]
            conn.executemany("INSERT OR IGNORE INTO notes_written VALUES (?, ?)",
                             [(n, _file_sha(os.path.join(NOTES_DIR, n))) for (n,) in rows
                              if os.path.exists(os.path.join(NOTES_DIR, n))])
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
    finally:
        conn.close()


def processed_hashes(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT file_hash FROM processed_papers")}


def giving_up_hashes(conn: sqlite3.Connection) -> set[str]:
    return {
        r[0]
        for r in conn.execute(
            "SELECT file_hash FROM failures WHERE attempts >= ?", (MAX_ATTEMPTS,)
        )
    }


def record_failure(conn: sqlite3.Connection, file_hash: str, rel_path: str, reason: str) -> None:
    conn.execute(
        """
        INSERT INTO failures (file_hash, rel_path, last_attempt, attempts, reason)
        VALUES (?, ?, ?, 1, ?)
        ON CONFLICT(file_hash) DO UPDATE SET
            last_attempt = excluded.last_attempt,
            attempts     = failures.attempts + 1,
            reason       = excluded.reason
        """,
        (file_hash, rel_path, now_iso(), reason[:500]),
    )
    conn.commit()


def clear_failure(conn: sqlite3.Connection, file_hash: str) -> None:
    conn.execute("DELETE FROM failures WHERE file_hash = ?", (file_hash,))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ==========================================================================
# Scanning & hashing
# ==========================================================================
def compute_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as fh:
        while chunk := fh.read(1 << 20):
            hasher.update(chunk)
    return hasher.hexdigest()


def iter_pdfs():
    """Yield (full_path, rel_path) for every PDF under Database/, sorted."""
    found = []
    for root, dirs, files in os.walk(PAPERS_DIR):
        dirs[:] = [d for d in dirs if not d.startswith((".", "~$"))]
        for name in files:
            if not name.lower().endswith(".pdf"):
                continue
            if name.startswith((".", "~$")):
                continue
            full = os.path.join(root, name)
            found.append((full, os.path.relpath(full, PAPERS_DIR)))
    found.sort(key=lambda t: t[1].lower())
    return found


def hash_with_cache(conn: sqlite3.Connection, full_path: str, rel_path: str) -> str:
    """
    Hash a file, skipping the read when size+mtime are unchanged since last scan.
    Turns a ~5 minute weekly rescan of 542 files into a few seconds.
    """
    try:
        st = os.stat(full_path)
    except OSError:
        return compute_sha256(full_path)

    row = conn.execute(
        "SELECT size, mtime, file_hash FROM scan_cache WHERE rel_path = ?", (rel_path,)
    ).fetchone()
    if row and row["size"] == st.st_size and abs(row["mtime"] - st.st_mtime) < 1.0:
        return row["file_hash"]

    digest = compute_sha256(full_path)
    conn.execute(
        "INSERT OR REPLACE INTO scan_cache (rel_path, size, mtime, file_hash) VALUES (?, ?, ?, ?)",
        (rel_path, st.st_size, st.st_mtime, digest),
    )
    conn.commit()
    return digest


def pending_papers(conn: sqlite3.Connection, include_failed: bool = False):
    """Return [(full_path, rel_path, file_hash)] for papers not yet in the manifest."""
    done = processed_hashes(conn)
    skip = set() if include_failed else giving_up_hashes(conn)
    out, seen = [], set()
    for full, rel in iter_pdfs():
        try:
            digest = hash_with_cache(conn, full, rel)
        except OSError as exc:
            print(f"  ! unreadable: {rel} ({exc})", file=sys.stderr)
            continue
        if digest in done or digest in skip or digest in seen:
            continue  # already catalogued, given up on, or a duplicate copy
        seen.add(digest)
        out.append((full, rel, digest))
    return out


# ==========================================================================
# PDF text extraction
# ==========================================================================
def text_cache_path(file_hash: str) -> str:
    return os.path.join(TEXT_CACHE, f"{file_hash}.txt")


def _pages_path(file_hash: str) -> str:
    return os.path.join(TEXT_CACHE, f"{file_hash}.pages")


def _cached_pages_ok(file_hash: str) -> bool:
    """
    Does the cached text cover the pages the current MAX_PAGES asks for?
    The sidecar holds '<page limit used> <pages in the PDF>'. Caches written
    before the setting existed have no sidecar and were made with 12 pages.
    """
    try:
        with open(_pages_path(file_hash), encoding="utf-8") as fh:
            used, total = (int(x) for x in fh.read().split()[:2])
        return min(total, used) == min(total, MAX_PAGES)
    except (OSError, ValueError):
        return MAX_PAGES == DEFAULT_MAX_PAGES


def extract_pdf_text(filepath: str, file_hash: str, use_cache: bool = True):
    """
    Return (text, info). `info` is None on success, or a string describing why
    the text is unusable. Never raises on a malformed PDF.
    """
    cached = text_cache_path(file_hash)
    if use_cache and os.path.exists(cached):
        try:
            with open(cached, encoding="utf-8") as fh:
                text = fh.read()
            # Vision-OCR text is legitimately shorter than native text (a
            # one-page scanned table is ~400 chars), so it has its own bar -
            # otherwise every re-run would pay for OCR again. It is also kept
            # whatever the page setting: re-running OCR costs minutes a page.
            ocr = text_source(file_hash) == "vision-ocr"
            floor = VISION_MIN_CHARS if ocr else MIN_USABLE_CHARS
            if len(text.strip()) >= floor and (ocr or _cached_pages_ok(file_hash)):
                return text, None
        except OSError:
            pass

    from .pdf import Encrypted, open_pdf

    doc = None
    try:
        doc = open_pdf(filepath)
        parts = []
        page_total = doc.page_count
        for page_num in range(min(doc.page_count, MAX_PAGES)):
            try:
                parts.append(f"\n--- Page {page_num + 1} ---\n" + doc.text(page_num))
            except Exception as exc:
                parts.append(f"\n--- Page {page_num + 1} (unreadable: {exc}) ---\n")
        text = "".join(parts)
    except Encrypted:
        return "", "encrypted (password required)"
    except Exception as exc:
        return "", f"pdf open/parse failed: {type(exc).__name__}: {exc}"
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    if len(text.strip()) < MIN_USABLE_CHARS:
        return text, (
            f"only {len(text.strip())} chars of extractable text "
            "(scanned or image-only PDF - needs OCR)"
        )

    try:
        with open(cached, "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(_pages_path(file_hash), "w", encoding="utf-8") as fh:
            fh.write(f"{MAX_PAGES} {page_total}")
    except OSError:
        pass
    return text, None


# ==========================================================================
# Vision OCR fallback for scanned / image-only PDFs
#
# The production model (Qwen3.5-9B) ships with a vision projector, so pages
# can be rendered to images and transcribed by the model already loaded - no
# Tesseract, no extra download. Verified 2026-09-23. Two settings matter:
#   * 200 dpi. At 110 dpi a dense page came out 680x908 and the model spent its
#     entire 6000-token budget deliberating without transcribing anything.
#   * A terse "you are an OCR engine" system prompt plus a 12000-token budget.
# With reasoning switched off (reasoning_effort=none) a page takes ~6 s
# (was ~75 s with reasoning); still a fallback, never the default path.
# Pages are rendered with PDFium (pdf.py).
# ==========================================================================
VISION_DPI        = 200
VISION_MAX_PAGES  = min(6, MAX_PAGES)   # scans are rarely worth more than 6 pages
VISION_MAX_SIDE   = 1800     # px; caps image tokens on large-format pages
VISION_MIN_CHARS  = 150      # a genuinely short scan (one table) is ~400 chars
VISION_PROMPT = (
    "You are an OCR engine. Output only the text visible in the image, verbatim, "
    "in reading order. Do not describe, summarise or analyse. Keep any internal "
    "deliberation to a few words and start transcribing immediately."
)


def text_source_path(file_hash: str) -> str:
    return os.path.join(TEXT_CACHE, f"{file_hash}.source")


def text_source(file_hash: str) -> str:
    """'vision-ocr' if the cached text came from the vision fallback."""
    try:
        with open(text_source_path(file_hash), encoding="utf-8") as fh:
            return fh.read().strip() or "pdf-text"
    except OSError:
        return "pdf-text"


def vision_extract(filepath: str, file_hash: str, log=print):
    """
    Transcribe the first pages of an image-only PDF with the loaded vision
    model. Returns (text, info) exactly like extract_pdf_text.
    """
    import base64
    import requests
    from .pdf import Encrypted, open_pdf

    try:
        doc = open_pdf(filepath)
    except Encrypted:
        return "", "encrypted (password required)"
    except Exception as exc:
        return "", f"vision OCR: cannot open PDF: {exc}"

    parts, total = [], 0
    try:
        for i in range(min(doc.page_count, VISION_MAX_PAGES)):
            scale = VISION_DPI / 72.0
            longest = max(doc.size(i)) * scale
            if longest > VISION_MAX_SIDE:
                scale *= VISION_MAX_SIDE / longest
            png = doc.png(i, scale)
            payload = {
                "model": MODEL_NAME,
                "messages": [
                    {"role": "system", "content": VISION_PROMPT},
                    {"role": "user", "content": [
                        {"type": "text", "text": "Transcribe this page."},
                        {"type": "image_url", "image_url": {
                            "url": "data:image/png;base64," + base64.b64encode(png).decode()}},
                    ]},
                ],
                "temperature": 0.0,
                "max_tokens": MAX_TOKENS,
                "stream": False,
                **reasoning_args(),
            }
            try:
                r = requests.post(f"{LM_BASE}/chat/completions", json=payload,
                                  timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                ch = r.json()["choices"][0]
                page_text = (ch["message"].get("content") or "").strip()
                if ch.get("finish_reason") == "length":
                    log(f"      page {i + 1}: truncated, keeping partial text")
            except Exception as exc:
                log(f"      page {i + 1}: vision request failed ({type(exc).__name__})")
                page_text = ""
            log(f"      page {i + 1}: {len(page_text)} chars via vision")
            parts.append(f"\n--- Page {i + 1} ---\n{page_text}")
            total += len(page_text)
            if total >= MAX_CHARS:
                break
    finally:
        doc.close()

    text = "".join(parts)
    if len(text.strip()) - 20 * len(parts) < VISION_MIN_CHARS:
        return text, (f"no readable text even via vision OCR "
                      f"({total} chars over {len(parts)} page(s))")
    try:
        with open(text_cache_path(file_hash), "w", encoding="utf-8") as fh:
            fh.write(text)
        with open(text_source_path(file_hash), "w", encoding="utf-8") as fh:
            fh.write("vision-ocr")
    except OSError:
        pass
    return text, None


# ==========================================================================
# Record validation
# ==========================================================================
def normalise_record(data: dict) -> dict:
    """Coerce a model/Claude-produced dict into the canonical record shape."""
    if not isinstance(data, dict):
        raise ValueError("record is not a JSON object")

    out: dict = {}
    for field in RECORD_FIELDS:
        value = data.get(field)
        if field in LIST_FIELDS:
            if isinstance(value, str):
                value = [v.strip() for v in re.split(r"[;,\n]", value) if v.strip()]
            elif not isinstance(value, list):
                value = []
            out[field] = [str(v).strip() for v in value if str(v).strip()]
        else:
            out[field] = ("" if value is None else str(value)).strip()

    if not out["title"]:
        raise ValueError("record has no title")

    m = re.search(r"(1[89]\d{2}|20\d{2})", out["year"])
    out["year"] = m.group(1) if m else "Unknown"
    out["authors"] = normalise_authors(out["authors"])
    return out


MAX_AUTHORS = 6
_INITIALS_RE = re.compile(r"^(?:[A-Z]{1,3}\.?(?:[\s\-]?[A-Z]\.?){0,3})$")


def normalise_authors(s: str) -> str:
    """'Surname A, Surname B, ...' capped at MAX_AUTHORS, then 'et al.'.

    Without reasoning the model often lists every author (21 on one test
    paper) and mixes styles ('Ochocka, N., Kaminska, B.', 'Stuart, Tim;
    Satija, Rahul'). Initial-only pieces are glued back to their surname.
    """
    s = re.sub(r"\s+", " ", s or "").strip().rstrip(",;")
    etal = bool(re.search(r"\bet al\.?$", s, re.I))
    s = re.sub(r",?\s*\bet al\.?$", "", s, flags=re.I).strip()
    s = re.sub(r",?\s+(?:and|&)\s+", ", ", s)       # 'You and Snell' -> 'You, Snell'
    if not s:
        return ""
    names: list[str] = []
    if ";" in s:                                   # 'Surname, Given; Surname, Given'
        for part in (p.strip() for p in s.split(";")):
            if "," in part:
                sur, given = (x.strip() for x in part.split(",", 1))
                ini = "".join(w[0] for w in re.split(r"[\s\-]+", given) if w[:1].isalpha())
                part = f"{sur} {ini.upper()}".strip()
            if part:
                names.append(part)
    else:
        for part in (p.strip() for p in s.split(",")):
            if not part:
                continue
            if names and _INITIALS_RE.match(part):  # 'Ochocka, N.' -> 'Ochocka N.'
                names[-1] = f"{names[-1]} {part}"
            else:
                names.append(part)
    if len(names) > MAX_AUTHORS:
        names, etal = names[:MAX_AUTHORS], True
    return ", ".join(names) + (" et al." if etal else "")


# ==========================================================================
# Note rendering
# ==========================================================================
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def note_filename(record: dict, file_hash: str) -> str:
    """Collision-proof: same year + similar title still get distinct files."""
    title = _UNSAFE.sub(" ", record["title"])
    title = re.sub(r"\s+", " ", title).strip().rstrip(". ")
    title = title[:60].strip() or "Untitled"
    return f"{record['year']}_{title}_{file_hash[:8]}.md"


def _yaml(value) -> str:
    """JSON encoding is valid YAML for scalars and flow sequences, and escapes quotes."""
    return json.dumps(value, ensure_ascii=False)


def render_note(record: dict, file_hash: str, rel_path: str, source: str,
                facts: dict | None = None) -> str:
    facts = facts or {}
    lines = [
        "---",
        f"title: {_yaml(record['title'])}",
        f"authors: {_yaml(record['authors'])}",
        f"year: {_yaml(record['year'])}",
        f"doi: {_yaml(facts.get('doi') or '')}",
        f"sha256: {file_hash}",
        f"relative_path: {_yaml(rel_path)}",
        f"fields: {_yaml(record['research_fields'])}",
        f"tags: {_yaml(record['keywords'])}",
        f"doc_type: {_yaml(classify_doc(rel_path))}",
        f"text_source: {_yaml(facts.get('text_source') or 'pdf-text')}",
        f"summarised_by: {_yaml(source)}",
        f"processed_at: {_yaml(now_iso())}",
        "---",
        "",
        f"# {record['title']}",
        "",
        f"- **Authors:** {record['authors']}",
        f"- **Year:** {record['year']}",
        f"- **Fields:** {', '.join(record['research_fields'])}",
        f"- **Keywords:** {', '.join(record['keywords'])}",
        f"- **File Location:** `{rel_path}`",
        "",
    ]
    if facts.get("text_source") == "vision-ocr":
        lines += ["> _Text recovered by vision OCR from a scanned PDF; expect "
                  "occasional transcription errors in names and numbers._", ""]
    lines += [
        "## Research Question",
        record["research_question"] or "_not stated_",
        "",
        "## Methodology",
        record["methodology"] or "_not stated_",
        "",
        "## Key Findings",
    ]
    lines += [f"- {x}" for x in record["key_findings"]] or ["_none extracted_"]
    lines += ["", "## Limitations"]
    lines += [f"- {x}" for x in record["limitations"]] or ["_none stated_"]
    lines += ["", "## Core Takeaway", record["core_takeaway"] or "_not stated_", ""]

    # Papers reported in a weekly literature-scout brief get an extra section
    # carrying the competitive/citation consequence for the owner's own projects,
    # including his verbatim comments on the brief. Wrapped defensively: a
    # missing or malformed scout index must never stop a paper being ingested.
    try:
        from . import projects as kb_projects
        section = kb_projects.section_for(
            (facts or {}).get("doi"), record.get("title"))
        if section:
            lines += ["", section]
    except Exception as exc:                      # pragma: no cover
        print(f"  (scout intersection skipped: {exc})", file=sys.stderr)

    return "\n".join(lines)


def _file_sha(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return ""


def write_note(conn: sqlite3.Connection, note_name: str, content: str) -> str:
    """Write a note unless the user edited it since this program last wrote it.

    A hand-edited note is kept; the new version goes next to it as
    <name>.generated.md and a WARNING line is printed. Returns the file written.
    """
    path = os.path.join(NOTES_DIR, note_name)
    row = conn.execute("SELECT sha FROM notes_written WHERE note_filename = ?",
                       (note_name,)).fetchone()
    if os.path.exists(path) and row and row["sha"] and _file_sha(path) != row["sha"]:
        side = path[:-3] + ".generated.md"
        with open(side, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        print(f"    WARNING Notes/{note_name} was edited by hand - kept; the new version is "
              f"{os.path.basename(side)}", flush=True)
        return side
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    os.replace(tmp, path)
    conn.execute("INSERT OR REPLACE INTO notes_written VALUES (?, ?)", (note_name, _file_sha(path)))
    return path


def current_setup() -> tuple[str, dict]:
    """(key, description) of what writes notes now - model, prompt, limits, reasoning."""
    s = {"model": MODEL_NAME, "reasoning_effort": REASONING_EFFORT,
         "prompt_sha8": hashlib.sha1(SYSTEM_PROMPT.encode("utf-8")).hexdigest()[:8],
         "max_doc_tokens": MAX_DOC_TOKENS, "max_pages": MAX_PAGES}
    return hashlib.sha1(json.dumps(s, sort_keys=True).encode()).hexdigest()[:10], s


def commit_paper(conn: sqlite3.Connection, record: dict, file_hash: str,
                 rel_path: str, filename: str, source: str,
                 facts: dict | None = None) -> str:
    """Write the note and upsert the manifest row. Returns the note filename."""
    ensure_dirs()
    note_name = note_filename(record, file_hash)
    write_note(conn, note_name, render_note(record, file_hash, rel_path, source, facts))
    key, setup = current_setup()
    conn.execute("INSERT OR REPLACE INTO paper_setup VALUES (?, ?, ?)",
                 (file_hash, key, json.dumps(setup)))

    conn.execute(
        """
        INSERT OR REPLACE INTO processed_papers
        (file_hash, rel_path, filename, processed_at, source, note_filename,
         title, year, authors, research_fields, keywords,
         research_question, methodology, key_findings, limitations, core_takeaway,
         doc_type, doi, meta_year, text_source)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            file_hash, rel_path, filename, now_iso(), source, note_name,
            record["title"], record["year"], record["authors"],
            json.dumps(record["research_fields"], ensure_ascii=False),
            json.dumps(record["keywords"], ensure_ascii=False),
            record["research_question"], record["methodology"],
            json.dumps(record["key_findings"], ensure_ascii=False),
            json.dumps(record["limitations"], ensure_ascii=False),
            record["core_takeaway"],
            classify_doc(rel_path),
            (facts or {}).get("doi"),
            (facts or {}).get("meta_year"),
            (facts or {}).get("text_source") or "pdf-text",
        ),
    )
    clear_failure(conn, file_hash)
    conn.commit()
    return note_name


def record_from_row(row) -> dict:
    """Rebuild the model's record dict from a processed_papers row."""
    def js(v):
        try:
            return json.loads(v or "[]")
        except Exception:
            return []
    return {
        "title": row["title"] or "", "authors": row["authors"] or "",
        "year": row["year"] or "Unknown",
        "research_fields": js(row["research_fields"]), "keywords": js(row["keywords"]),
        "research_question": row["research_question"] or "",
        "methodology": row["methodology"] or "",
        "key_findings": js(row["key_findings"]), "limitations": js(row["limitations"]),
        "core_takeaway": row["core_takeaway"] or "",
    }


def rerender_note(conn: sqlite3.Connection, file_hash: str) -> str | None:
    """
    Regenerate a note from the catalogue alone - no model call. Every field the
    model produced is stored in the DB, so notes can be refreshed whenever the
    rendering changes (a new section, corrected metadata) at zero GPU cost.
    Keeps the existing filename so links in the matrix stay valid.
    """
    row = conn.execute("SELECT * FROM processed_papers WHERE file_hash = ?",
                       (file_hash,)).fetchone()
    if row is None:
        return None
    facts = {"doi": row["doi"], "meta_year": row["meta_year"],
             "text_source": row["text_source"] or "pdf-text"}
    name = row["note_filename"] or note_filename(record_from_row(row), file_hash)
    write_note(conn, name, render_note(record_from_row(row), file_hash, row["rel_path"],
                                       row["source"] or "qwen", facts))
    conn.commit()
    return name


# ==========================================================================
# Synthesis matrix - always regenerated from the DB
# ==========================================================================
def _cell(text: str, limit: int = 300) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).replace("|", "\\|").strip()
    return text[: limit - 1] + "\u2026" if len(text) > limit else text


def rebuild_matrix(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT year, title, research_fields, keywords, core_takeaway,
               note_filename, doc_type
        FROM processed_papers
        ORDER BY (year = 'Unknown'), year DESC, title COLLATE NOCASE
        """
    ).fetchall()

    out = [
        "# Synthesis Matrix",
        "",
        f"_{len(rows)} papers - regenerated {now_iso()} by scripts/rebuild_matrix.py_",
        "",
        "| Year | Title | Research Fields | Keywords | Core Takeaway | Note |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        fields = ", ".join(json.loads(r["research_fields"] or "[]"))
        kws = ", ".join(json.loads(r["keywords"] or "[]"))
        stem = os.path.splitext(r["note_filename"] or "")[0]
        title = r["title"]
        if r["doc_type"] == "supplementary":
            title = f"(suppl.) {title}"
        out.append(
            f"| {_cell(r['year'], 12)} | {_cell(title, 160)} | {_cell(fields, 120)} "
            f"| {_cell(kws, 160)} | {_cell(r['core_takeaway'], 320)} | [[{stem}]] |"
        )

    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, INDEX_PATH)
    return len(rows)


# ==========================================================================
# LM Studio client
# ==========================================================================
PAPER_SCHEMA = {
    "type": "object",
    "properties": {
        "title":             {"type": "string"},
        "authors":           {"type": "string"},
        "year":              {"type": "string"},
        "research_fields":   {"type": "array", "items": {"type": "string"}},
        "keywords":          {"type": "array", "items": {"type": "string"}},
        "research_question": {"type": "string"},
        "methodology":       {"type": "string"},
        "key_findings":      {"type": "array", "items": {"type": "string"}},
        "limitations":       {"type": "array", "items": {"type": "string"}},
        "core_takeaway":     {"type": "string"},
    },
    "required": list(RECORD_FIELDS),
}

# config research_domain, e.g. "marine ecology and climate science": the fields you work in, so the model reads papers as a specialist.
RESEARCH_DOMAIN = (CONFIG.get("research_domain") or "").strip().rstrip(".")
SYSTEM_PROMPT = (
    "You are an expert scientific research assistant"
    + (f" specialising in {RESEARCH_DOMAIN}" if RESEARCH_DOMAIN else "") + ".\n"
    "Analyse the supplied paper text and return metadata describing it.\n"
    "Rules:\n"
    "- Use only information present in the text. Never invent authors, years or findings.\n"
    "- 'authors' is a plain string: 'Surname A, Surname B, ...' (up to 6, then 'et al.').\n"
    "- 'year' is the 4-digit year THIS document was published or posted. Look for "
    "'Published online', 'Received/Accepted', the journal volume and date line, a DOI "
    "containing a year, or a preprint stamp such as '2026.02.27'. Answer 'Unknown' only "
    "if no year appears anywhere.\n"
    "- 'research_fields' are 2-4 broad domains that describe THIS paper's own subject "
    "(e.g. 'Immunology', 'Signal processing', 'Deep learning'). Do not add a field "
    "just because it is in these examples or in your specialisation above.\n"
    "- 'keywords' are 4-8 specific technical terms, including method and tool names.\n"
    "- 'key_findings' are 2-5 concise sentences stating what THIS study found or built.\n"
    "- 'limitations' are 2-5 concise sentences describing weaknesses OF THIS STUDY ITSELF: "
    "cohort or sample size, single-site or single-tissue data, study design, missing "
    "controls, lack of validation, the computational cost of the authors' own approach, or "
    "caveats the authors state about their own work.\n"
    "  CRITICAL: do NOT list shortcomings of prior work, of competing methods, or of the "
    "field in general. If the paper benchmarks or criticises other methods, those "
    "criticisms are 'key_findings', NOT 'limitations'. Ask: would a reviewer raise this "
    "against THIS paper? If no, it does not belong in 'limitations'.\n"
    "- 'core_takeaway' is one concrete sentence naming the actual result, not a generic "
    "aspiration.\n"
    "- If the document is supplementary material, a thesis, a book chapter or a review, "
    "say so explicitly in 'methodology'."
)

def reasoning_args() -> dict:
    """Request fields that set the model's reasoning (none = off, the default)."""
    return {"reasoning_effort": REASONING_EFFORT} if REASONING_EFFORT else {}


_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def lm_available(timeout: int = 10):
    """Return (ok, detail). Used as a guard before any weekly run."""
    import requests
    try:
        resp = requests.get(f"{LM_BASE}/models", timeout=timeout)
        resp.raise_for_status()
        ids = [m.get("id") for m in resp.json().get("data", [])]
        if MODEL_NAME not in ids:
            return False, f"model '{MODEL_NAME}' not offered by server; available: {ids}"
        return True, f"server up, model '{MODEL_NAME}' available"
    except Exception as exc:
        return False, f"LM Studio not reachable at {LM_BASE}: {exc}"


def _strip_to_json(content: str) -> str:
    content = _THINK_RE.sub("", content or "").strip()
    if content.startswith("```"):
        lines = content.splitlines()
        lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        content = content[start : end + 1]
    return content


CONCISE_DIRECTIVE = (
    "\n\nBE CONCISE. A previous attempt on this paper ran out of output budget. "
    "Keep your reasoning brief. Each list item is one short sentence, at most "
    "4 items per list; research_question, methodology and core_takeaway are at "
    "most two sentences each."
)
RETRY_MAX_CHARS = max(8000, MAX_CHARS // 2)   # 15040 at the default budget

_ALNUM_RE = re.compile(r"\w", re.UNICODE)


def clean_for_model(text: str) -> str:
    """Drop text that only costs tokens: lines without a single letter or digit.

    Scatter plots drawn with font glyphs come out of the PDF as thousands of
    '●' lines (Lun 2018: 30,000 of 100,000 characters), which pushed a
    96,000-character excerpt to 65k tokens - over the 49k context - and carry
    nothing a summary can use. Page markers and every line with words or
    numbers are kept; runs of blank lines are squeezed.
    """
    out, blank = [], 0
    for line in text.splitlines():
        s = line.strip()
        if not s:
            blank += 1
            if blank <= 1:
                out.append("")
            continue
        blank = 0
        if s.startswith("--- Page") or _ALNUM_RE.search(s):
            out.append(line)
    return "\n".join(out)


def _context_overflow(resp) -> tuple[int, int] | None:
    """(prompt tokens, context size) when LM Studio refused a too-long request."""
    m = re.search(r"request \((\d+) tokens\) exceeds the available context size \((\d+) tokens\)",
                  getattr(resp, "text", "") or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def query_lm(paper_text: str, file_hash: str | None = None,
             max_chars: int | None = None, concise: bool = False, _fit_retry: bool = True):
    """
    Ask the local model for a paper record.
    Returns (record_dict_or_None, detail_string).

    `max_chars` / `concise` exist for the truncation retry: when reasoning ran
    away and consumed the whole budget, retrying the same request identically
    cannot work, so the retry halves the input and asks for brevity.

    A text that tokenises more densely than planned (formulas, tables) can
    still exceed the loaded context; the request is then repeated once with the
    excerpt cut to what fits, measured from LM Studio's own token count.
    """
    import requests

    excerpt = clean_for_model(paper_text)[:max_chars or MAX_CHARS]
    system = SYSTEM_PROMPT + (CONCISE_DIRECTIVE if concise else "")
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Paper text:\n{excerpt}"},
        ],
        "temperature": 0.1,
        "max_tokens": MAX_TOKENS,
        "stream": False,
        # Qwen3.5 reasons by default; left on, it spends 2.5-12k tokens (and
        # 1-4 min) per paper. Only reasoning_effort switches it off here.
        **reasoning_args(),
        # Structured output: the server constrains decoding to the schema.
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "paper_record", "strict": True, "schema": PAPER_SCHEMA},
        },
    }

    try:
        resp = requests.post(
            f"{LM_BASE}/chat/completions",
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        over = _context_overflow(resp) if resp.status_code == 400 else None
        if over and over[1] < CONTEXT_LENGTH:
            # the model is loaded too small (by hand, or by another app): shortening
            # every paper would hide that - say so instead
            return None, (f"model loaded with a {over[1]}-token context, the pipeline needs "
                          f"{CONTEXT_LENGTH} - press Update now (it reloads the model correctly)")
        if over and _fit_retry:
            n_prompt, n_ctx = over
            text_tokens = max(1, n_prompt - PROMPT_OVERHEAD_TOKENS)
            fit = int((n_ctx - MAX_TOKENS - PROMPT_OVERHEAD_TOKENS - 512)
                      * len(excerpt) / text_tokens * 0.95)
            print(f"    text too long for the context ({n_prompt} > {n_ctx} tokens) - "
                  f"retrying with the first {fit:,} characters", flush=True)
            record, detail = query_lm(paper_text, file_hash, max_chars=max(4000, fit),
                                      concise=concise, _fit_retry=False)
            return record, detail if record is None else f"{detail} (shortened to fit)"
        if over:
            return None, f"text too long for the context even shortened ({over[0]} > {over[1]} tokens)"
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:
        return None, f"request failed: {type(exc).__name__}: {exc}"

    try:
        choice = body["choices"][0]
        content = choice["message"].get("content") or ""
        finish = choice.get("finish_reason")
    except (KeyError, IndexError) as exc:
        return None, f"unexpected response shape: {exc}"

    if file_hash:
        try:
            with open(os.path.join(LLM_CACHE, f"{file_hash}.json"), "w", encoding="utf-8") as fh:
                json.dump(body, fh, ensure_ascii=False, indent=2)
        except OSError:
            pass

    if finish == "length":
        return None, "response truncated (max_tokens reached)"

    try:
        data = json.loads(_strip_to_json(content))
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON from model: {exc}; head={content[:200]!r}"

    try:
        return normalise_record(data), "ok"
    except ValueError as exc:
        return None, f"invalid record: {exc}"
