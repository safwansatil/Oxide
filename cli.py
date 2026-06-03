"""Command line entry point for Oxide."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

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


@cli.command()
@click.argument("question")
def ask(question: str) -> None:
    """Ask natural language about your command history."""

    click.echo("🤔 Analyzing your execution history...")
    try:
        from oxide_ai.query_engine import answer_question

        answer = answer_question(question)
        click.echo(f"\n💡 {answer}\n")
    except Exception as exc:
        click.echo(f"❌ Error: {exc}", err=True)
        click.echo("Fallback: Try 'oxide history --help'")


@cli.command()
@click.argument("filepath")
def lineage(filepath: str) -> None:
    """Show complete lineage of how a file was created."""

    from oxide_ai.query_engine import _get_file_lineage

    click.echo(json.dumps(_get_file_lineage(filepath), indent=2))


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
    for row in rows:
        timestamp = _parse_timestamp(row["timestamp"])
        if not start <= timestamp <= end:
            continue
        command = row["command"]
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        click.echo(
            f"{row['timestamp']} exit={_exit_code(row['output_snapshot'])} "
            f"{row['command_hash'][:12]} {command}"
        )
        preview = _output_preview(row["output_snapshot"])
        if preview:
            click.echo(f"  {preview}")


@cli.command()
def graph() -> None:
    """Print ASCII graph of recent execution."""

    from oxide_ai.graph_analyzer import ExecutionGraph

    execution_graph = ExecutionGraph(".oxide/oxide.db")
    click.echo(execution_graph.ascii_recent(limit=10))


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
        command = subprocess.list2cmdline(command_parts) if os.name == "nt" else shlex.join(command_parts)
    else:
        command = command_parts

    recorder = CommandRecorder(cwd=cwd, db_path=db)
    result = recorder.run(command, shell=shell, timeout=timeout)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    click.echo(
        f"oxide recorded run_id={result.run_id} command_hash={result.command_hash}",
        err=True,
    )
    raise click.exceptions.Exit(result.exit_code if result.exit_code is not None else 1)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    try:
        cli.main(args=args, standalone_mode=False)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
