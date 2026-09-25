# BiblioMancer

**Your PDF library, read and summarised by a local AI model — searchable by meaning,
ready for deep research, and exportable to any AI agent. Nothing leaves your computer.**

BiblioMancer watches a folder of scientific papers. Every new PDF is read by a model
running in [LM Studio](https://lmstudio.ai) on your own GPU and turned into a structured
Markdown note: research question, methodology, key findings, the study's own limitations
and a one-sentence takeaway. On top of those notes you get search by meaning, a ranking
of your folders by topic, cited deep-research reports, reading lists, duplicate cleanup
and a weekly schedule — all from one control panel.

Sibling app: [PhotoMancer](https://github.com/matteomarchionni/photomancer) does the same
for a photo archive, with the same panel.

![BiblioMancer overview](docs/images/overview.png)

## Features

- **Automatic reading** — drop PDFs anywhere in your library and press *Update now*
  (or let the weekly/monthly schedule do it). Scanned PDFs are read with the model's
  vision. About 15 s per paper on a 12 GB GPU with `qwen/qwen3.5-9b`.
- **Notes you own** — plain Markdown files (Obsidian-friendly YAML front matter). Notes
  you edit by hand are never overwritten.
- **Browse** — the whole catalogue, searchable *by meaning* ("reefs recovering after
  heatwaves"), filterable by field, with each paper's summary beside the list.
- **Folders** — rank the folders of your library by how well their papers match a topic.
- **Research** — save a top-N reading list, or run a *deep research*: the model reads the
  top papers against your question and writes one report citing them as [1], [2]…
- **Duplicates** — the same paper downloaded twice (same DOI) is detected; the larger
  file is kept, anything ambiguous is left for you to review.
- **Export for AI agents** — one self-describing `literature_kb.md` (plus SQLite with
  full-text search, JSONL and a ZIP) for Claude, ChatGPT or a local model, rewritten
  after every run into a synced folder together with a full backup.
- **Scout briefs (optional)** — PDF digests of new literature (for example from a weekly
  AI "literature scout") with your highlights and comments are matched to your papers
  and projects.
- **Runs everywhere** — Windows, macOS and Linux; installers for all three (macOS and Linux: experimental).

## Requirements

- [LM Studio](https://lmstudio.ai) with a chat model (default `qwen/qwen3.5-9b`, vision
  recommended for scanned PDFs) and the embedding model `nomic-embed-text-v1.5`.
- A GPU with ~8–12 GB of memory is comfortable for the default model.
- To run from source: Python 3.10+.

## Install

**Installers** (no Python needed): download the latest release from the
[Releases page](https://github.com/matteomarchionni/bibliomancer/releases):

| System | File | Notes |
|---|---|---|
| Windows | `BiblioMancer-<version>-Setup.exe` | per-user install, no admin rights; optional desktop / Start-menu shortcuts |
| macOS *(experimental)* | `BiblioMancer-<version>-macos-arm64.dmg` / `-x86_64.dmg` | drag to Applications; unsigned: first launch with right-click → Open |
| Linux *(experimental)* | `BiblioMancer-<version>-x86_64.AppImage` | `chmod +x` and run; the first-run window offers a menu entry |

> **macOS and Linux builds are experimental**: they are built automatically but have
> not been tested on real machines yet. Please report problems in the issues; running
> from Python (below) works everywhere.

**With Python** (any system):

```
pipx install git+https://github.com/matteomarchionni/bibliomancer
bibliomancer                  # opens the panel (first run: a short setup window)
bibliomancer shortcuts --menu --desktop
```

**From source**:

```
git clone https://github.com/matteomarchionni/bibliomancer
cd bibliomancer
python -m pip install -e .
python BiblioMancer.pyw       # or BiblioMancer.cmd on Windows
```

## Quick start

1. Start LM Studio once and download the models (the setup window checks and links them).
2. Open BiblioMancer. On first run it asks for your **PDF library**, a local **data
   folder** (notes and database) and, optionally, a **synced export folder**.
3. Press **Update now**. Watch the progress bars; notes appear in the data folder.
4. Try **Browse** and type a topic. Double-click a paper to open its note.

The [tutorials](docs/tutorials/) walk through every page step by step.

## Documentation

| | |
|---|---|
| [Tutorials](docs/tutorials/) | installation, first update, search, research, scheduling, customisation, agents, scout briefs |
| [Configuration](docs/configuration.md) | every setting in `config.yaml` |
| [Command line](docs/command-line.md) | every panel button is a command too |
| [Troubleshooting](docs/troubleshooting.md) | common problems and fixes |
| [Packaging](packaging/README.md) | how the installers are built |

## How it works

```
PDF library ──► text (PDFium; vision OCR for scans) ──► local model (LM Studio)
     │                                                      │ structured JSON
     ▼                                                      ▼
duplicate check                              Markdown note + SQLite catalogue
                                                            │
                         embeddings (nomic-embed) ◄─────────┤
                                  │                         ▼
                  Browse / Folders / Research      export for AI agents + backup
```

Everything runs on your machine; the only network traffic is to LM Studio on
`127.0.0.1`. Settings live in a commented `config.yaml`; the look in a CSS-style
`theme.css`.

## Development

```
python -m pip install -e ".[test]"
python -m pytest -q
```

The control-panel frame, theme, scheduler, LM Studio client and installer tooling are
shared with PhotoMancer: `src/bibliomancer/_shared/` is kept byte-identical in both
repositories with `python tools/sync_shared.py push` (a test fails if it drifts).

## License

[MIT](LICENSE) © 2026 Matteo Marchionni
