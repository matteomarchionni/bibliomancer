"""
Top-N lists and deep research over the library.

Two things, both written to Research/ in the knowledge-base folder:

  top-N list     title, year, DOI, PDF path and note path of the N papers that
                 best match a search. No model needed beyond the embedding one.

  deep research  the local model reads each of the top N papers - its note
                 plus the paper's own extracted text - against a question, then
                 writes one report that cites them. Two passes, because the
                 model's 24k-token window cannot hold N papers at once:
                   1. per paper: what does THIS paper contribute to the question
                   2. synthesis: one report from those extracts, cited as [n]

    bibliomancer research --query "coral bleaching" --top 10 --list-only
    bibliomancer research --query "coral bleaching" --top 8 \
                          --question "Which species recovered after repeated heatwaves?"
    add --exact for word matching instead of semantic search.

About 2 minutes per paper plus a few minutes for the report on the 9B model.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
from . import core as kb
from . import pipeline as kb_pipeline

RESEARCH_DIR = kb.RESEARCH_DIR

# paper text per extraction call: 14000 chars (~3.5k tokens) at the default
# budget, growing in proportion when max_doc_tokens is raised (the context grows too)
EXCERPT_CHARS = max(14000, int(14000 * kb.MAX_DOC_TOKENS / kb.DEFAULT_DOC_TOKENS))
RETRY_EXCERPT_CHARS = 6000
EXTRACTS_BUDGET = 30000     # all extracts together in the synthesis call (~7.5k tokens)
MAX_TOKENS = kb.MAX_TOKENS  # reasoning + answer; prompt + this must fit CONTEXT_LENGTH

MAP_SYSTEM = (
    "You are a careful research assistant working on a researcher's personal "
    "literature library. You receive ONE paper - a structured summary note written "
    "earlier, plus an excerpt of the paper's own text - and a research question.\n\n"
    "Extract only what THIS paper contributes to the question: specific methods, "
    "datasets, conditions, numbers and conclusions, as the paper states them. Use no "
    "outside knowledge. If the paper does not address the question, set relevance to "
    "'none' and say so in one sentence. 'evidence' holds at most 5 short items, each "
    "a concrete fact from the paper. 'caveats' covers limits of this paper that "
    "matter for the question. Write in the language of the question. Keep your "
    "reasoning brief."
)

MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "relevance": {"type": "string", "enum": ["high", "medium", "low", "none"]},
        "contribution": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "caveats": {"type": "string"},
    },
    "required": ["relevance", "contribution", "evidence", "caveats"],
    "additionalProperties": False,
}

REDUCE_SYSTEM = (
    "You write a literature report for a researcher, using ONLY the per-paper "
    "extracts provided. Each extract comes from one paper of the researcher's own "
    "library and is labelled [n]. Cite every claim with its [n]; several papers "
    "can back one claim ([2][5]). Never invent papers, numbers or citations; if the "
    "extracts do not answer part of the question, say so plainly.\n\n"
    "Structure, in Markdown:\n"
    "## Short answer\n3-6 sentences that answer the question directly.\n"
    "## What the papers show\nGrouped by theme, not paper by paper.\n"
    "## Disagreements and gaps\nWhere papers conflict, and what none of them covers.\n"
    "## Implications\nWhat this means for the question asked, concretely.\n"
    "## Papers that did not help\nOne line each, with [n], or 'None'.\n\n"
    "Write in the language of the question. Keep your reasoning brief."
)

CONCISE = ("\n\nBE CONCISE. A previous attempt ran out of output budget. Keep your "
           "reasoning very short and the answer compact.")

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


def say(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


# ------------------------------------------------------------------ search --
def top_papers(query: str, n: int, semantic: bool = True):
    """
    Best-matching papers, best first: (rows, note). Each row is a dict with
    score, file_hash, title, year, doi, rel_path, note_filename, pdf_path,
    note_path. Falls back to word matching when the semantic index or the
    embedding model is unavailable, and says so in `note`.
    """
    conn = kb.connect()
    by = {r["file_hash"]: r for r in conn.execute("SELECT * FROM processed_papers")}
    found, note, mode = [], "", "semantic"
    if semantic:
        from . import embed as kb_embed
        hits = kb_embed.search(query, k=n)
        if hits is None:
            note = ("Semantic index unavailable (is LM Studio running?) - "
                    "showing exact-word matches instead.")
        else:
            found = [(f"{s:.2f}", by[h]) for s, h in hits if h in by]
    if not found:
        mode = "exact words"
        t = f"%{query}%"
        found = [("", r) for r in conn.execute(
            "SELECT * FROM processed_papers WHERE title LIKE ? OR keywords LIKE ? OR "
            "research_fields LIKE ? OR core_takeaway LIKE ? OR authors LIKE ? "
            "ORDER BY year DESC LIMIT ?", (t, t, t, t, t, n))]
    conn.close()
    rows = []
    for score, r in found[:n]:
        rows.append({
            "score": score, "file_hash": r["file_hash"], "title": r["title"] or "",
            "year": r["year"] or "", "doi": r["doi"] or "", "rel_path": r["rel_path"],
            "note_filename": r["note_filename"] or "",
            "pdf_path": os.path.join(kb.PAPERS_DIR, r["rel_path"]),
            "note_path": os.path.join(kb.NOTES_DIR, r["note_filename"] or ""),
            "mode": mode,
        })
    return rows, note


def _slug(text: str, n: int = 50) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s[:n].strip("-") or "search"


def _stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d_%H%M")


def list_text(query: str, rows: list[dict], question: str = "") -> str:
    mode = rows[0]["mode"] if rows else "-"
    L = [f"BiblioMancer - top {len(rows)} for: \"{query}\"",
         f"Search: {mode}  |  {dt.datetime.now():%Y-%m-%d %H:%M}  |  {kb.KB_HOME}"]
    if question:
        L.append(f"Question: {question}")
    L.append("")
    for i, r in enumerate(rows, 1):
        score = f"[{r['score']}] " if r["score"] else ""
        L += [f"{i:>2}. {score}{r['title']}",
              f"    Year: {r['year'] or '-'}",
              f"    DOI:  {r['doi'] or '-'}",
              f"    PDF:  {r['pdf_path']}",
              f"    Note: {r['note_path']}",
              ""]
    return "\n".join(L)


def save_list(query: str, rows: list[dict], question: str = "", base: str | None = None) -> str:
    os.makedirs(RESEARCH_DIR, exist_ok=True)
    base = base or f"{_stamp()}_{_slug(query)}"
    path = os.path.join(RESEARCH_DIR, f"{base}_top{len(rows)}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(list_text(query, rows, question))
    return path


# ------------------------------------------------------------------- model --
def chat(system: str, user: str, schema: dict | None = None):
    """(content, finish_reason, error). Reasoning is stripped from content."""
    import requests
    payload = {
        "model": kb.MODEL_NAME,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2, "max_tokens": MAX_TOKENS, "stream": False,
    }
    if schema:
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "extract", "strict": True, "schema": schema}}
    try:
        r = requests.post(f"{kb.LM_BASE}/chat/completions", json=payload,
                          timeout=kb.REQUEST_TIMEOUT)
        r.raise_for_status()
        choice = r.json()["choices"][0]
    except Exception as exc:
        return "", None, f"{type(exc).__name__}: {exc}"
    content = _THINK_RE.sub("", choice["message"].get("content") or "").strip()
    return content, choice.get("finish_reason"), ""


def paper_material(row: dict, excerpt_chars: int) -> str:
    try:
        with open(row["note_path"], encoding="utf-8") as fh:
            note = fh.read()
    except OSError:
        note = f"# {row['title']}\n(no note file)"
    try:
        with open(kb.text_cache_path(row["file_hash"]), encoding="utf-8") as fh:
            text = fh.read()[:excerpt_chars]
    except OSError:
        text = ""
    parts = ["SUMMARY NOTE:", note.strip()]
    if text.strip():
        parts += ["", "EXCERPT OF THE PAPER'S OWN TEXT (may be cut off):", text.strip()]
    return "\n".join(parts)


def extract_one(row: dict, question: str) -> dict:
    """What one paper contributes to the question; retried once, shorter, on runaway reasoning."""
    for excerpt, extra in ((EXCERPT_CHARS, ""), (RETRY_EXCERPT_CHARS, CONCISE)):
        user = f"QUESTION:\n{question}\n\n{paper_material(row, excerpt)}"
        content, finish, err = chat(MAP_SYSTEM + extra, user, MAP_SCHEMA)
        if err:
            return {"error": err}
        if finish == "length":
            print("    truncated - retrying shorter and concise", flush=True)
            continue
        try:
            start, end = content.find("{"), content.rfind("}")
            return json.loads(content[start:end + 1])
        except (ValueError, json.JSONDecodeError) as exc:
            return {"error": f"unreadable answer: {exc}"}
    return {"error": "model ran out of output budget twice"}


def extract_block(i: int, row: dict, ex: dict, limit: int) -> str:
    head = f"[{i}] {row['title']} ({row['year'] or 'n.d.'})"
    if "error" in ex:
        return f"{head}\nNot read: {ex['error']}"
    body = [f"Relevance: {ex['relevance']}", f"Contribution: {ex['contribution']}"]
    if ex.get("evidence"):
        body.append("Evidence: " + " | ".join(ex["evidence"][:5]))
    if ex.get("caveats"):
        body.append(f"Caveats: {ex['caveats']}")
    text = "\n".join(body)
    return f"{head}\n{text[:limit]}"


def synthesise(query: str, question: str, rows, extracts) -> tuple[str, str]:
    per = max(600, EXTRACTS_BUDGET // max(1, len(rows)))
    for limit, extra in ((per, ""), (per // 2, CONCISE)):
        blocks = [extract_block(i, r, e, limit) for i, (r, e) in enumerate(zip(rows, extracts), 1)]
        user = (f"QUESTION:\n{question}\n\nSEARCH TOPIC USED TO PICK THE PAPERS:\n{query}\n\n"
                "PER-PAPER EXTRACTS:\n\n" + "\n\n".join(blocks))
        content, finish, err = chat(REDUCE_SYSTEM + extra, user)
        if err:
            return "", err
        if finish == "length":
            print("    report truncated - retrying with shorter extracts, concise", flush=True)
            continue
        return content, ""
    return "", "model ran out of output budget twice"


def report_markdown(query, question, rows, extracts, body, err, minutes) -> str:
    L = [f"# Research report: {question}", "",
         f"- **Search topic:** {query}  ({rows[0]['mode'] if rows else '-'} search, top {len(rows)})",
         f"- **Generated:** {dt.datetime.now():%Y-%m-%d %H:%M} by `{kb.MODEL_NAME}` "
         f"in {minutes:.0f} min",
         "- Written by a local model from your notes and the papers' text. Check any "
         "claim that matters against the paper itself (DOI and PDF below).", ""]
    L += [body.strip() if body else f"> Report could not be written: {err}. "
          "The per-paper extracts below are still usable.", "", "## Sources", "",
          "| # | Paper | Year | DOI | Relevance |", "|---|---|---|---|---|"]
    for i, (r, e) in enumerate(zip(rows, extracts), 1):
        doi = f"[{r['doi']}](https://doi.org/{r['doi']})" if r["doi"] else "-"
        L.append(f"| [{i}] | {r['title'].replace('|', '/')} | {r['year'] or '-'} | {doi} "
                 f"| {e.get('relevance', 'not read')} |")
    L += ["", "## Per-paper extracts", ""]
    for i, (r, e) in enumerate(zip(rows, extracts), 1):
        L += [f"### [{i}] {r['title']}", "",
              f"PDF: `{r['pdf_path']}`  ", f"Note: `{r['note_filename']}`", ""]
        if "error" in e:
            L += [f"_Not read: {e['error']}_", ""]
            continue
        L += [f"**Relevance:** {e['relevance']}", "", e["contribution"], ""]
        L += [f"- {x}" for x in e.get("evidence", [])]
        if e.get("caveats"):
            L += ["", f"_Caveats:_ {e['caveats']}"]
        L.append("")
    return "\n".join(L)


# -------------------------------------------------------------------- main --
def main() -> int:
    ap = argparse.ArgumentParser(description="Top-N list and deep research report.")
    ap.add_argument("--query", required=True, help="search topic used to pick the papers")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--question", default="", help="what the report must answer")
    ap.add_argument("--exact", action="store_true", help="word matching, not semantic")
    ap.add_argument("--list-only", action="store_true", help="write the top-N list and stop")
    ap.add_argument("--no-lm-setup", action="store_true",
                    help="assume the model is already loaded at the right context")
    args = ap.parse_args()
    n = max(1, min(args.top, 100))
    question = args.question.strip() or f"What do these papers say about: {args.query}?"
    t0 = time.time()

    lms_ok = os.path.exists(kb_pipeline.LMS)
    if not args.list_only and not args.no_lm_setup and lms_ok:
        kb_pipeline.run([kb_pipeline.LMS, "server", "start"], "start LM Studio server")

    say("find top papers")
    rows, note = top_papers(args.query, n, semantic=not args.exact)
    if note:
        print(note, flush=True)
    if not rows:
        print("No papers match this search - nothing to do.", flush=True)
        return 1
    base = f"{_stamp()}_{_slug(args.query)}"
    list_path = save_list(args.query, rows, question if not args.list_only else "", base)
    print(f"Top-{len(rows)} list written: {list_path}", flush=True)
    for i, r in enumerate(rows, 1):
        print(f"  {i:>2}. {r['score'] or '':>5} {r['year'] or '':>7}  {r['title'][:90]}", flush=True)
    if args.list_only:
        return 0

    if not args.no_lm_setup and lms_ok:
        kb_pipeline.lm_setup()
    ok, detail = kb.lm_available()
    if not ok:
        print(f"ABORT: {detail}", flush=True)
        return 1

    say("read papers")
    extracts = []
    for i, r in enumerate(rows, 1):
        print(f"[{i}/{len(rows)}] {r['title'][:100]}", flush=True)
        t = time.time()
        ex = extract_one(r, question)
        extracts.append(ex)
        if "error" in ex:
            print(f"    FAIL  {ex['error']}", flush=True)
        else:
            print(f"    OK    {time.time() - t:.1f}s  relevance: {ex['relevance']}", flush=True)

    say("write report")
    if all("error" in e for e in extracts):
        body, err = "", "no paper could be read"
    else:
        body, err = synthesise(args.query, question, rows, extracts)
    if err:
        print(f"    FAIL  {err}", flush=True)
    md = report_markdown(args.query, question, rows, extracts, body, err,
                         (time.time() - t0) / 60)
    path = os.path.join(RESEARCH_DIR, f"{base}_report.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    print(f"Report written: {path}", flush=True)
    print(f"\n=== research finished in {(time.time() - t0) / 60:.1f} min ===", flush=True)
    return 0 if body else 1


if __name__ == "__main__":
    raise SystemExit(main())
