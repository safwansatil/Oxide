# Oxide Judge Demo Script

Use this as your live walkthrough. The goal is to show that Oxide does more than remember terminal commands: it records command effects and explains them later.

## One-Sentence Pitch

Oxide is a forensic command recorder for developers: it records what commands ran, what files changed, what outputs happened, and then lets you ask natural-language questions about why something changed or failed.

## Pre-Demo Setup

From Git Bash:

```bash
cd /d/new_wrkspc/oxide
export PATH="$PATH:/c/Users/Safwan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Scripts"
oxide doctor
```

If you want a clean demo:

```bash
oxide reset --yes
rm -f data.txt
oxide init
oxide doctor
```

For AI answers, set a private API key outside Git:

```bash
export OPENAI_API_KEY="paste-your-rotated-key-here"
```

If no API key is set, Oxide still works using SQLite fallback answers. That is a good reliability point to mention.

## Live Demo Commands

Run these one by one:

```bash
oxide run "echo 'version 1' > data.txt"
oxide run "cat data.txt"
oxide run "python -c \"with open('data.txt', 'r') as f: print(f.read().upper())\""
oxide run "echo 'version 2' > data.txt"
oxide run "python -c \"import does_not_exist\""
```

Then ask questions:

```bash
oxide ask "what commands today"
oxide ask "what changed in data.txt?"
oxide timeline --failures
oxide lineage data.txt
oxide graph
```

Optional:

```bash
oxide timeline --since "10 minutes ago"
```

## What To Say While Demoing

1. "First, I am running normal shell commands through `oxide run`. Oxide does not need me to manually describe what happened."
2. "Before every command, Oxide snapshots the project files using blake2b hashes."
3. "After every command, it snapshots again and stores the diff: new files, changed files, deleted files, stdout, stderr, exit code, timestamp, and Python imports."
4. "Now I intentionally break a command by importing a missing Python module."
5. "The timeline gives a plain-English clue: the command failed in inline Python and shows the final exception."
6. "The key part is that I can now ask questions after the fact. Oxide can explain command history, file lineage, and failures."

## Expected Story

After the demo commands, `data.txt` has two important events:

- It was created by `echo 'version 1' > data.txt`.
- It was modified by `echo 'version 2' > data.txt`.

The failed command:

```bash
python -c "import does_not_exist"
```

should show exit code `1`, stderr output, and appear under failures.

## Strong Judge Explanation

"Traditional shell history only stores strings. Oxide stores forensic evidence. It records command outputs, exit codes, file hashes before and after, and dependency hints. That means it can answer questions like 'what generated this file?', 'what changed before the failure?', and 'which command modified this output?'"

## If Asked: Is This Fully Production-Ready?

Say:

"This is an MVP with production-minded primitives. It has robust SQLite storage, hashing, error handling, command output capture, and graph analysis. The current file lineage is based on before/after snapshots, so it reliably detects writes and modifications. Full syscall-level read tracking is the next step."

## Best Closing Line

"Oxide turns a terminal session from a disposable stream of commands into a searchable forensic timeline."
