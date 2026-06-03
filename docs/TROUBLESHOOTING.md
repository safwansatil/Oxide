# Oxide Troubleshooting And Demo Runbook

## `oxide: command not found`

Install Oxide and add the Python scripts folder to PATH:

```bash
cd /d/new_wrkspc/oxide
python -m pip install -e .
export PATH="$PATH:/c/Users/Safwan/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/Scripts"
oxide --help
```

## WSL `/bin/bash` Error

Symptom:

```text
WSL ERROR: execvpe(/bin/bash) failed: No such file or directory
```

Cause:

Windows resolved `bash` to a WSL shim instead of Git Bash.

Fix:

Run the latest code and check:

```bash
oxide doctor
```

It should show Git Bash similar to:

```text
git bash       C:\Program Files\Git\bin\bash.exe
```

If needed, force the shell:

```bash
export OXIDE_SHELL="/c/Program Files/Git/bin/bash.exe"
```

## `python: command not found` Inside `oxide run`

Oxide now prepends its own Python runtime to child command PATH.

Verify:

```bash
oxide run "python --version"
```

## Bad Old Demo History

If your database contains failed old runs, reset before presenting:

```bash
oxide reset --yes
rm -f data.txt
```

Then rerun the demo.

## OpenAI Key Not Found

Symptom:

```text
OpenAI was unavailable, so I answered from SQLite directly (OPENAI_API_KEY is not set).
```

This is not fatal. SQLite fallback still works.

For AI answers:

```bash
export OPENAI_API_KEY="paste-your-rotated-key-here"
```

Or create a private `.env` file:

```bash
cp .env.example .env
```

Then edit `.env`.

Never commit `.env`.

## Judge-Day Checklist

Run:

```bash
oxide doctor
oxide reset --yes
rm -f data.txt
oxide run "echo 'version 1' > data.txt"
oxide run "cat data.txt"
oxide run "echo 'version 2' > data.txt"
oxide lineage data.txt
oxide graph
```

If those work, the core demo is ready.
