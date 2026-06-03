"""Command line entry point for Oxide."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys

from oxide_daemon.recorder import CommandRecorder


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record shell commands with Oxide.")
    parser.add_argument("--db", help="Path to the SQLite database.")
    parser.add_argument("--cwd", help="Working directory to snapshot and execute in.")
    parser.add_argument("--timeout", type=float, help="Command timeout in seconds.")
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Run the command through the platform shell.",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command after --.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command_parts = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command_parts:
        parser.error("provide a command, usually after --")

    command: str | list[str]
    if args.shell:
        command = subprocess.list2cmdline(command_parts) if os.name == "nt" else shlex.join(command_parts)
    else:
        command = command_parts

    recorder = CommandRecorder(cwd=args.cwd, db_path=args.db)
    result = recorder.run(command, shell=args.shell, timeout=args.timeout)
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    print(
        f"oxide recorded run_id={result.run_id} command_hash={result.command_hash}",
        file=sys.stderr,
    )
    return result.exit_code if result.exit_code is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
