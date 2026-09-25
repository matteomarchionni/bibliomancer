"""
scouts.py - Parse literature-scout briefs into a machine-readable index,
including your own PDF annotations.

A scout brief is a PDF digest of new papers (e.g. written weekly by an AI
agent) saved into <data>/Scouts/. Two layouts are understood:

  Priority alerts    N. Title (may wrap)
                     Authors - Venue, date - 10.xxxx/yyyy
                     Description... For <Project>. <strategic consequence>

  Grouped entries    Name - description. Authors et al., Venue - 10.xxxx/yyyy.
                     One-line relevance note.

Both put the DOI at the end of a byline, with the item's analysis after it, so
this anchors on DOIs: an item spans from the start of its title block to the
next DOI's title block. You highlight items and attach comments (in any
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
    r"^(https?://|\d{2}/\d{2}/\d{2},|Posta di \w|\d+/\d+$|"
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


def parse_pdf(path: str) -> list[dict]:
    from .pdf import open_pdf

    doc = open_pdf(path)
    pages = [clean_text(doc.text(i)) for i in range(doc.page_count)]
    full = "\n".join(pages)
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

    # ---- annotations ------------------------------------------------------
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
                if probe in it["analysis"]:
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

    for it in items:
        it.pop("_span", None)
        it.pop("_titleblock", None)
        c = " | ".join(x["comment"] for x in it.get("annotations", []) if x["comment"])
        it["user_comment"] = c
        it["user_verdict"] = classify_verdict(c)
        it["source_brief"] = os.path.basename(path)
    return items, placed, unplaced


def main() -> int:
    ap = argparse.ArgumentParser(description="Index the literature-scout briefs.")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    os.makedirs(PROJECTS_DIR, exist_ok=True)
    if not os.path.isdir(SCOUTS_DIR):
        print(f"No Scouts folder at {SCOUTS_DIR}")
        return 1

    all_items = []
    for f in sorted(os.listdir(SCOUTS_DIR)):
        if not f.lower().endswith(".pdf"):
            continue
        got, placed, unplaced = parse_pdf(os.path.join(SCOUTS_DIR, f))
        print(f"  {f}: {len(got)} item(s); annotations placed {placed}, unplaced {unplaced}")
        all_items += got

    by_doi = {}
    for it in all_items:
        key = it["doi"] or f"{it['source_brief']}::{it['doi_raw']}"
        prev = by_doi.get(key)
        if prev and prev.get("user_comment") and not it.get("user_comment"):
            it["user_comment"], it["user_verdict"] = prev["user_comment"], prev["user_verdict"]
        by_doi[key] = it

    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        json.dump({"generated": kb.now_iso(), "items": list(by_doi.values())},
                  fh, ensure_ascii=False, indent=2)
    print(f"\nWrote {INDEX_PATH}: {len(by_doi)} unique DOI(s), "
          f"{sum(1 for i in by_doi.values() if i['user_comment'])} carrying your comments")

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
