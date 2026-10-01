"""
scouts.py - Parse literature-scout briefs into a machine-readable index,
including your own PDF annotations.

A scout brief is a digest of new papers (e.g. written weekly by an AI agent)
saved into <data>/Scouts/ as a PDF or as the .html page itself.

CARD LAYOUT (an HTML report, or that page saved as PDF from the browser):

    <article class="entry">
      <p class="entry-id"><span class="risk ...">High overlap</span><span>Venue</span>
         <span>1 Mar 2026</span><span class="pill ...">flag</span>
         <span class="pill proj">Project</span></p>
      <h3>Title</h3>
      <p class="byline">Authors · doi <a href="https://doi.org/10.x/y">10.x/y</a></p>
      <p class="body">Summary</p>
      <p class="verdict"><b>What this means for Project</b>Assessment</p>
    </article>

  The .html is read exactly. In the PDF the same card is a run of text lines
  (a line starting with a risk label, title, byline, summary, verdict); project
  names in the header are recognised from projects.json, the section headings
  ("Project  5 ITEMS") and the "What this means for ..." labels. arXiv-only
  entries get the arXiv DOI (10.48550/arxiv.<id>).

PROSE LAYOUT (a brief printed from an e-mail), two forms:

  Priority alerts    N. Title (may wrap)
                     Authors - Venue, date - 10.xxxx/yyyy
                     Description... For <Project>. <strategic consequence>

  Grouped entries    Name - description. Authors et al., Venue - 10.xxxx/yyyy.
                     One-line relevance note.

Both put the DOI at the end of a byline, with the item's analysis after it, so
this anchors on DOIs: an item spans from the start of its title block to the
next DOI's title block.

A PDF made by a PDF *printer* (e.g. "Microsoft Print to PDF") can have no text
at all: it cannot be read - save the page from the browser with "Save as PDF",
or put the .html in Scouts. In a PDF you highlight items and attach comments (in any
language); those are your own verdict and outrank the scout's prose, so they
are captured verbatim and mapped back onto items by locating the highlighted
text. All position arithmetic happens in whitespace-normalised space, because
highlighted text does not contain the page text's original line breaks.

Project names ("For <Project>.") come from <data>/Projects/projects.json.

    python -m bibliomancer scouts          # rebuild Projects/scout_index.json (+ summary)
"""

from __future__ import annotations

import argparse
import json
import os
import re

from . import core as kb

SCOUTS_DIR   = kb.SCOUTS_DIR
PROJECTS_DIR = kb.PROJECTS_DIR
INDEX_PATH   = os.path.join(PROJECTS_DIR, "scout_index.json")

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")

def _project_names() -> list[str]:
    """Your project names, from Projects/projects.json ({"projects": [{"name": ...}]})."""
    try:
        with open(os.path.join(PROJECTS_DIR, "projects.json"), encoding="utf-8") as fh:
            return [p["name"] for p in json.load(fh).get("projects", []) if p.get("name")]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


# Only real project names - a permissive pattern captured phrases like
# "For <Project> to publish one" as if they were project names.
PROJECT_NAMES = _project_names()
_ALT = "|".join(re.escape(n) for n in PROJECT_NAMES) or r"(?!x)x"     # no projects: never matches
PROJECT_RE = re.compile(
    r"\bFor ((?:" + _ALT + r")(?:\s+and\s+(?:" + _ALT + r"))*)\b[.:]", re.IGNORECASE)
# A bare mention, for grouped entries that name a project without "For X."
MENTION_RE = re.compile(r"\b(" + _ALT + r")\b", re.IGNORECASE)

# E-mail print furniture that pollutes the extracted text (a brief printed from
# Gmail: URLs, date stamps, "Posta di <org>", page counters, sender/recipient lines).
JUNK_RE = re.compile(
    r"^(https?://|file:///|\d{2}/\d{2}/\d{2},|Posta di \w|\d+/\d+$|"
    r"[^<\n]{2,60} <[^@\s>]+@[^>\s]+>|(A|To|Da|From): \S)", re.I)

VERDICT_RULES = [
    (re.compile(r"\bfuffa\b|non interessante|non ne vale la pena", re.I), "dismissed"),
    (re.compile(r"non un vero competitor|non e un competitor|non è un competitor", re.I),
     "not-a-competitor"),
    (re.compile(r"parzialmente", re.I),                                 "partial-overlap"),
    (re.compile(r"da testare|da considerare|potremmo utilizzarlo", re.I), "to-evaluate"),
    (re.compile(r"molto interessante|interessante", re.I),              "of-interest"),
]


def classify_verdict(comment: str) -> str | None:
    for rx, label in VERDICT_RULES:
        if rx.search(comment or ""):
            return label
    return "commented" if (comment or "").strip() else None


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def clean_text(raw: str) -> str:
    return "\n".join(l for l in raw.splitlines() if not JUNK_RE.match(l.strip()))


BOILERPLATE = re.compile(
    r"\s*(Compiled \d|Literature search performed via PubMed|"
    r"Every entry comes from a record actually retrieved|\]\]>)")


def _trim_analysis(a: str) -> str:
    """Drop the brief's closing boilerplate and any run-on into the next
    entry's heading."""
    m = BOILERPLATE.search(a)
    if m:
        a = a[:m.start()]
    # A trailing fragment containing the byline separator is the start of the
    # next entry, not part of this assessment. Assessments never contain it.
    if "·" in a:
        head, _, tail = a.rpartition("·")
        cut = head.rfind(". ")
        if cut > 40:
            a = head[:cut + 1]
    a = re.sub(r"\s+\d+\.\s*$", "", a)      # trailing next-item number
    return a.strip()


def _doi_usable(d: str) -> bool:
    """A DOI wrapped across a PDF line break is truncated and must not be used
    as a match key - it would prefix-match an unrelated paper. The item itself
    is still kept, because it may carry one of your comments."""
    suffix = d.split("/", 1)[1] if "/" in d else ""
    return len(suffix) >= 5 and not d.endswith(("-", ".", "_"))


class NoText(Exception):
    """The PDF has no text layer (made by a PDF printer, or scanned)."""


# ------------------------------------------------------------ card layout ---
RISK_LABELS = ("scoop risk", "high overlap", "potential overlap", "cite / benchmark",
               "of interest")
_RISK = "|".join(re.escape(r) for r in RISK_LABELS)
CARD_HEAD_RE = re.compile(rf"^({_RISK})\s+(\S.*)$", re.I)
SECTION_RE = re.compile(r"^(.+?)\s+\d+\s+(?:ITEMS?|PAPERS?)$")       # "Project 5 ITEMS"
VERDICT_RE = re.compile(r"^(?:what this means for\s+(.+)|relevance)$", re.I)
DATE_RE = re.compile(r"\b\d{1,2} [A-Z][a-z]{2} \d{4}\b")
ARXIV_RE = re.compile(r"\barXiv:\s*(\d{4}\.\d{4,5})", re.I)


def _canon(name: str) -> str:
    """A project name as projects.json spells it; otherwise as the brief does."""
    name = norm(name)
    return next((c for c in PROJECT_NAMES if c.lower() == name.lower()), name)


def _key(text: str) -> tuple[str | None, str]:
    """(doi usable as a match key or None, raw key) from a byline: a DOI, or an
    arXiv id turned into its DOI; neither -> (None, '')."""
    m = DOI_RE.search(text)
    if m:
        d = m.group(0).rstrip(".,;)").lower()
        return (d if _doi_usable(d) else None), d
    m = ARXIV_RE.search(text)
    if m:
        d = f"10.48550/arxiv.{m.group(1)}"
        return d, d
    return None, ""


def _card_item(title: str, byline: str, summary: str, label_project: str, verdict: str,
               projects: list[str], risk: str, venue: str, date: str, flags: list[str]) -> dict:
    doi, raw = _key(byline)
    title, verdict, summary = norm(title), norm(verdict), norm(summary)
    projects = [_canon(p) for p in projects]
    assessments = {}
    if label_project:
        lp = _canon(label_project)
        if lp not in projects:
            projects.append(lp)
        assessments[lp] = verdict[:1400]
    return {
        "doi": doi, "doi_raw": raw or "title:" + re.sub(r"[^a-z0-9]+", " ", title.lower()).strip(),
        "title": title[:260], "projects": list(dict.fromkeys(projects)),
        "assessments": assessments, "analysis": verdict[:1400],
        "summary": summary[:1400], "risk": norm(risk).lower(), "venue": norm(venue),
        "date": date, "flags": [norm(f) for f in flags if norm(f)],
        "_text": norm(" ".join([title, byline, summary, verdict])),
        "_titleblock": norm(title + " " + byline)[-320:],
    }


def parse_html(path: str) -> list[dict]:
    """Cards of an HTML brief (<article class="entry">), read exactly."""
    from html.parser import HTMLParser

    class Cards(HTMLParser):
        def __init__(self):
            super().__init__()
            self.cards, self.cur, self.slot, self.sub, self.buf = [], None, None, None, []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            cls = a.get("class") or ""
            if tag == "article" and "entry" in cls.split():
                self.cur = {"chips": [], "title": "", "byline": "", "body": "", "label": "",
                            "verdict": ""}
            if self.cur is None:
                return
            if tag == "details":
                self.slot = "skip"
            elif self.slot == "skip":
                return
            elif tag == "h3":
                self.slot = "title"
            elif tag == "p":
                c = cls.split()
                self.slot = next((s for s in ("entry-id", "byline", "body", "verdict") if s in c), None)
            elif tag == "span" and self.slot == "entry-id":
                self.sub, self.buf = cls, []
            elif tag == "b" and self.slot == "verdict":
                self.sub = "label"
            elif tag == "a" and self.slot == "byline" and a.get("href"):
                self.cur["byline"] += " " + a["href"].replace("https://doi.org/", "") + " "

        def handle_endtag(self, tag):
            if self.cur is None:
                return
            if tag == "details":
                self.slot = None
            elif tag == "span" and self.slot == "entry-id" and self.sub is not None:
                self.cur["chips"].append((self.sub, "".join(self.buf)))
                self.sub = None
            elif tag == "b":
                self.sub = None
            elif tag in ("p", "h3") and self.slot != "skip":
                self.slot = None
            elif tag == "article":
                self.cards.append(self.cur)
                self.cur = self.slot = None

        def handle_data(self, data):
            if self.cur is None or self.slot in (None, "skip"):
                return
            if self.slot == "entry-id":
                if self.sub is not None:
                    self.buf.append(data)
            elif self.slot == "verdict":
                self.cur["label" if self.sub == "label" else "verdict"] += data
            else:
                self.cur[self.slot] += data

    with open(path, encoding="utf-8", errors="replace") as fh:
        p = Cards()
        p.feed(fh.read())
    items = []
    for c in p.cards:
        chips = [(cls.split(), norm(t)) for cls, t in c["chips"] if norm(t)]
        plain = [t for cls, t in chips if not cls]
        date = next((t for t in plain if DATE_RE.search(t)), "")
        m = VERDICT_RE.match(norm(c["label"]))
        items.append(_card_item(
            c["title"], c["byline"], c["body"], (m.group(1) or "") if m else "", c["verdict"],
            projects=[t for cls, t in chips if "proj" in cls],
            risk=next((t for cls, t in chips if "risk" in cls), ""),
            venue=next((t for t in plain if t != date), ""), date=date,
            flags=[t for cls, t in chips if "pill" in cls and "proj" not in cls]))
    return items


def _cards_from_text(full: str) -> list[dict] | None:
    """Cards of a card-layout brief saved as PDF, from its text lines; None when
    the text is not in card layout (fewer than two card headers)."""
    lines = [ln.strip() for ln in full.splitlines() if ln.strip()]
    heads = [i for i, ln in enumerate(lines) if CARD_HEAD_RE.match(ln)]
    if len(heads) < 2:
        return None
    squash = lambda s: re.sub(r"\s+", "", s.lower())      # noqa: E731  (PDF text drops spaces)
    # Names a header chip can be: projects.json, section headings, verdict labels.
    names = {squash(n): n for n in PROJECT_NAMES}
    for ln in lines:                    # section headings keep the name's own spelling
        m = SECTION_RE.match(ln)
        if m and any(squash(m.group(1)) in squash(lines[h]) for h in heads):
            names.setdefault(squash(m.group(1)), norm(m.group(1)))
    for ln in lines:                    # verdict labels are upper-cased by the page
        m = VERDICT_RE.match(ln)
        if m and m.group(1):
            names.setdefault(squash(m.group(1)), norm(m.group(1)).capitalize())

    items = []
    for n, h in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        card = lines[h:end]
        cut = next((k for k, ln in enumerate(card) if k and (SECTION_RE.match(ln) or ln == "ARCHIVE")),
                   len(card))
        card = card[:cut]
        m = CARD_HEAD_RE.match(card[0])
        risk, chips = m.group(1), m.group(2)
        k = 1
        while k < len(card) and card[k].isupper() and not VERDICT_RE.match(card[k]):
            chips += " " + card[k]                        # a header that wrapped
            k += 1
        by = next((j for j in range(k, min(k + 8, len(card)))
                   if re.search(r"\bdoi\s+10\.|^10\.\d{4,9}/|arXiv:\s*\d", card[j], re.I)), None)
        v = next((j for j in range(k, len(card)) if VERDICT_RE.match(card[j])), len(card))
        title_end = by if by is not None else min(k + 2, v)
        body = [ln for ln in card[(by + 1 if by is not None else title_end):v] if ln.upper() != "ABSTRACT"]
        label = VERDICT_RE.match(card[v]).group(1) if v < len(card) else None
        label = names.get(squash(label), label) if label else None
        dm = DATE_RE.search(chips)
        sq = squash(chips[dm.end():] if dm else chips)
        items.append(_card_item(
            " ".join(card[k:title_end]), card[by] if by is not None else "", " ".join(body),
            label or "", " ".join(card[v + 1:]),
            projects=[name for key, name in sorted(names.items(), key=lambda kv: sq.find(kv[0]))
                      if key in sq],
            risk=risk, venue=chips[:dm.start()] if dm else "", date=dm.group(0) if dm else "",
            flags=[]))
    return items


def parse_pdf(path: str) -> tuple[list[dict], int, int]:
    from .pdf import open_pdf

    doc = open_pdf(path)
    pages = [clean_text(doc.text(i)) for i in range(doc.page_count)]
    full = "\n".join(pages)
    if not full.strip():
        doc.close()
        raise NoText(path)
    nfull = norm(full)
    items = _cards_from_text(full)
    if items is None:
        items = _prose_items(full)
    return _annotate(doc, nfull, items, path)


def _prose_items(full: str) -> list[dict]:
    """Items of a prose-layout brief (see the module docstring), anchored on DOIs."""
    nfull = norm(full)
    # Rejoin DOIs broken across a PDF line wrap, e.g.
    #     10.1038/s41467-026-\n76829-1
    # which normalises to "...-026- 76829-1". Scoped to strings that already
    # start with a DOI prefix and break on a hyphen, so ordinary hyphenated
    # text is untouched. Without this the item is unmatchable, and the
    # STADiffuser entry carrying a substantive comment was being missed.
    nfull = re.sub(r"(10\.\d{4,9}/[-._;()/:A-Za-z0-9]*-)\s+(?=[A-Za-z0-9])",
                   r"\1", nfull)

    # Map every DOI to its position in normalised space.
    hits = []
    for m in DOI_RE.finditer(nfull):
        d = m.group(0).rstrip(".,;)")
        # Reject DOIs truncated by a line wrap in the PDF (e.g.
        # "10.1038/s41467-026-"): as an index key a truncated DOI is worse
        # than no key, because it can prefix-match an unrelated paper.
        hits.append((m.start(), m.end(), d))

    # Pass 1: where does each item's own title block begin? Needed twice -
    # to extract the title, and to stop the PREVIOUS item's analysis before it.
    # Without the second use, an item's "scout assessment" ran on into the next
    # entry's byline (STADiffuser was showing Halo's).
    # Abbreviations are excluded from the sentence-boundary test: "et al.",
    # "(corr. Can Yang" and friends are not new sentences.
    BOUNDARY = re.compile(
        r"(?:\d+\.\s|(?<!et al)(?<!corr)(?<!Fig)(?<!vs)(?<!e\.g)(?<!i\.e)"
        r"(?<! al)\.\s+)(?=[A-Z0-9])")
    starts = []
    for i, (ds, de, doi) in enumerate(hits):
        prev_end = hits[i - 1][1] if i else 0
        before = nfull[prev_end:ds]
        cands = [m.end() for m in BOUNDARY.finditer(before)]
        ts = cands[-1] if cands else max(0, len(before) - 220)
        starts.append(prev_end + ts)

    items = []
    for i, (ds, de, doi) in enumerate(hits):
        prev_end = hits[i - 1][1] if i else 0
        # Analysis stops where the NEXT item's title begins, not at its DOI.
        nxt_start = starts[i + 1] if i + 1 < len(hits) else len(nfull)

        before = nfull[prev_end:ds]
        after = nfull[de:max(de, nxt_start)]

        title_start = starts[i] - prev_end
        title_block = before[title_start:].strip()
        # Trim a trailing byline ("Authors ... - Venue, date -")
        title = re.split(r"\s[-–—·]\s", title_block)[0]
        title = re.sub(r"^\d+\.\s*", "", title).strip()

        # Where the analysis for the PREVIOUS item ends is where this item's
        # span begins - used for placing annotations.
        span = (prev_end + title_start, nxt_start)

        projects = []
        for m in PROJECT_RE.finditer(after):
            for p in re.split(r"\s+and\s+", m.group(1)):
                canon = next((c for c in PROJECT_NAMES if c.lower() == p.strip().lower()), None)
                if canon and canon not in projects:
                    projects.append(canon)
        if not projects:  # grouped entries often just name the project inline
            for m in MENTION_RE.finditer(after):
                canon = next(c for c in PROJECT_NAMES if c.lower() == m.group(1).lower())
                if canon not in projects:
                    projects.append(canon)

        assessments = {}
        for p in projects:
            mm = re.search(rf"For {re.escape(p)}[^.]*\.\s(.+?)(?=\bFor (?:"
                           + _ALT + r")\b|$)", after, re.I)
            raw = mm.group(1) if mm else after
            assessments[p] = _trim_analysis(norm(raw))[:1400]

        items.append({
            "doi": doi.lower() if _doi_usable(doi) else None,
            "doi_raw": doi.lower(),
            "title": title[:260],
            "projects": projects,
            "assessments": assessments,
            "analysis": _trim_analysis(norm(after))[:1400],
            # Last stretch before the DOI: the item's own heading/byline.
            # Kept for annotation matching, since highlights often cover only
            # the title, and the parsed `title` field can be truncated.
            "_titleblock": before[-320:],
            "_span": span,
        })
    return items


def _annotate(doc, nfull: str, items: list[dict], path: str) -> tuple[list[dict], int, int]:
    """Attach your highlights and comments to the items; (items, placed, unplaced)."""
    annots = []
    for pi in range(doc.page_count):
        try:
            page_annots = doc.annotations(pi)
        except Exception:
            page_annots = []
        for a in page_annots:
            marked = norm(a["marked"])
            pos = nfull.find(marked[:60]) if len(marked) >= 20 else -1
            annots.append({"comment": a["comment"], "marked": marked, "pos": pos})
    doc.close()

    # Attribution by direct substring containment against each item's own
    # text, NOT by global character offsets. Offsets mis-assign titles: a
    # byline such as "(corr. Can Yang, HKUST)" looks like a sentence boundary,
    # which pushed the CellART heading into the preceding item's span.
    placed = unplaced = 0
    for a in annots:
        mk = a["marked"]
        if len(mk) < 8:
            unplaced += 1
            continue
        target, how = None, None
        for probe in (mk[:60], mk[:40], mk[:25]):
            if target or len(probe) < 12:
                continue
            for it in items:
                if probe in it["analysis"] or probe in it.get("_text", ""):
                    target, how = it, "analysis"
                    break
            if not target:
                for it in items:
                    if probe in it["title"]:
                        target, how = it, "title"
                        break
            if not target:
                for it in items:
                    if probe in it["_titleblock"]:
                        target, how = it, "titleblock"
                        break
        if target is None:
            unplaced += 1
            print(f"      UNPLACED annotation {a['comment'][:40]!r} "
                  f"marked={mk[:60]!r}")
            continue
        placed += 1
        target.setdefault("annotations", []).append(
            {"comment": a["comment"], "marked": mk[:300], "matched_on": how})

    # The brief repeats key DOIs in its closing "worth reading" section. That
    # later occurrence has no analysis of its own and runs into the footer, so
    # keep the FIRST occurrence per DOI within a brief and merge annotations.
    collapsed = {}
    for it in items:
        key = it["doi_raw"]
        if key in collapsed:
            collapsed[key].setdefault("annotations", []).extend(it.get("annotations", []))
            continue
        collapsed[key] = it
    items = list(collapsed.values())

    return _finish(items, path), placed, unplaced


def _finish(items: list[dict], path: str) -> list[dict]:
    for it in items:
        for k in ("_span", "_titleblock", "_text"):
            it.pop(k, None)
        c = " | ".join(x["comment"] for x in it.get("annotations", []) if x["comment"])
        it["user_comment"] = c
        it["user_verdict"] = classify_verdict(c)
        it["source_brief"] = os.path.basename(path)
    return items


def refresh_notes(dry_run: bool = False) -> list[str]:
    """Notes of papers ALREADY in the library whose scout section is missing or
    out of date (a brief that arrived after the paper, a new comment): re-render
    them from the catalogue - no model call; hand-edited notes are not overwritten
    (see core.write_note). Returns the note names concerned."""
    from . import projects
    projects._cache.clear()                       # the index was just rewritten
    if not os.path.exists(kb.DB_PATH):
        return []
    conn = kb.connect()
    changed = []
    try:
        for r in conn.execute("SELECT file_hash, doi, title, note_filename FROM processed_papers"
                              ).fetchall():
            section = projects.section_for(r["doi"], r["title"])
            if not section or not r["note_filename"]:
                continue
            try:
                with open(os.path.join(kb.NOTES_DIR, r["note_filename"]), encoding="utf-8") as fh:
                    note = fh.read()
            except OSError:
                continue
            if norm(section) in norm(note):
                continue
            changed.append(r["note_filename"])
            if not dry_run:
                kb.rerender_note(conn, r["file_hash"])
    finally:
        conn.close()
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description="Index the literature-scout briefs.")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--no-notes", action="store_true",
                    help="only rebuild the index; do not refresh the notes of library papers")
    args = ap.parse_args()

    os.makedirs(PROJECTS_DIR, exist_ok=True)
    if not os.path.isdir(SCOUTS_DIR):
        print(f"No Scouts folder at {SCOUTS_DIR}")
        return 1

    all_items, unreadable = [], 0
    for f in sorted(os.listdir(SCOUTS_DIR)):
        path, ext = os.path.join(SCOUTS_DIR, f), os.path.splitext(f)[1].lower()
        try:
            if ext == ".pdf":
                got, placed, unplaced = parse_pdf(path)
                print(f"  {f}: {len(got)} item(s); annotations placed {placed}, unplaced {unplaced}")
            elif ext in (".html", ".htm"):
                got = _finish(parse_html(path), path)
                print(f"  {f}: {len(got)} item(s)")
            else:
                continue
        except NoText:
            unreadable += 1
            print(f"  {f}: WARNING no text in this PDF - nothing indexed. It was made by a PDF "
                  f"printer (e.g. Microsoft Print to PDF): save the brief from the browser with "
                  f"'Save as PDF' instead, or put its .html file in the Scouts folder.")
            continue
        if not got:
            unreadable += 1
            print(f"  {f}: WARNING no items recognised - is this a scout brief in a known layout?")
        all_items += got

    by_doi = {}
    for it in all_items:
        key = it["doi"] or f"{it['source_brief']}::{it['doi_raw']}"
        prev = by_doi.get(key)
        if prev:
            if prev.get("user_comment") and not it.get("user_comment"):     # keep your verdict
                for k in ("user_comment", "user_verdict", "annotations"):
                    if k in prev:
                        it[k] = prev[k]
            if prev.get("risk") and not it.get("risk"):       # the card version is the richer one
                for k in ("user_comment", "user_verdict", "annotations"):
                    if k in it:
                        prev[k] = it[k]
                it = prev
        by_doi[key] = it

    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump({"generated": kb.now_iso(), "items": list(by_doi.values())},
                  fh, ensure_ascii=False, indent=2)
    print(f"\nWrote {INDEX_PATH}: {len(by_doi)} unique DOI(s), "
          f"{sum(1 for i in by_doi.values() if i['user_comment'])} carrying your comments")
    if unreadable:
        print(f"WARNING {unreadable} brief(s) could not be read - see above")
    if not args.no_notes:
        changed = refresh_notes()
        if changed:
            print(f"Updated the scout section in {len(changed)} note(s) of papers already "
                  f"in the library:")
            for name in changed:
                print(f"  {name}")

    if args.show:
        for it in by_doi.values():
            if it["user_comment"]:
                print(f"\n  {it['doi']}")
                print(f"    title   : {it['title'][:90]}")
                print(f"    projects: {', '.join(it['projects']) or '-'}")
                print(f"    verdict : {it['user_verdict']}")
                print(f"    comment : {it['user_comment'][:130]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
