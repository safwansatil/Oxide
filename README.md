# Oxide

Oxide turns terminal work into a forensic timeline.

Normal shell history remembers command strings. Oxide records what actually
happened: command output, exit codes, timestamps, file hashes before and after,
file changes, Python imports, and a graph of command/file effects. Then you can
ask natural-language questions about your local execution history.

## Quick Start

```bash
cd /d/new_wrkspc/oxide
python -m pip install -e .
export PATH="$PATH:/c/Users/Safwan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Scripts"

oxide init
oxide run "python --version"
oxide status
oxide timeline --since "10 minutes ago"
```

For AI answers:

```bash
export OPENAI_API_KEY="paste-your-rotated-key-here"
```

You can also create a private `.env` file from `.env.example`. `.env` is ignored
by Git.

## Beginner Workflow

```bash
oxide init
oxide doctor

oxide run "echo 'version 1' > data.txt"
oxide run "cat data.txt"
oxide run "echo 'version 2' > data.txt"

oxide timeline --since "10 minutes ago"
oxide lineage data.txt
oxide graph
oxide ask "what changed in data.txt?"
```

## Commands

| Command | What it does |
| --- | --- |
| `oxide init` | Creates local `.oxide/oxide.db` history storage. |
| `oxide run "..."` | Runs a normal shell command and records what changed. |
| `oxide record ...` | Records argv-style commands with explicit shell control. |
| `oxide timeline` | Shows recorded commands with file effects and failure clues. |
| `oxide ask "..."` | Answers questions with OpenAI, or SQLite fallback if offline. |
| `oxide lineage FILE` | Shows which commands created or modified a file. |
| `oxide graph` | Prints an ASCII command-to-file effect graph. |
| `oxide status` | Summarizes command count, failures, and recent changes. |
| `oxide doctor` | Checks database, shell, Python, and API-key readiness. |
| `oxide guide` | Prints common beginner workflows. |
| `oxide reset --yes` | Deletes local `.oxide` history for a clean rehearsal. |

Full command guide: [`docs/COMMANDS.md`](docs/COMMANDS.md).

## Demo Flow

```bash
oxide reset --yes
rm -f data.txt
oxide doctor

oxide run "echo 'version 1' > data.txt"
oxide run "cat data.txt"
oxide run "python -c \"with open('data.txt', 'r') as f: print(f.read().upper())\""
oxide run "echo 'version 2' > data.txt"
oxide run "python -c \"import does_not_exist\""

oxide timeline --since "10 minutes ago"
oxide timeline --failures
oxide ask "why did the last command fail?"
oxide lineage data.txt
oxide graph
```

## What Oxide Records

Each command run stores:

- command text and command hash,
- UTC timestamp,
- stdout and stderr,
- exit code and spawn errors,
- before/after file snapshots,
- new, changed, and deleted files,
- Python module imports when Python commands run.

The database lives at:

```text
.oxide/oxide.db
```

## How It Works

1. Before a command, Oxide snapshots project files and hashes content with
   `blake2b`.
2. It runs the command through `subprocess`.
3. After the command, it snapshots files again and computes file effects.
4. It stores the execution record in SQLite.
5. `timeline`, `lineage`, and `graph` explain the stored evidence.
6. `ask` sends a compact context to OpenAI when `OPENAI_API_KEY` is available;
   otherwise it falls back to direct SQLite answers.

## Current Limitations

Oxide currently uses before/after snapshots. It reliably detects file writes and
modifications, but exact syscall-level read tracking is future work. Read
dependencies in the graph are conservative and based on pre-command snapshots.

## Docs

- [`docs/JUDGE_DEMO.md`](docs/JUDGE_DEMO.md): live demo script and judge talking points.
- [`docs/COMMANDS.md`](docs/COMMANDS.md): beginner command reference.
- [`docs/TECHNICAL_OVERVIEW.md`](docs/TECHNICAL_OVERVIEW.md): architecture, storage, graph, and AI details.
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md): demo-day fixes and setup checks.
