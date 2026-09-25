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

Save each brief as a PDF into `<data>/Scouts/`. BiblioMancer understands two layouts — a
numbered item (*Title / Authors – Venue, date – DOI / description… For ReefWatch.
consequence…*) and grouped one-liners — anchored on each item's DOI.

Annotate the PDF as you read it: **highlight** an item and add a **comment** with your
verdict, in any language ("not a real competitor", "to test", "not worth it"…). Your
comments outrank the scout's prose and are quoted verbatim.

At the next update (or **Refresh scouts** on the Overview page) the briefs are indexed
into `<data>/Projects/scout_index.json`, and every paper of your library that a brief
reported gets an **Intersection with personal work** section in its note: which projects
it concerns, the scout's assessment, and your comment.

Hand-edited notes are respected: the updated version goes next to them as
`<note>.generated.md`.
