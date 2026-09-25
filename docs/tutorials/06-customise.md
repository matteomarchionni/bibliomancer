# 6. Customise

All of these are in **Options**; the scheduled run uses the same settings
(they are saved in `config.yaml` — see [configuration](../configuration.md)).

## How much of each paper is read

**Max tokens per document** and **Max pages per file**: whichever comes first. The hint
below the fields shows the characters read and the context the model is loaded with.
Larger values catch more of long papers but need more GPU memory and time. New limits
apply to papers read from then on; **Rebuild from scratch** re-reads everything.

## Speed

- **Reasoning off** (`reasoning_effort: none`, the default) makes summaries ~5× faster with
  no loss of quality in our tests.
- **Papers at the same time** (1–4) reads several papers in parallel — 2 was 1.7× faster —
  but loads the model with that many times the context. With a big token budget on a 12 GB
  GPU, keep it at 1 or halve the budget.

## A different model

**Model and folders → LM Studio model**: pick an installed model (or type its key) and
press **Save**. It is checked first — a model that is not installed, an embedding model or
one with too small a context is refused, with the list of installed models. Without vision,
scanned PDFs are skipped (and retried later).

## Your research domain and the prompt

Set `research_domain` in `config.yaml` (e.g. *marine ecology and climate
science*) so the model reads papers as a specialist in your field.

For full control, write your own system prompt in a `.txt` file and pick it under **Custom
prompt file**. It must ask for the same fields (title, authors, year, research_fields,
keywords, research_question, methodology, key_findings, limitations, core_takeaway) — the
answer format is enforced by a JSON schema. Update now applies it to new papers;
**Rebuild from scratch** (a backup is taken first) to all of them. The **Report** shows how
many notes were written with the current setup.

## The look

**Appearance → Edit theme** opens `theme.css`: colours, fonts, font size, transparency —
one commented line each. Save and press **Apply**. See [configuration](../configuration.md#the-look-themecss).

## Maintenance buttons

| Button | Does |
|---|---|
| **Report** | statistics per year, field, folder and type, failures, reading speed, setups; CSV of the whole catalogue |
| **Check setup** | folders, LM Studio, model, embeddings — changes nothing |
| **Update search index** | re-embeds new notes (done after every update anyway) |
| **Remove schedule** | removes the weekly / monthly task |
| **Rebuild from scratch…** | backs up, clears the catalogue and notes, re-reads all PDFs |

Next: [use it with AI agents](07-ai-agents.md).
