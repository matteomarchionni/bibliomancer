# Configuration

BiblioMancer keeps its settings in one commented YAML file, `config.yaml`. The panel
edits the common ones (**Options**); everything else can be edited by hand
(**Options → Model and folders → Edit config.yaml**). The scheduled run reads the same
file, so a change applies everywhere.

## Where the files are

| | Installed app | Source checkout |
|---|---|---|
| `config.yaml`, `theme.css` | Windows `%APPDATA%\BiblioMancer`, macOS `~/Library/Application Support/BiblioMancer`, Linux `~/.config/bibliomancer` | next to the code, if a `config.yaml` is there ("portable" mode) |
| data (notes, database, caches, logs) | `data_dir`, chosen at first run (default `Documents/BiblioMancer`) | `data_dir`, or the code folder in portable mode |
| panel preferences (`gui_settings.json`) | Windows `%LOCALAPPDATA%\BiblioMancer`, macOS as above, Linux `~/.local/state/bibliomancer` | same |

`BIBLIOMANCER_CONFIG=<file>` points to another `config.yaml`;
`BIBLIOMANCER_HOME`, `BIBLIOMANCER_PAPERS_DIR` and `BIBLIOMANCER_EXPORT_DIR` override the
three folders for one run (handy for tests).

## Settings

| Key | Default | Meaning |
|---|---|---|
| `papers_dir` | – | Your PDF library. Only ever read — except duplicates, which are deleted (see *Duplicates* in the tutorials). |
| `export_dir` | – | A synced folder (Google Drive, OneDrive…) rewritten after every run with the export for AI agents and a full backup. Empty = no export. |
| `data_dir` | – | Notes, database, caches, reports. **Use a local disk**: SQLite on a cloud-drive mount can be locked by the sync client mid-run. |
| `model` | `qwen/qwen3.5-9b` | LM Studio chat model that reads the papers. With vision it also reads scanned PDFs. |
| `embedding_model` | `text-embedding-nomic-embed-text-v1.5` | For search by meaning. |
| `reasoning_effort` | `none` | `none` switches the model's reasoning off: ~15 s instead of ~80 s a paper, no truncated answers. `''` = the model's default. Deep research always reasons. |
| `research_domain` | `''` | Your field(s), e.g. `marine ecology and climate science`. The model then reads papers as a specialist. |
| `max_doc_tokens` | `9400` | How much of each paper the model reads (≈ 3.2 characters per token). The model's context window grows with it. |
| `max_pages` | `12` | Pages extracted per PDF. Whichever of the two limits comes first applies. |
| `lms_path` | `''` | LM Studio's `lms` command line; empty = found automatically. |
| `base_url` | `http://127.0.0.1:1234/v1` | LM Studio server. Use `127.0.0.1`, not `localhost` (on Windows `localhost` adds ~2 s to every request). |
| `parallel` | `1` | Papers read at the same time (1–4). 2 was 1.7× faster in a test, but the model is loaded with that many times the context: check your GPU memory. |

### Sizing the limits

The context the model is loaded with is derived from `max_doc_tokens` (shown under the
fields in Options). Reference points on a 12 GB GPU with `qwen/qwen3.5-9b`: 9,400 tokens
→ 24k context; 30,000 tokens → 49k context (fits); 30,000 × `parallel: 2` → 98k context
(fills the GPU). A paper whose text is still too long (dense formulas and tables) is read
shortened to fit, automatically.

## The look: `theme.css`

**Options → Appearance → Edit theme**. CSS custom properties, one per element, each with a
comment saying what it paints:

```css
:root {
  --background: #111111;                 /* window background */
  --card: rgba(255, 255, 255, 0.06);     /* cards; see-through colours blend into the background */
  --font-display: "Georgia", "Segoe UI"; /* first installed font wins */
  --font-scale: 1.15;                    /* every font size x this */
  --window-opacity: 0.95;                /* whole window */
}
```

Save, then **Apply** (the panel restarts). Mistyped values are listed in the activity log
and ignored; **Reset to default** rewrites the file.

## Custom prompt

Write a system prompt in a `.txt` file and pick it under **Custom prompt file**. It applies
to papers read from then on; **Rebuild from scratch** re-reads everything with it. The
report shows how many notes were written with the current setup (model, prompt, limits).

## Projects (for scout briefs)

`<data>/Projects/projects.json` lists your projects, so scout briefs that say
"For *Project*." are linked to them — see
[tutorial 8](tutorials/08-scout-briefs-and-projects.md).
