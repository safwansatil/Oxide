"""ASCII terminal rendering helpers for the Oxide CLI."""

from __future__ import annotations

import shutil
from typing import Iterable


MIN_WIDTH = 58
MAX_WIDTH = 92


def terminal_width() -> int:
    width = shutil.get_terminal_size((78, 24)).columns
    return max(MIN_WIDTH, min(MAX_WIDTH, width))


def banner(title: str = "OXIDE", subtitle: str = "FORENSIC COMMAND TRACE") -> str:
    width = terminal_width()
    rule = "=" * (width - 2)
    title_text = f" {title} :: {subtitle} "
    return "\n".join(
        [
            f"+{rule}+",
            "|" + title_text.center(width - 2) + "|",
            f"+{rule}+",
        ]
    )


def panel(title: str, lines: Iterable[str], *, width: int | None = None) -> str:
    target_width = width or terminal_width()
    body_width = target_width - 4
    rendered: list[str] = []
    rendered.append("+" + "-" * (target_width - 2) + "+")
    rendered.append("| " + title[:body_width].ljust(body_width) + " |")
    rendered.append("+" + "-" * (target_width - 2) + "+")
    for line in lines:
        for wrapped in _wrap(line, body_width):
            rendered.append("| " + wrapped.ljust(body_width) + " |")
    rendered.append("+" + "-" * (target_width - 2) + "+")
    return "\n".join(rendered)


def kv(label: str, value: object) -> str:
    return f"{label:<14} {value}"


def status(ok: bool | None) -> str:
    if ok is True:
        return "OK"
    if ok is False:
        return "FAIL"
    return "WARN"


def command_line(run_id: int, timestamp: str, exit_code: int | None, command: object) -> str:
    state = "OK" if exit_code == 0 else "FAIL"
    return f"[{run_id:>3}] {state:<4} {timestamp}  {command}"


def tree_line(label: str, value: object = "") -> str:
    suffix = f" {value}" if value != "" else ""
    return f"  |-- {label}{suffix}"


def last_tree_line(label: str, value: object = "") -> str:
    suffix = f" {value}" if value != "" else ""
    return f"  `-- {label}{suffix}"


def _wrap(value: str, width: int) -> list[str]:
    text = str(value)
    if not text:
        return [""]
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
