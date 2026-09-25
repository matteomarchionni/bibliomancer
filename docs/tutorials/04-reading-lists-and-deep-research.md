# 4. Reading lists and deep research

Both live on the **Research** page and use the same ranking as Browse.

## A top-N reading list

1. Type a topic and press **Preview**: the best N papers appear (set **Top N**, 1–100).
2. **Save top N list** writes a `.txt` into `<data>/Research/` — title, year, DOI, PDF path
   and note path of each paper — and opens it.

## Deep research

A deep research answers a question from your own papers, with citations.

1. Type the topic (it picks the papers) and set **Top N** (8–15 is a good start).
2. Write your **question** in the box below, e.g. *Which coral species recovered after
   repeated bleaching, and how was recovery measured?* Leave it empty to get a summary
   of what the papers say about the topic.
3. Press **Deep research on top N** and confirm.

The model reads each paper — its note plus an excerpt of the paper's own text — against
your question, then writes one report:

- *Short answer* · *What the papers show* (by theme) · *Disagreements and gaps* ·
  *Implications* · *Papers that did not help*
- every claim cited as [1], [2]…, a sources table with DOIs and PDF paths, and the
  per-paper extracts at the end.

It takes about 1–2 minutes per paper plus a few minutes for the report; the progress bars
show where it is, and the report (`<data>/Research/<date>_<topic>_report.md`) opens when
done. Write the question in another language and the report comes back in that language.

Deep research always lets the model reason (it improves the synthesis), and reads more of
each paper when you raise *Max tokens per document*.

Next: [scheduling, export and backup](05-scheduling-export-backup.md).
