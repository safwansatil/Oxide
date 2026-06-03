"""Command line entry point for Oxide."""

from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from oxide_ai.terminal import banner, command_line, kv, last_tree_line, panel, status, tree_line
from oxide_daemon.recorder import CommandRecorder


@click.group(
    invoke_without_command=True,
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
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
        click.echo(ctx.get_help())
        return
    _record_command(command_parts, db=db, cwd=cwd, timeout=timeout, shell=shell)


@cli.command(
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
    """Record a command execution."""

    command_parts = list(command)
    if command_parts[:1] == ["--"]:
        command_parts = command_parts[1:]
    _record_command(command_parts, db=db, cwd=cwd, timeout=timeout, shell=shell)


@cli.command(
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
    """Run and record a shell command."""

    command_parts = list(command)
    if command_parts[:1] == ["--"]:
        command_parts = command_parts[1:]
    _record_command(command_parts, db=db, cwd=cwd, timeout=timeout, shell=not no_shell)


@cli.command()
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


@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Print raw JSON lineage.")
@click.argument("filepath")
def lineage(filepath: str, as_json: bool) -> None:
    """Show complete lineage of how a file was created."""

    from oxide_ai.query_engine import _get_file_lineage

    data = _get_file_lineage(filepath)
    if as_json:
        click.echo(json.dumps(data, indent=2))
        return
    click.echo(banner("OXIDE LINEAGE", data["file"]))
    click.echo(panel("FILE HISTORY", _lineage_lines(data)))


@cli.command()
@click.option("--since", help='Start time like "2 hours ago".')
@click.option("--until", help="End time.")
def timeline(since: str | None, until: str | None) -> None:
    """Show command timeline with natural language time parsing."""

    from oxide_ai.query_engine import (
        _exit_code,
        _load_command_rows,
        _output_preview,
        _parse_time_reference,
        _parse_timestamp,
        _resolve_db_path,
    )

    db_path = _resolve_db_path(".oxide")
    rows = _load_command_rows(db_path, limit=None, ascending=True)
    start = _parse_time_reference(since) if since else datetime.min.replace(tzinfo=timezone.utc)
    end = _parse_time_reference(until) if until else datetime.max.replace(tzinfo=timezone.utc)
    click.echo(banner("OXIDE TIMELINE", "RECORDED COMMANDS"))
    emitted = 0
    for row in rows:
        timestamp = _parse_timestamp(row["timestamp"])
        if not start <= timestamp <= end:
            continue
        command = row["command"]
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        emitted += 1
        click.echo(command_line(row["id"], row["timestamp"], _exit_code(row["output_snapshot"]), command))
        click.echo(tree_line("hash", row["command_hash"][:16]))
        preview = _output_preview(row["output_snapshot"])
        if preview:
            click.echo(last_tree_line("output", preview.replace("\n", " | ")))
    if emitted == 0:
        click.echo(panel("NO MATCHES", ["No commands were recorded in that time range."]))


@cli.command()
def graph() -> None:
    """Print ASCII graph of recent execution."""

    from oxide_ai.graph_analyzer import ExecutionGraph

    execution_graph = ExecutionGraph(".oxide/oxide.db")
    click.echo(banner("OXIDE GRAPH", "COMMAND -> FILE EFFECTS"))
    click.echo(execution_graph.ascii_recent(limit=10))


@cli.command()
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


@cli.command()
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
    state = "OK" if result.exit_code == 0 else "FAIL"
    click.echo(f"[oxide] {state} run={result.run_id} exit={result.exit_code} hash={result.command_hash[:16]}", err=True)
    raise click.exceptions.Exit(result.exit_code if result.exit_code is not None else 1)


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


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    try:
        cli.main(args=args, standalone_mode=False)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
