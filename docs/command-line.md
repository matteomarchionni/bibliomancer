# Command line

Every panel button runs one of these commands, so everything can also be scripted.

| How you installed | Command |
|---|---|
| installer | `bibliomancer-cli <command>` in the install folder |
| pip / pipx | `bibliomancer <command>` (or `python -m bibliomancer <command>`) |
| source checkout | `BiblioMancer.cmd <command>` (Windows) or `python BiblioMancer.pyw <command>` |

No command opens the panel.

## Commands

```
update  [--limit N] [--max-minutes M] [--retry-failed] [--no-vision]
        [--skip-supplementary] [--system-prompt-file F]
                          the whole pipeline: model, scout briefs, incoming duplicates,
                          new papers, duplicates, search index, matrix, export
rebuild [same options]    back up, clear the catalogue and notes, re-read everything
post                      duplicates + search index + matrix + export only
plan                      what an update would process (changes nothing)
check                     folders, LM Studio, the model, embeddings
report                    statistics + CSV in <data>/Reports
search "question" [--semantic]
research --query "topic" --top N [--list-only | --question "..."] [--exact]
dedup                     report duplicates (add --apply to delete them)
index                     update the search index
export [--dest DIR] [--force]
scouts                    re-read the scout briefs
scheduled <command ...>   run a command with its output appended to
                          <data>/cache/logs/run_scheduled.log (what the weekly task runs)
unschedule                remove the weekly / monthly task
shortcuts [--menu] [--desktop] [--remove]
setup                     the first-run window (folders, LM Studio check)
gui                       the panel
```

## Examples

```
bibliomancer update --limit 5                       # try the pipeline on 5 new papers
bibliomancer research --query "coral bleaching" --top 8 \
                      --question "Which methods work without a nuclear stain?"
bibliomancer search "foundation models for pathology" --semantic
bibliomancer export --dest D:\share\literature      # a one-off export elsewhere
```

Exit code 0 = success. Progress lines (`=== step ===`, `[i/n] file`, `OK 14.2s`) are what
the panel turns into its progress bars.
