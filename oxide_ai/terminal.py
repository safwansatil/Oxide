"""Terminal rendering helpers for the Oxide CLI."""

from __future__ import annotations

import os
import re
import shutil
from typing import Iterable

import click


MIN_WIDTH = 64
MAX_WIDTH = 100
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

BORDER = "bright_blue"
TITLE = "bright_cyan"
MUTED = "bright_black"
GOOD = "green"
BAD = "red"
WARN = "yellow"
ACCENT = "magenta"


def terminal_width() -> int:
    width = shutil.get_terminal_size((84, 24)).columns
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


def color(text: object, fg: str | None = None, *, bold: bool = False) -> str:
    value = str(text)
    if os.environ.get("NO_COLOR") or os.environ.get("OXIDE_NO_COLOR"):
        return value
    return click.style(value, fg=fg, bold=bold)


def banner(title: str = "OXIDE", subtitle: str = "FORENSIC COMMAND TRACE") -> str:
    width = terminal_width()
    rule = "=" * (width - 2)
    title_text = f" {title} :: {subtitle} "
    return "\n".join(
        [
            color(f"+{rule}+", BORDER, bold=True),
            color("|", BORDER, bold=True)
            + color(title_text.center(width - 2), TITLE, bold=True)
            + color("|", BORDER, bold=True),
            color(f"+{rule}+", BORDER, bold=True),
        ]
    )


def panel(
    title: str,
    lines: Iterable[object],
    *,
    width: int | None = None,
    border: str = BORDER,
) -> str:
    target_width = width or terminal_width()
    body_width = target_width - 4
    rendered: list[str] = []
    rendered.append(color("+" + "-" * (target_width - 2) + "+", border))
    rendered.append(
        color("| ", border)
        + _pad(color(title[:body_width], TITLE, bold=True), body_width)
        + color(" |", border)
    )
    rendered.append(color("+" + "-" * (target_width - 2) + "+", border))
    for line in lines:
        for wrapped in _wrap(str(line), body_width):
            rendered.append(color("| ", border) + _pad(wrapped, body_width) + color(" |", border))
    rendered.append(color("+" + "-" * (target_width - 2) + "+", border))
    return "\n".join(rendered)


def section(title: str) -> str:
    return color(f"\n-- {title} " + "-" * max(8, terminal_width() - len(title) - 5), ACCENT, bold=True)


def tip(text: str) -> str:
    return color("tip", WARN, bold=True) + color("  " + text, MUTED)


def kv(label: str, value: object) -> str:
    return f"{label:<16} {value}"


def status(ok: bool | None) -> str:
    if ok is True:
        return color("OK", GOOD, bold=True)
    if ok is False:
        return color("FAIL", BAD, bold=True)
    return color("WARN", WARN, bold=True)


def status_for_exit(exit_code: int | None) -> str:
    if exit_code == 0:
        return color("OK", GOOD, bold=True)
    if exit_code is None:
        return color("SPAWN", WARN, bold=True)
    return color("FAIL", BAD, bold=True)


def command_line(run_id: int, timestamp: str, exit_code: int | None, command: object) -> str:
    state = status_for_exit(exit_code)
    return f"[{run_id:>3}] {state} {color(timestamp, MUTED)}  {command}"


def tree_line(label: str, value: object = "") -> str:
    suffix = f" {value}" if value != "" else ""
    return color("  |-- ", MUTED) + color(label, "cyan") + suffix


def last_tree_line(label: str, value: object = "") -> str:
    suffix = f" {value}" if value != "" else ""
    return color("  `-- ", MUTED) + color(label, "cyan") + suffix


def bullet(label: str, value: object = "") -> str:
    suffix = f" {value}" if value != "" else ""
    return color("  * ", MUTED) + color(label, "cyan") + suffix


def _wrap(value: str, width: int) -> list[str]:
    text = str(value)
    if not text:
        return [""]
    if ANSI_RE.search(text):
        if _visible_len(text) <= width:
            return [text]
        text = ANSI_RE.sub("", text)
    chunks: list[str] = []
    remaining = text
    while len(remaining) > width:
        split_at = remaining.rfind(" ", 0, width + 1)
        if split_at <= 0:
            split_at = width
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    chunks.append(remaining)
    return chunks


def _visible_len(value: str) -> int:
    return len(ANSI_RE.sub("", value))


def _pad(value: str, width: int) -> str:
    padding = max(0, width - _visible_len(value))
    return value + " " * padding
