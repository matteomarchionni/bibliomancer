# 8. Scout briefs and projects (optional)

If you get regular digests of new literature — for example a weekly brief written by an AI
"literature scout" that flags papers relevant to your projects — BiblioMancer can connect
them to your library and keep your own verdicts.

## Your projects

Create `<data>/Projects/projects.json`:

```json
{
  "owner": "Your Name",
  "affiliation": "Your institution",
  "projects": [
    {"name": "ReefWatch", "status": "active",
     "one_line": "drone surveys of coral cover after heatwaves"},
    {"name": "KelpMap", "status": "writing",
     "one_line": "a map of kelp forest decline from satellite data"}
  ]
}
```

The project list also appears as the *research context* in the export for AI agents.

## Scout briefs

Save each brief into `<data>/Scouts/`, as a PDF or as the `.html` page itself. Two kinds
of brief are understood:

- **Card layout** — an HTML report where every paper is a card: a risk label (*Scoop
  risk*, *High overlap*, *Potential overlap*, *Cite / benchmark*, *Of interest*), venue
  and date, project tags, the title, a byline with the DOI (or `arXiv:` id), a summary and
  a verdict (*What this means for ReefWatch* / *Relevance*). The `.html` is read exactly;
  the same page saved as PDF is read from its text. The markup is described at the top
  of `src/bibliomancer/scouts.py`.
- **Prose layout** — a brief printed from an e-mail: a numbered item (*Title / Authors –
  Venue, date – DOI / description… For ReefWatch. consequence…*) or grouped one-liners,
  anchored on each item's DOI.

> **Save the PDF from the browser** (Print → destination *Save as PDF*), not with a PDF
> printer such as *Microsoft Print to PDF*: a printer can produce a PDF with no text in
> it, which cannot be read (the activity log says so) or highlighted. If you do not need
> to annotate, just keep the `.html`. With both in the folder, the `.html` gives the
> entries and the PDF gives your comments.

Project tags that are not in `projects.json` are kept as the brief writes them; add them
to `projects.json` to get their one-line description in the notes.

Annotate the PDF as you read it: **highlight** an item and add a **comment** with your
verdict, in any language ("not a real competitor", "to test", "not worth it"…). Your
comments outrank the scout's prose and are quoted verbatim.

At the next update (or **Refresh scouts** on the Overview page) the briefs are indexed
into `<data>/Projects/scout_index.json`, and every paper of your library that a brief
reported gets an **Intersection with personal work** section in its note: which projects
it concerns, the scout's rating and assessment, and your comment. Papers that were already
in the library when the brief arrived get it too (their notes are refreshed, no model needed).

Hand-edited notes are respected: the updated version goes next to them as
`<note>.generated.md`.
