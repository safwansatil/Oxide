# Oxide Command Reference

This guide explains every command currently exposed by `oxide --help`.

## `oxide init`

Creates local project storage.

```bash
oxide init
```

Use this once per project. It creates:

```text
.oxide/oxide.db
```

It can also create a private `.env` placeholder for `OPENAI_API_KEY`.

## `oxide run "command"`

Runs a normal shell command and records the result.

```bash
oxide run "python -m pytest"
oxide run "echo 'version 1' > data.txt"
oxide run "cat data.txt"
```

Use this for everyday terminal commands. It supports shell features like
redirection, quotes, and pipes because the command is executed through the
shell.

Records stdout, stderr, exit code, command hash, file changes, timestamp, and
Python imports when available.

## `oxide record ...`

Lower-level command recorder.

```bash
oxide record -- python -m pytest
oxide record --shell "echo hello > out.txt"
```

Most beginners should use `oxide run`. Use `record` when you need explicit
argv-style behavior or shell control.

## `oxide timeline`

Shows recorded commands in human-readable order.

```bash
oxide timeline
oxide timeline --since "1 hour ago"
oxide timeline --since today
oxide timeline --failures
oxide timeline --limit 5
```

The timeline explains whether a command passed or failed, which files were
created/modified/deleted, output previews, Python traceback locations, and a
concise failure reason.

Example failure explanation:

```text
failure inline Python line 1: ModuleNotFoundError: No module named 'does_not_exist'
```

## `oxide ask "question"`

Asks a natural-language question about recorded history.

```bash
oxide ask "what commands today"
oxide ask "what changed in data.txt?"
oxide ask "why did the last command fail?"
```

If `OPENAI_API_KEY` is set, Oxide sends compact forensic context to OpenAI. If
no key is available, Oxide falls back to direct SQLite answers.

## `oxide lineage FILE`

Shows how a file was created or modified.

```bash
oxide lineage data.txt
oxide lineage oxide_daemon/recorder.py
oxide lineage --json data.txt
```

Use this to answer what generated a file, which command last modified it, and
what the old/new hashes were.

## `oxide graph`

Prints an ASCII graph of recent command/file effects.

```bash
oxide graph
```

Use this to show the relationship between commands and written files.

## `oxide status`

Summarizes the project.

```bash
oxide status
```

Shows database path, command count, failure count, file-change count, OpenAI key
status, last command, recent failures, and recent file changes.

## `oxide doctor`

Checks whether the local setup is ready.

```bash
oxide doctor
```

Checks current directory, database path, database existence, OpenAI key presence,
chosen model, Git Bash detection on Windows, and `OXIDE_SHELL`.

## `oxide guide`

Prints a beginner-friendly workflow directly in the terminal.

```bash
oxide guide
```

Use it when you forget the flow.

## `oxide reset --yes`

Deletes local recorded history.

```bash
oxide reset --yes
```

Use this only when rehearsing from a clean state. It deletes `.oxide`.
