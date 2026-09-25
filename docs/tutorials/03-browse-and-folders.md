# 3. Browse and Folders

## Browse

The **Browse** page lists the whole catalogue (newest first).

- Type a topic and press Enter or **Search**. With **By meaning** ticked, papers are found
  by what they are about even when they use other words — "reefs recovering after
  heatwaves" finds papers on coral resilience. The *Match* column shows the similarity, best
  first; only the relevant ones are listed. Untick it for exact words.
- The **field** list filters by research field; **Failed / given up only** shows papers
  that could not be read, with the reason.
- Select a paper to read its summary on the right: takeaway, key findings, limitations,
  fields and keywords. **Open note**, **Open PDF** and **Show in folder** do what they say;
  double-click opens the note.

The search box at the top right of every page jumps to Browse with your query.

How search works: every note is embedded (title, fields, keywords, research question,
findings, takeaway) with the `nomic-embed-text` model; a query is embedded the same way and
compared, with a small bonus when a query word literally appears in the title, fields or
keywords. The index is updated after every run.

## Folders

If your library is organised in folders, **Folders** tells you which ones are about a topic.

1. Type a topic and pick the level: the paper's own folder, the top level, or the second
   level of your library.
2. Each folder gets a **Score** (its best papers plus the folder as a whole) and
   **Matching** (papers that are a hit / papers in the folder). Click a column title to sort.
3. Select a folder to see its best matches, its most common fields and keywords.
   **Show in Browse** lists the matching papers of that folder; double-click opens it.

Next: [reading lists and deep research](04-reading-lists-and-deep-research.md).
