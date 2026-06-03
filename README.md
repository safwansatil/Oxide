# Oxide

Oxide records command executions, file snapshots, process output, and dependency
metadata so later tooling can explain how a workspace changed.

## Current layout

- `oxide_daemon/recorder.py`: wraps commands, hashes files with blake2b, captures
  stdout/stderr and exit codes, and stores execution records in SQLite.
- `oxide_daemon/storage.py`: SQLite persistence for command run records.
- `oxide_ai/`: placeholders for the natural language query layer.
- `cli.py`: small command line wrapper around the recorder.

## Usage

```powershell
python cli.py -- python -c "from pathlib import Path; Path('out.txt').write_text('ok')"
```

By default, records are written to `.oxide/oxide.sqlite3`.
