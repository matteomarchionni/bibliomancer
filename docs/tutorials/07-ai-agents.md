# 7. Use it with AI agents

The export folder (tutorial 5) is made for other AI tools: Claude, ChatGPT, Gemini, or a
local model in LM Studio.

## The one-file way

Give the agent **`literature_kb.md`**. It explains itself ("How to use this file"),
includes your research context (from `projects.json`), an index of every paper and every
full note. Example prompts:

> Use `literature_kb.md` as my literature database; read its "How to use" section first.
> Which papers are the closest competitors to my project on reef monitoring, and what
> do their limitation sections say?

> From `literature_kb.md`, list the papers that compare coral-cover survey
> methods, with year and DOI, as a table.

## Too big for the agent?

Give it **`literature_kb_index.md`** (one line per paper — title, year, fields, takeaway,
DOI, note file) and let it ask for the notes it needs from `Notes/`.

## Agents that run code

`literature_kb.sqlite` has a `papers` table with every field and the note text, and a
`papers_fts` full-text index:

```sql
SELECT title, year, doi FROM papers
WHERE rowid IN (SELECT rowid FROM papers_fts WHERE papers_fts MATCH 'bleaching AND recovery');
```

`literature_kb.jsonl` has one JSON record per paper. `README_FOR_AGENTS.md` in the export
folder describes every file.

## Keeping it current

The export is rewritten after every update, so a cloud folder (Google Drive, OneDrive,
Dropbox) gives your agents an up-to-date library wherever they run.

Next: [scout briefs and projects](08-scout-briefs-and-projects.md).
