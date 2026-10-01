"""Scout briefs: the card layout as .html and as the text of a browser-saved PDF.

Run from the repository root:  python -m pytest tests -q
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from bibliomancer import projects, scouts  # noqa: E402

HTML = """<html><body>
<p class="risk-legend"><span class="risk risk-scoop">Scoop risk</span></p>
<section><div class="sec-head"><h2>Priority alerts</h2><span class="sec-count">2 items</span></div>
<article class="entry is-alert" id="a1">
  <p class="entry-id"><span class="risk risk-high_overlap">High overlap</span><span>Coral Reefs</span>
     <span>1 Mar 2026</span><span class="pill fail">direct overlap</span>
     <span class="pill proj">ReefWatch</span></p>
  <h3>Drone surveys of coral cover after recurrent heatwaves</h3>
  <p class="byline">Rossi A, et al. &middot; doi
     <a href="https://doi.org/10.1234/reef.2026.001">10.1234/reef.2026.001</a></p>
  <p class="body">Repeated drone transects over forty reefs &amp; three summers.</p>
  <details class="abs"><summary>Abstract</summary><p>Long abstract, doi 10.9999/not.an.item</p></details>
  <p class="verdict"><b>What this means for ReefWatch</b>Same design as ReefWatch; compare the
     transect length before citing.</p>
</article>
<article class="entry" id="i2">
  <p class="entry-id"><span class="risk risk-cite">Cite / benchmark</span><span>arXiv</span>
     <span>23 Sep 2026</span><span class="pill part">arXiv preprint</span>
     <span class="pill proj">Kelp mapping</span><span class="pill proj">ReefWatch</span></p>
  <h3>Do sensor biases propagate? A benchmark of satellite kelp maps</h3>
  <p class="byline"><a href="https://www.alphaxiv.org/abs/2609.28231">arXiv:2609.28231</a></p>
  <p class="body">Tests whether sensor-specific signal carries through to the maps.</p>
  <p class="verdict"><b>Relevance</b>A confounder for any multi-sensor map.</p>
</article></section>
<div class="archive"><p>Earlier issue: 10.5555/archived.item</p></div>
</body></html>"""

# The same two cards as pdfium extracts them from the page saved as PDF (the page
# upper-cases its labels, and PDF text sometimes drops a space).
PDF_TEXT = """BIWEEKLY SCOUT
RISK FOR OUR PROJECTS SCOOP RISK HIGH OVERLAP POTENTIAL OVERLAP CITE / BENCHMARK
OF INTEREST
Priority alerts 2 ITEMS
HIGH OVERLAP Coral Reefs 1 Mar 2026 DIRECT OVERLAP REEFWATCH
Drone surveys of coral cover after
recurrent heatwaves
Rossi A, et al. · doi 10.1234/reef.2026.001
Repeated drone transects over forty reefs & three summers.
ABSTRACT
WHAT THIS MEANS FOR REEFWATCH
Same design as ReefWatch; compare the
transect length before citing.
Kelp mapping 1 ITEM
CITE / BENCHMARK arXiv 23 Sep 2026 ARXIV PREPRINT KELP MAPPING REEFWATCH
Do sensor biases propagate? A benchmark of satellite kelp maps
arXiv:2609.28231
Tests whether sensor-specific signal carries through to the maps.
RELEVANCE
A confounder for any multi-sensor map.
Read in full this issue 1 PAPERS
1 Drone surveys — Coral Reefs 10.1234/reef.2026.001
ARCHIVE
Earlier issue: 10.5555/archived.item
"""


def _check(items):
    assert [it["doi"] for it in items] == ["10.1234/reef.2026.001", "10.48550/arxiv.2609.28231"]
    a, b = items
    assert a["title"] == "Drone surveys of coral cover after recurrent heatwaves"
    assert (a["risk"], a["venue"], a["date"]) == ("high overlap", "Coral Reefs", "1 Mar 2026")
    assert a["projects"] == ["ReefWatch"]
    assert a["assessments"]["ReefWatch"].startswith("Same design as ReefWatch; compare the transect")
    assert "forty reefs & three summers" in a["summary"] and "abstract" not in a["summary"].lower()
    assert b["projects"] == ["Kelp mapping", "ReefWatch"] and b["assessments"] == {}
    assert b["analysis"] == "A confounder for any multi-sensor map."
    assert not any(k.startswith("_") for it in items for k in it)


def test_card_layout_html(tmp_path, monkeypatch):
    monkeypatch.setattr(scouts, "PROJECT_NAMES", ["ReefWatch"])
    f = tmp_path / "brief.html"
    f.write_text(HTML, encoding="utf-8")
    items = scouts._finish(scouts.parse_html(str(f)), str(f))
    _check(items)
    assert items[0]["flags"] == ["direct overlap"] and items[0]["source_brief"] == "brief.html"


def test_card_layout_pdf_text_matches_html(monkeypatch):
    monkeypatch.setattr(scouts, "PROJECT_NAMES", ["ReefWatch"])
    _check(scouts._finish(scouts._cards_from_text(PDF_TEXT), "brief.pdf"))
    assert scouts._cards_from_text("1. A title\nRossi A - Venue - 10.1234/x.y\nFor ReefWatch. ok") is None


def test_note_section_for_card_item(monkeypatch):
    monkeypatch.setattr(scouts, "PROJECT_NAMES", ["ReefWatch"])
    monkeypatch.setitem(projects._cache, "projects", {"projects": [
        {"name": "ReefWatch", "one_line": "drone surveys of coral cover"}]})
    a, b = scouts._finish(scouts._cards_from_text(PDF_TEXT), "brief.pdf")
    s = projects.render_section({**a, "_matched_on": "doi"})
    assert "**Scout rating:** High overlap _(Coral Reefs, 1 Mar 2026)_" in s
    assert "### ReefWatch" in s and "**Scout assessment.** Same design as ReefWatch" in s
    s = projects.render_section({**b, "_matched_on": "doi"})
    assert "**Projects touched:** Kelp mapping, ReefWatch" in s
    assert "### Kelp mapping" not in s                  # a bare tag gets no empty heading
    assert "**Scout note.** A confounder for any multi-sensor map." in s
