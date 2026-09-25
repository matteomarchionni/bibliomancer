# 2. Your first update

## Check first (optional)

Press **Check** on the Overview page: the activity log lists every PDF an update would
read, and nothing changes. **Options → Maintenance → Check setup** verifies the folders,
LM Studio, the model and the embedding model.

## Update now

Press **Update now**. One button runs the whole pipeline:

1. starts LM Studio and loads the model with the right context size;
2. reads new scout briefs (tutorial 8) and drops incoming duplicates;
3. **reads every new paper**: text extraction (vision OCR for scans), then the model's
   structured summary;
4. removes duplicates, updates the search index, rebuilds the synthesis matrix;
5. exports to the synced folder (if you set one).

The **Status** card shows the overall progress, the current step and, while papers are
being read, *Paper 12 of 80 · about 15 min left*. The activity log shows every paper with
its time and the note it produced. You can keep working or close the panel's window
(it asks first) — nothing already read is lost if you stop.

A first run on a large library takes a while: ~15 s per paper with the default model and
limits. Later updates only read what is new.

## What you get

For each paper a Markdown note in `<data>/Notes/`:

```markdown
---
title: "Thermal Tolerance of Reef-Building Corals Under Recurrent Heatwaves"
authors: "…"
year: "2025"
doi: "10.…"
fields: ["Marine ecology", "Climate change"]
keywords: […]
summarised_by: "qwen/qwen3.5-9b"
---
# Thermal Tolerance of Reef-Building Corals …
**Research question** …
**Methodology** …
**Key findings** …
**Limitations** (of this study itself) …
**Core takeaway** …
```

The notes are plain files: open them in any editor or in Obsidian. If you edit one,
BiblioMancer never overwrites your version (a later rewrite goes next to it as
`<note>.generated.md`).

`<data>/synthesis_matrix.md` is a one-table overview of the whole library.

## Failures and retries

A paper that fails (unreadable PDF, model error) is retried at the next update; after 3
attempts it is *given up* (tile on the Overview). **Browse → Failed / given up only**
shows why. Tick **Options → Retry failed papers** to try them again.

## Duplicates

The same paper downloaded twice has different bytes but the same DOI. BiblioMancer keeps
the **larger file** and deletes the others (PDF, note, catalogue entry) — but only when
the titles and first pages also match. Anything uncertain (a poster and its article, a
preprint and the published version) is left alone and listed as *REVIEW* in
`<data>/cache/logs/dedup_*.log`. Every deletion is recorded in `dedup_history.tsv`.

Next: [Browse and Folders](03-browse-and-folders.md).
