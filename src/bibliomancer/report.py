"""report - statistics of the library, like PhotoMancer's report.

    python -m bibliomancer report

Writes into <data>/Reports/:
    <date>_report.md    counts per year, field, folder, text source, model;
                        failures and given-up papers; measured reading speed
    <date>_catalog.csv  one row per catalogued paper (title, year, DOI, fields,
                        keywords, folder, note file) - opens in Excel
and prints "Report written: <md>" so the panel opens it.
"""

from __future__ import annotations

import csv
import datetime as dt
import glob
import json
import os
import re
import statistics
from collections import Counter

from . import core as kb

REPORT_DIR = os.path.join(kb.KB_HOME, "Reports")


def _js(v):
    try:
        return json.loads(v or "[]")
    except ValueError:
        return []


def reading_speed() -> tuple[float | None, int]:
    """Median seconds per paper from the ingestion logs ('OK 14.2s'), and how many."""
    secs = []
    for p in sorted(glob.glob(os.path.join(kb.LOG_DIR, "*.log")))[-40:]:
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                secs += [float(m) for m in re.findall(r"\bOK\s+([\d.]+)s\b", fh.read())]
        except OSError:
            pass
    return (statistics.median(secs), len(secs)) if secs else (None, 0)


def main() -> int:
    os.makedirs(REPORT_DIR, exist_ok=True)
    conn = kb.connect()
    rows = conn.execute("SELECT * FROM processed_papers").fetchall()
    fails = conn.execute("SELECT * FROM failures ORDER BY attempts DESC").fetchall()
    pending = len(kb.pending_papers(conn))
    conn.close()
    n_pdfs = len(kb.iter_pdfs())

    def table(title, counter, top=25):
        out = [f"## {title}", "", "| | papers |", "|---|---:|"]
        out += [f"| {k or '(none)'} | {v} |" for k, v in counter.most_common(top)]
        return out + [""]

    years = Counter(r["year"] or "Unknown" for r in rows)
    fields = Counter(f for r in rows for f in _js(r["research_fields"]))
    folders = Counter((r["rel_path"] or "").replace("\\", "/").split("/")[0] if "/" in
                      (r["rel_path"] or "").replace("\\", "/") else "(library root)" for r in rows)
    sources = Counter(r["text_source"] or "text" for r in rows)
    models = Counter(r["source"] or "?" for r in rows)
    kinds = Counter(r["doc_type"] or "article" for r in rows)
    speed, n_speed = reading_speed()
    given_up = [f for f in fails if f["attempts"] >= kb.MAX_ATTEMPTS]

    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    md = [f"# BiblioMancer - report {dt.datetime.now():%Y-%m-%d %H:%M}", "",
          f"- PDFs in the library: **{n_pdfs}**", f"- Catalogued: **{len(rows)}**",
          f"- Pending (read at the next update): **{pending}**",
          f"- Given up after {kb.MAX_ATTEMPTS} attempts: **{len(given_up)}**",
          f"- Model now: `{kb.MODEL_NAME}` (reasoning: {kb.REASONING_EFFORT or 'model default'})",
          f"- Measured reading speed: " + (f"median {speed:.0f} s per paper over the last "
                                           f"{n_speed} papers" if speed else "no log data yet"),
          ""]
    md += table("Years", Counter(dict(sorted(years.items(), reverse=True))), top=60)
    md += table("Research fields", fields)
    md += table("Library folders", folders)
    md += table("Document types", kinds)
    md += table("Text source", sources)
    md += table("Summarised by", models)
    key, setup = kb.current_setup()
    conn = kb.connect()
    by_key = Counter(r[0] or "before 2026-09-25 (not recorded)" for r in conn.execute(
        "SELECT s.setup_key FROM processed_papers p LEFT JOIN paper_setup s USING (file_hash)"))
    conn.close()
    md += [f"## Setup that wrote the notes", "",
           f"Current setup `{key}`: model `{setup['model']}`, reasoning {setup['reasoning_effort'] or 'default'}, "
           f"prompt {setup['prompt_sha8']}, {setup['max_doc_tokens']:,} tokens / {setup['max_pages']} pages "
           "per paper. Notes written with another setup are only re-read by Rebuild.", "",
           "| setup | papers |", "|---|---:|"]
    md += [f"| {k}{' (current)' if k == key else ''} | {v} |" for k, v in by_key.most_common()] + [""]
    if fails:
        md += ["## Failures", "", "| file | attempts | last error |", "|---|---:|---|"]
        md += [f"| {f['rel_path']} | {f['attempts']} | {(f['reason'] or '')[:120]} |"
               for f in fails[:100]]
        md.append("")

    csv_path = os.path.join(REPORT_DIR, f"{stamp}_catalog.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["title", "year", "authors", "doi", "fields", "keywords", "folder",
                    "pdf", "note", "text_source", "doc_type"])
        for r in sorted(rows, key=lambda r: (r["year"] or "", r["title"] or "")):
            w.writerow([r["title"], r["year"], r["authors"], r["doi"] or "",
                        "; ".join(_js(r["research_fields"])), "; ".join(_js(r["keywords"])),
                        os.path.dirname(r["rel_path"] or ""), r["rel_path"], r["note_filename"],
                        r["text_source"] or "text", r["doc_type"] or ""])
    md += [f"Full catalogue as CSV: `{csv_path}`", ""]
    md_path = os.path.join(REPORT_DIR, f"{stamp}_report.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    print(f"=== report ===\n{len(rows)} papers, {len(fails)} failure(s)", flush=True)
    print(f"Report written: {md_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
