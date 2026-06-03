# Oxide

Oxide records command executions, file snapshots, process output, and dependency
metadata so later tooling can explain how a workspace changed.

## Current layout

- `oxide_daemon/recorder.py`: wraps commands, hashes files with blake2b, captures
  stdout/stderr and exit codes, and stores execution records in SQLite.
- `oxide_daemon/storage.py`: SQLite persistence for command run records.
- `oxide_ai/`: natural language query layer, graph analysis, and terminal UI.
- `cli.py`: Click command line wrapper around the recorder.

## Usage

```powershell
python -m pip install -e .
oxide run "echo version 1 > data.txt"
oxide ask "what commands today"
```

By default, records are written to `.oxide/oxide.db`.

## Setup For Git Bash

```bash
cd /d/new_wrkspc/oxide
python -m pip install -e .
export PATH="$PATH:/c/Users/Safwan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Scripts"
oxide doctor
```

For AI answers, set your key outside Git:

```bash
export OPENAI_API_KEY="paste-your-key-here"
```

Or create a private `.env` file from `.env.example`. `.env` is ignored by Git.

## Demo Flow

```bash
oxide reset --yes
oxide doctor
oxide run "echo 'version 1' > data.txt"
oxide run "cat data.txt"
oxide run "python -c \"with open('data.txt', 'r') as f: print(f.read().upper())\""
oxide run "echo 'version 2' > data.txt"
oxide run "python -c \"import does_not_exist\""
oxide ask "what commands today"
oxide ask "what changed in data.txt?"
oxide lineage data.txt
oxide graph
```
