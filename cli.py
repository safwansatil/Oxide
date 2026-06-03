"""Command line entry point for Oxide."""

from __future__ import annotations

import json
import os
import re
import shutil
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import click

from oxide_ai.terminal import (
    BAD,
    GOOD,
    WARN,
    banner,
    bullet,
    color,
    command_line,
    kv,
    last_tree_line,
    panel,
    section,
    status,
    status_for_exit,
    tip,
    tree_line,
)
from oxide_daemon.recorder import CommandRecorder


@click.group(
    invoke_without_command=True,
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    help=(
        "Record shell commands, inspect what changed, and ask questions about "
        "your local execution history.\n\n\b\n"
        "Common flow:\n"
        "  oxide init\n"
        "  oxide run \"python -m pytest\"\n"
        "  oxide timeline --failures\n"
        "  oxide lineage path/to/file.py\n"
        "  oxide ask \"why did the last command fail?\""
    ),
)
@click.option("--db", help="Path to the SQLite database.")
@click.option("--cwd", help="Working directory to snapshot and execute in.")
@click.option("--timeout", type=float, help="Command timeout in seconds.")
@click.option("--shell", is_flag=True, help="Run the command through the platform shell.")
@click.pass_context
def cli(
    ctx: click.Context,
    db: str | None,
    cwd: str | None,
    timeout: float | None,
    shell: bool,
) -> None:
    """Record commands and ask questions about Oxide history."""

    if ctx.invoked_subcommand is not None:
        return
    command_parts = list(ctx.args)
    if command_parts[:1] == ["--"]:
        command_parts = command_parts[1:]
    if not command_parts:
        click.echo(banner())
        click.echo(
            panel(
                "QUICK START",
                [
                    "oxide init",
                    "oxide run \"python --version\"",
                    "oxide timeline --since \"10 minutes ago\"",
                    "oxide ask \"why did the last command fail?\"",
                ],
            )
        )
        click.echo(ctx.get_help())
        return
    _record_command(command_parts, db=db, cwd=cwd, timeout=timeout, shell=shell)


@cli.command(
    short_help="Record argv-style commands without shell redirection by default.",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--db", help="Path to the SQLite database.")
@click.option("--cwd", help="Working directory to snapshot and execute in.")
@click.option("--timeout", type=float, help="Command timeout in seconds.")
@click.option("--shell", is_flag=True, help="Run the command through the platform shell.")
@click.argument("command", nargs=-1, required=True)
def record(
    command: tuple[str, ...],
    db: str | None,
    cwd: str | None,
    timeout: float | None,
    shell: bool,
) -> None:
    """Record a command execution.

    Prefer `oxide run "command"` for normal terminal commands. This command is
    useful when you want argv-style execution and explicit shell control.
    """

    command_parts = list(command)
    if command_parts[:1] == ["--"]:
        command_parts = command_parts[1:]
    _record_command(command_parts, db=db, cwd=cwd, timeout=timeout, shell=shell)


@cli.command(
    short_help="Run a normal shell command and save what happened.",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.option("--db", help="Path to the SQLite database.")
@click.option("--cwd", help="Working directory to snapshot and execute in.")
@click.option("--timeout", type=float, help="Command timeout in seconds.")
@click.option("--no-shell", is_flag=True, help="Run as argv instead of through the shell.")
@click.argument("command", nargs=-1, required=True)
def run(
    command: tuple[str, ...],
    db: str | None,
    cwd: str | None,
    timeout: float | None,
    no_shell: bool,
) -> None:
    """Run a normal shell command and save what happened."""

    command_parts = list(command)
    if command_parts[:1] == ["--"]:
        command_parts = command_parts[1:]
    _record_command(command_parts, db=db, cwd=cwd, timeout=timeout, shell=not no_shell)


@cli.command(short_help="Ask a natural-language question about history.")
@click.argument("question")
def ask(question: str) -> None:
    """Ask natural language about your command history."""

    click.echo(banner("OXIDE ASK", "NATURAL LANGUAGE FORENSICS"))
    click.echo(panel("QUESTION", [question]))
    try:
        from oxide_ai.query_engine import answer_question

        answer = answer_question(question)
        click.echo(panel("ANSWER", answer.splitlines() or ["No answer returned."]))
    except Exception as exc:
        click.echo(panel("ERROR", [str(exc), "Fallback: try `oxide timeline --help`"]), err=True)


@cli.command(short_help="Show which commands created or changed a file.")
@click.option("--json", "as_json", is_flag=True, help="Print raw JSON lineage.")
@click.argument("filepath")
def lineage(filepath: str, as_json: bool) -> None:
    """Show complete lineage of how a file was created."""

    from oxide_ai.query_engine import _get_file_lineage

    try:
        data = _get_file_lineage(filepath)
    except Exception as exc:
        click.echo(banner("OXIDE LINEAGE", "NO HISTORY"))
        click.echo(panel("NOT READY", [str(exc), "Run `oxide init`, then record commands with `oxide run \"...\"`."]))
        return
    if as_json:
        click.echo(json.dumps(data, indent=2))
        return
    click.echo(banner("OXIDE LINEAGE", data["file"]))
    click.echo(panel("FILE HISTORY", _lineage_lines(data)))


@cli.command(short_help="Show recent commands with plain-English explanations.")
@click.option("--since", help='Start time like "2 hours ago", "today", or "3pm".')
@click.option("--until", help='End time like "now", "yesterday", or "4:30pm".')
@click.option("--limit", default=25, show_default=True, help="Maximum commands to show.")
@click.option("--failures", is_flag=True, help="Only show commands that failed.")
def timeline(since: str | None, until: str | None, limit: int, failures: bool) -> None:
    """Show command timeline with natural language time parsing."""

    from oxide_ai.query_engine import (
        _exit_code,
        _load_command_rows,
        _output_preview,
        _parse_time_reference,
        _parse_timestamp,
        _resolve_db_path,
    )

    click.echo(banner("OXIDE TIMELINE", "RECORDED COMMANDS"))
    db_path = _resolve_db_path(".oxide")
    if not db_path.exists():
        click.echo(panel("NOT READY", ["No .oxide/oxide.db found.", "Run `oxide init`, then record commands with `oxide run \"...\"`."]))
        return
    try:
        rows = _load_command_rows(db_path, limit=None, ascending=True)
        start = _parse_time_reference(since) if since else datetime.min.replace(tzinfo=timezone.utc)
        end = _parse_time_reference(until) if until else datetime.max.replace(tzinfo=timezone.utc)
    except Exception as exc:
        click.echo(panel("TIMELINE ERROR", [str(exc), 'Try a simpler time like `--since "1 hour ago"` or `--since today`.'], border=BAD))
        return
    click.echo(
        panel(
            "TIME RANGE",
            [
                kv("since", since or "beginning"),
                kv("until", until or "now"),
                kv("filter", "failures only" if failures else "all commands"),
                kv("database", db_path),
            ],
        )
    )
    emitted = 0
    for row in rows:
        timestamp = _parse_timestamp(row["timestamp"])
        if not start <= timestamp <= end:
            continue
        exit_code = _exit_code(row["output_snapshot"])
        if failures and exit_code == 0:
            continue
        command = row["command"]
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        emitted += 1
        click.echo(section(f"run {row['id']}"))
        click.echo(command_line(row["id"], row["timestamp"], exit_code, command))
        for line in _timeline_details(row):
            click.echo(line)
        if emitted >= max(1, limit):
            break
    if emitted == 0:
        click.echo(panel("NO MATCHES", ["No commands were recorded in that time range."]))
    else:
        click.echo(tip("Use `oxide lineage <file>` for file history, or `oxide ask \"why did this fail?\"` for AI analysis."))


@cli.command(short_help="Print an ASCII graph of commands and file effects.")
def graph() -> None:
    """Print ASCII graph of recent execution."""

    from oxide_ai.graph_analyzer import ExecutionGraph

    execution_graph = ExecutionGraph(".oxide/oxide.db")
    click.echo(banner("OXIDE GRAPH", "COMMAND -> FILE EFFECTS"))
    click.echo(execution_graph.ascii_recent(limit=10))


@cli.command(short_help="Create local Oxide history storage and env template.")
@click.option("--env-file/--no-env-file", default=True, show_default=True, help="Create a private .env template if missing.")
def init(env_file: bool) -> None:
    """Initialize Oxide in the current project."""

    from oxide_daemon.storage import SQLiteCommandStore

    oxide_dir = Path(".oxide")
    db_path = oxide_dir / "oxide.db"
    SQLiteCommandStore(db_path)
    lines = [
        kv("database", db_path.resolve()),
        kv("history dir", oxide_dir.resolve()),
        kv("status", status(True)),
    ]
    if env_file:
        env_path = Path(".env")
        if env_path.exists():
            lines.append(kv("env file", ".env already exists"))
        else:
            env_path.write_text("OPENAI_API_KEY=\nOXIDE_OPENAI_MODEL=gpt-4\n", encoding="utf-8")
            lines.append(kv("env file", "created .env with placeholders"))
    lines.extend(
        [
            "",
            "Next:",
            "  oxide run \"python --version\"",
            "  oxide timeline --since \"10 minutes ago\"",
            "  oxide ask \"what changed?\"",
        ]
    )
    click.echo(banner("OXIDE INIT", "PROJECT SETUP"))
    click.echo(panel("READY", lines))


@click.command(name="status", short_help="Summarize command count, failures, and recent changes.")
def status_cmd() -> None:
    """Show the current Oxide project status."""

    from oxide_ai.query_engine import (
        _exit_code,
        _file_change_history,
        _load_command_rows,
        _load_dotenv,
        _resolve_db_path,
    )

    _load_dotenv()
    db_path = _resolve_db_path(".oxide")
    click.echo(banner("OXIDE STATUS", "PROJECT SUMMARY"))
    if not db_path.exists():
        click.echo(panel("NOT INITIALIZED", ["No .oxide/oxide.db found.", "Run `oxide init` to start recording."]))
        return

    rows = _load_command_rows(db_path, limit=None, ascending=False)
    failures = [row for row in rows if (_exit_code(row["output_snapshot"]) or 0) != 0]
    changes = _file_change_history(rows)
    last = rows[0] if rows else None
    lines = [
        kv("database", db_path),
        kv("commands", len(rows)),
        kv("failures", len(failures)),
        kv("file changes", len(changes)),
        kv("openai key", status(bool(os.environ.get("OPENAI_API_KEY")))),
    ]
    if last:
        lines.extend(
            [
                "",
                "Last command:",
                command_line(last["id"], last["timestamp"], _exit_code(last["output_snapshot"]), _command_text(last["command"])),
            ]
        )
    click.echo(panel("SUMMARY", lines))

    if failures:
        click.echo(panel("RECENT FAILURES", [_failure_one_liner(row) for row in failures[:5]], border=BAD))
    if changes:
        click.echo(panel("RECENT FILE CHANGES", [_change_one_liner(change) for change in changes[:8]], border=GOOD))
    click.echo(tip("Try `oxide timeline --failures` or `oxide lineage <file>` next."))


cli.add_command(status_cmd)


@cli.command(short_help="Print a beginner-friendly command guide.")
def guide() -> None:
    """Print common Oxide workflows."""

    click.echo(banner("OXIDE GUIDE", "BEGINNER WORKFLOWS"))
    click.echo(
        panel(
            "1. START A PROJECT",
            [
                "oxide init",
                "oxide doctor",
                "oxide status",
            ],
        )
    )
    click.echo(
        panel(
            "2. RECORD NORMAL WORK",
            [
                "oxide run \"python -m pytest\"",
                "oxide run \"npm test\"",
                "oxide run \"echo 'version 1' > data.txt\"",
            ],
        )
    )
    click.echo(
        panel(
            "3. UNDERSTAND WHAT HAPPENED",
            [
                "oxide timeline --since \"1 hour ago\"",
                "oxide timeline --failures",
                "oxide lineage data.txt",
                "oxide graph",
                "oxide ask \"why did the last command fail?\"",
            ],
        )
    )
    click.echo(tip("Use `oxide reset --yes` only when you want to delete local history and rehearse from scratch."))


@cli.command(short_help="Check setup, database, shell, Python, and API key.")
def doctor() -> None:
    """Check whether Oxide is ready for a judge demo."""

    from oxide_ai.query_engine import _load_dotenv, _resolve_db_path

    _load_dotenv()
    db_path = _resolve_db_path(".oxide")
    bash_path = CommandRecorder._resolve_windows_git_bash(os.environ) if os.name == "nt" else None
    lines = [
        kv("cwd", Path.cwd()),
        kv("database", db_path),
        kv("db exists", f"{status(db_path.exists())} ({db_path.exists()})"),
        kv("openai key", status(bool(os.environ.get("OPENAI_API_KEY")))),
        kv("model", os.environ.get("OXIDE_OPENAI_MODEL", "gpt-4")),
        kv("msystem", os.environ.get("MSYSTEM", "not Git Bash")),
        kv("git bash", bash_path or "not needed / not detected"),
        kv("oxide shell", os.environ.get("OXIDE_SHELL", "auto")),
    ]
    click.echo(banner("OXIDE DOCTOR", "DEMO READINESS"))
    click.echo(panel("SYSTEM CHECK", lines))


@cli.command(short_help="Delete local .oxide history for a fresh start.")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
def reset(yes: bool) -> None:
    """Delete local recorded history from .oxide."""

    oxide_dir = Path(".oxide")
    if not oxide_dir.exists():
        click.echo(panel("RESET", ["No .oxide directory exists. Nothing to reset."]))
        return
    if not yes and not click.confirm("Delete local Oxide history in .oxide?"):
        click.echo(panel("RESET", ["Canceled."]))
        return
    shutil.rmtree(oxide_dir)
    click.echo(panel("RESET", ["Deleted .oxide history."]))


def _record_command(
    command_parts: list[str],
    *,
    db: str | None,
    cwd: str | None,
    timeout: float | None,
    shell: bool,
) -> None:
    command: str | list[str]
    if shell:
        if len(command_parts) == 1:
            command = command_parts[0]
        else:
            command = subprocess.list2cmdline(command_parts) if os.name == "nt" else shlex.join(command_parts)
    else:
        command = command_parts

    recorder = CommandRecorder(cwd=cwd, db_path=db)
    result = recorder.run(command, shell=shell, timeout=timeout)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    state = status_for_exit(result.exit_code)
    click.echo(
        f"[oxide] {state} run={result.run_id} exit={result.exit_code} hash={result.command_hash[:16]}",
        err=True,
    )
    raise click.exceptions.Exit(result.exit_code if result.exit_code is not None else 1)


def _timeline_details(row: Mapping[str, Any]) -> list[str]:
    output = row["output_snapshot"]
    exit_code = _safe_exit_code(output)
    lines = [
        tree_line("hash", row["command_hash"][:16]),
        tree_line("exit", exit_code),
    ]

    effects = _file_effect_lines(output)
    if effects:
        lines.extend(effects)
    else:
        lines.append(tree_line("writes", "none detected"))

    if exit_code not in (0, None):
        lines.append(tree_line("failure", _failure_reason(output)))
    elif exit_code is None:
        lines.append(tree_line("spawn", _failure_reason(output)))

    preview = _preview_output(output)
    if preview:
        lines.append(last_tree_line("output", preview))
    else:
        lines.append(last_tree_line("output", "no stdout/stderr"))
    return lines


def _lineage_lines(data: dict[str, object]) -> list[str]:
    history = data.get("history") or []
    if not isinstance(history, list) or not history:
        return [
            "No recorded command created or modified this file.",
            "Tip: run `oxide reset --yes`, rerun the demo commands, then try lineage again.",
        ]

    lines = [
        kv("file", data.get("file")),
        kv("events", len(history)),
    ]
    for item in history[-8:]:
        if not isinstance(item, dict):
            continue
        lines.append("")
        lines.append(command_line(item["run_id"], item["timestamp"], item["exit_code"], item["command"]))
        lines.append(tree_line("change", item.get("change_type")))
        lines.append(tree_line("old hash", _short_hash(item.get("hash_before"))))
        lines.append(last_tree_line("new hash", _short_hash(item.get("hash_after"))))
    return lines


def _short_hash(value: object) -> str:
    if not value:
        return "-"
    return str(value)[:16]


def _safe_exit_code(output: Mapping[str, Any]) -> int | None:
    value = output.get("exit_code")
    if value is None or isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _file_effect_lines(output: Mapping[str, Any]) -> list[str]:
    created = sorted((output.get("new_files") or {}).keys())
    changed = sorted((output.get("changed_files") or {}).keys())
    deleted = sorted(output.get("deleted_files") or [])
    lines: list[str] = []
    if created:
        lines.append(tree_line("created", _join_paths(created)))
    if changed:
        lines.append(tree_line("modified", _join_paths(changed)))
    if deleted:
        lines.append(tree_line("deleted", _join_paths(deleted)))
    return lines


def _join_paths(paths: list[str], limit: int = 5) -> str:
    shown = paths[:limit]
    suffix = "" if len(paths) <= limit else f" (+{len(paths) - limit} more)"
    return ", ".join(shown) + suffix


def _preview_output(output: Mapping[str, Any]) -> str:
    stdout = str(output.get("stdout") or "").strip()
    stderr = str(output.get("stderr") or "").strip()
    text = stdout if stdout else stderr
    if stdout and stderr:
        text = stdout + " | stderr: " + stderr
    text = " | ".join(line.strip() for line in text.splitlines() if line.strip())
    return text[:260] + ("..." if len(text) > 260 else "")


def _failure_reason(output: Mapping[str, Any]) -> str:
    spawn_error = output.get("spawn_error")
    if isinstance(spawn_error, dict) and spawn_error.get("message"):
        return str(spawn_error["message"])

    stderr = str(output.get("stderr") or "").strip()
    if not stderr:
        return "command exited non-zero without stderr"

    location = _last_python_location(stderr)
    message = _last_error_message(stderr)
    if location and message:
        return f"{location}: {message}"
    if message:
        return message
    return "stderr captured, but no concise error line was detected"


def _last_python_location(stderr: str) -> str | None:
    matches = re.findall(r'File "([^"]+)", line (\d+)', stderr)
    if not matches:
        return None
    file_name, line_number = matches[-1]
    if file_name == "<string>":
        return f"inline Python line {line_number}"
    return f"{file_name}:{line_number}"


def _last_error_message(stderr: str) -> str | None:
    ignored_prefixes = ("Traceback", "File ", "^", "~")
    for line in reversed([part.strip() for part in stderr.splitlines() if part.strip()]):
        if line.startswith(ignored_prefixes):
            continue
        return line
    return None


def _failure_one_liner(row: Mapping[str, Any]) -> str:
    command = _command_text(row["command"])
    return f"[{row['id']}] {row['timestamp']}  {command}  -> {_failure_reason(row['output_snapshot'])}"


def _change_one_liner(change: Mapping[str, Any]) -> str:
    old_hash = _short_hash(change.get("old_hash"))
    new_hash = _short_hash(change.get("new_hash"))
    return (
        f"{change.get('timestamp')}  {change.get('change_type')} "
        f"{change.get('file')}  {old_hash} -> {new_hash}"
    )


def _command_text(command: object) -> str:
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    try:
        cli.main(args=args, standalone_mode=False)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
