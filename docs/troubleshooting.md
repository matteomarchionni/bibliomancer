# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "LM Studio off" in the sidebar | Normal: *Update now* starts it, and so do Browse, Folders and Research when they need to search by meaning. If that fails, open LM Studio once by hand. **Check setup** (Options → Maintenance) tells you what is missing. |
| Every paper fails with HTTP 400 | The model was loaded with too small a context (by hand, or by another app). *Update now* reloads it correctly — do not load it from LM Studio yourself. The log says `model loaded with a N-token context`. |
| One paper keeps failing | After 3 attempts it is given up (the *Given up* tile; *Browse → Failed / given up only* shows why). Tick *Retry failed papers* to try again. Very long, formula-heavy papers are read shortened automatically. |
| A run stopped in the middle | Nothing is lost: every paper is saved as it completes. Press *Update now* again. (Overnight Windows Update restarts do this.) |
| "Semantic index not available" / search stays on exact words | LM Studio could not be started, or the embedding model is not downloaded (the activity log says which). Search falls back to exact words. Install `nomic-embed-text-v1.5` in LM Studio; *Update search index* rebuilds the index. |
| Scanned PDFs are skipped | The model has no vision. Pick a vision model, or untick *Vision OCR*. |
| The weekly run did not happen | The PC was off: it runs as soon as the PC is back on. The log is `<data>/cache/logs/run_scheduled.log`. Re-choose *Weekly* after moving the program. |
| "EXPORT SKIPPED: … holds the export of another knowledge base" | The export folder belongs to a different BiblioMancer data folder (a second copy or a test). Choose another folder, or `bibliomancer export --force`. |
| A note I edited was not updated | On purpose: hand-edited notes are never overwritten. The new version is next to it as `<note>.generated.md`. |
| macOS: "cannot be opened because the developer cannot be verified" | The build is unsigned: right-click the app → Open, once. |
| Windows: SmartScreen "unknown publisher" | The installer is unsigned: More info → Run anyway. |
| Linux: the AppImage does not start | `chmod +x` it; some distributions need `libfuse2` for AppImages. |

## Restoring on a new PC

Everything irreplaceable is in the export folder's `pipeline_backup/` (database, scout
briefs, projects, code, config) and `Notes/`. Install BiblioMancer, choose an empty data
folder at first run, then copy `manifest.db` into `<data>/cache/`, the `Notes`, `Scouts`
and `Projects` folders into `<data>/`, and press *Update now*.

## Logs

**Logs** in the sidebar opens `<data>/cache/logs`: `run_scheduled.log` (scheduled runs),
`weekly_*.log` (ingestion detail per day), `dedup_*.log` and `dedup_history.tsv` (every
deleted duplicate).
