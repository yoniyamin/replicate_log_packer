# clear_stale_cache

Removes cached job folders whose analysis is outdated. Run this after upgrading LogPacker so that previously analysed logs are re-analysed with the latest collectors when re-uploaded.

## Usage

```bash
# Dry-run — lists stale jobs without deleting anything
python -m tools.clear_stale_cache

# Delete stale jobs
python -m tools.clear_stale_cache --delete
```

## What it does

Each analysed log is stored as a job folder under the LogPacker data directory (`~/.logpacker/jobs/` by default, or `LOGPACKER_DATA_DIR` if set). Every job's `report.json` records the `analysis_version` it was built with.

The script compares each job's version against the current `ANALYSIS_VERSION` and flags any mismatches as stale. In dry-run mode it prints a summary; with `--delete` it removes the stale folders from disk.

Stale jobs that are not deleted will still show up in the UI, but packing will fail with a message asking the user to re-upload. Cleaning them avoids that confusion.

## Example output

```
Current analysis version: 4
Jobs directory:           C:\Users\YAM\.logpacker\jobs
Up-to-date jobs:          3
Stale jobs:               2

  STALE    08d49cabc565  v3  my_task_log.txt
  STALE    465cd725d620  v3  another_log.log

Dry-run complete. Re-run with --delete to remove stale jobs.
```
