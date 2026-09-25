# 5. Scheduling, export and backup

## Weekly or monthly updates

On the Overview page, choose **Weekly** or **Monthly** under *Repeat*, then press
**Update now**. It runs now and registers a task with your operating system from that
moment (same weekday / day of month, same time):

| System | Registered as |
|---|---|
| Windows | a Task Scheduler task "BiblioMancer Update" (runs only when you are logged on, never elevated) |
| macOS | a launchd agent in `~/Library/LaunchAgents` |
| Linux | a systemd user timer (or a crontab line) |

If the computer is off at that time, the run happens as soon as it is back on (Windows,
Linux). Scheduled runs have a 4-hour limit, show no window, and write
`<data>/cache/logs/run_scheduled.log`. If you open the panel while one is running, it
follows it live and keeps the run buttons locked until it ends.

*One-shot* runs once and leaves an existing schedule alone. **Options → Maintenance →
Remove schedule** removes it.

## The export folder

If you set a **synced export folder**, every run rewrites it with:

| File | For |
|---|---|
| `literature_kb.md` | the one file to give an AI agent (tutorial 7) |
| `literature_kb_index.md` | one line per paper — small enough for any agent |
| `literature_kb.sqlite` | all fields plus a full-text index, for agents that run code |
| `literature_kb.jsonl`, `literature_kb.zip` | every record; everything in one archive |
| `kb_doi_index.tsv` | DOIs you already own (e.g. for a literature scout) |
| `Notes/` | a mirror of your notes |
| `pipeline_backup/` | the database, scout briefs, projects, code and config |

The folder belongs to one BiblioMancer data folder: a second copy (or a test) pointed at it
is refused, so it can never overwrite the real export by accident.

## Restoring

Lost the computer? Install BiblioMancer, choose an empty data folder, copy
`pipeline_backup/manifest.db` into `<data>/cache/`, copy `Notes/`, `Scouts/` and
`Projects/` into `<data>/`, and press **Update now**. Your PDFs are only read, so as long
as the library is intact nothing needs to be summarised again.

Next: [customise](06-customise.md).
