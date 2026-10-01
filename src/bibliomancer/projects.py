"""
kb_projects.py - Match an incoming paper against the literature-scout briefs
and render the "Intersection with personal work" section.

A paper earns that section only when it is actually one of the items reported
in a scout brief. Matching is by DOI first - exact and unambiguous - then by a
normalised title comparison, because a preprint downloaded from bioRxiv can
carry a different DOI string from the one the brief quoted.

Deliberately NOT keyword-based. Matching on topic words would tag most of a
specialist library as "intersecting personal work", which would
make the section worthless. The scout brief is the filter: it has already
decided these specific items matter.

Your own PDF comments outrank the scout's prose and are quoted verbatim,
in whatever language he wrote them.

    python kb_projects.py --selftest      # check the index loads and matches
    python kb_projects.py --doi 10.x/y    # show what one DOI would produce
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

from . import core as kb

PROJECTS_DIR = kb.PROJECTS_DIR
INDEX_PATH   = os.path.join(PROJECTS_DIR, "scout_index.json")
PROJECTS_PATH = os.path.join(PROJECTS_DIR, "projects.json")

SECTION_HEADING = "## Intersection with personal work"

_cache: dict = {}


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def load_index() -> dict:
    """Load and memoise the scout index. Returns {} if it has not been built."""
    if "index" in _cache:
        return _cache["index"]
    idx = {"by_doi": {}, "by_title": {}, "items": []}
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            raw = json.load(fh)
        for it in raw.get("items", []):
            idx["items"].append(it)
            if it.get("doi"):
                idx["by_doi"][it["doi"].lower()] = it
            t = _norm_title(it.get("title"))
            if len(t) > 25:                      # short titles are too ambiguous
                idx["by_title"][t] = it
    except FileNotFoundError:
        pass
    except Exception as exc:                     # never break ingestion over this
        print(f"  (scout index unreadable: {exc})", file=sys.stderr)
    _cache["index"] = idx
    return idx


def load_projects() -> dict:
    if "projects" in _cache:
        return _cache["projects"]
    try:
        with open(PROJECTS_PATH, encoding="utf-8") as fh:
            p = json.load(fh)
    except Exception:
        p = {"projects": [], "verdict_vocabulary": {}}
    _cache["projects"] = p
    return p


def find_match(doi: str | None, title: str | None) -> dict | None:
    """Return the scout item for this paper, or None."""
    idx = load_index()
    if doi:
        hit = idx["by_doi"].get(doi.lower().rstrip(".,;)"))
        if hit:
            return {**hit, "_matched_on": "doi"}
    nt = _norm_title(title)
    if len(nt) > 25:
        hit = idx["by_title"].get(nt)
        if hit:
            return {**hit, "_matched_on": "title"}
        # containment both ways: scout titles are sometimes truncated
        for t, it in idx["by_title"].items():
            if (t in nt or nt in t) and min(len(t), len(nt)) > 40:
                return {**it, "_matched_on": "title-substring"}
    return None


def render_section(item: dict) -> str:
    """Markdown for the 'Intersection with personal work' note section."""
    projects = load_projects()
    by_name = {p["name"].lower(): p for p in projects.get("projects", [])}
    vocab = projects.get("verdict_vocabulary", {})

    L = [SECTION_HEADING, ""]
    L.append(f"_Reported in **{item.get('source_brief', 'a literature scout brief')}**; "
             f"matched on {item.get('_matched_on', 'doi')}._")
    L.append("")

    if item.get("user_comment"):
        verdict = item.get("user_verdict") or "commented"
        L.append(f"**Your evaluation — `{verdict}`**"
                 + (f" _({vocab[verdict]})_" if verdict in vocab else ""))
        L.append("")
        for c in [c.strip() for c in item["user_comment"].split("|") if c.strip()]:
            L.append(f"> {c}")
        L.append("")
    else:
        L.append("_No manual evaluation recorded for this item in the brief._")
        L.append("")

    if item.get("risk"):                             # card-layout briefs rate each item
        rating = item["risk"].capitalize() + "".join(f" · {f}" for f in item.get("flags") or [])
        where = ", ".join(x for x in (item.get("venue"), item.get("date")) if x)
        L.append(f"**Scout rating:** {rating}" + (f" _({where})_" if where else ""))
        L.append("")

    assessed = False
    if item.get("projects"):
        L.append(f"**Projects touched:** {', '.join(item['projects'])}")
        L.append("")
        for p in item["projects"]:
            meta = by_name.get(p.lower(), {})
            assessment = (item.get("assessments") or {}).get(p)
            if not (assessment or meta.get("one_line")):
                continue                             # a bare tag: already in the list above
            L.append(f"### {p}")
            if meta.get("one_line"):
                L.append(f"_{meta['one_line']}_")
                L.append("")
            if assessment:
                assessed = True
                L.append(f"**Scout assessment.** {assessment}")
                L.append("")
    if not assessed and item.get("analysis"):
        L.append(f"**Scout note.** {item['analysis']}")
        L.append("")

    return "\n".join(L).rstrip() + "\n"


def section_for(doi: str | None, title: str | None) -> str:
    """Empty string when the paper is not in any brief."""
    item = find_match(doi, title)
    return render_section(item) if item else ""


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="Scout/project intersection helper.")
    ap.add_argument("--doi")
    ap.add_argument("--title")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    idx = load_index()
    if args.selftest:
        pr = load_projects()
        print(f"scout index : {len(idx['items'])} item(s), "
              f"{len(idx['by_doi'])} DOI key(s), {len(idx['by_title'])} title key(s)")
        print(f"projects    : {len(pr.get('projects', []))} "
              f"({', '.join(p['name'] for p in pr.get('projects', []))})")
        commented = [i for i in idx["items"] if i.get("user_comment")]
        print(f"with your comments: {len(commented)}")
        for it in commented[:3]:
            print(f"\n--- sample render for {it['doi']} ---")
            print(render_section({**it, "_matched_on": "doi"})[:700])
        return 0

    if args.doi or args.title:
        out = section_for(args.doi, args.title)
        print(out if out else "no scout match for that paper")
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
