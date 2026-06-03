"""Natural language query engine for recorded Oxide command history."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterable, Mapping

from .prompts import SYSTEM_PROMPT


MAX_OUTPUT_PREVIEW = 500
MAX_RECENT_COMMANDS = 50
MAX_CONTEXT_CHARS = 24000
MAX_FILE_CHANGES = 200
MAX_GRAPH_READS_PER_COMMAND = 40
DEFAULT_MODEL = "gpt-4"


class QueryEngineError(RuntimeError):
    """Raised when Oxide cannot query command history."""


def answer_question(question: str, oxide_dir: str = ".oxide") -> str:
    """Answer a natural language question using SQLite history and OpenAI."""

    _load_dotenv()
    db_path = _resolve_db_path(oxide_dir)
    rows = _load_command_rows(db_path, limit=MAX_RECENT_COMMANDS)
    context = _build_context(rows)

    try:
        return _call_openai(question, context)
    except Exception as exc:
        return _fallback_answer(question, db_path, error=exc)


def _get_file_lineage(filepath: str, oxide_dir: str = ".oxide") -> dict[str, Any]:
    """Trace which commands created or modified a file and their inputs."""

    db_path = _resolve_db_path(oxide_dir)
    rows = _load_command_rows(db_path, limit=None, ascending=True)
    normalized = _normalize_file_key(filepath)
    history: list[dict[str, Any]] = []

    for row in rows:
        input_files = _snapshot_files(row["input_snapshot"])
        output = row["output_snapshot"]
        writes = _written_files(output)
        deletes = set(output.get("deleted_files") or [])
        change_type: str | None = None
        after_meta: dict[str, Any] | None = None

        if normalized in writes["created"]:
            change_type = "created"
            after_meta = writes["created"][normalized]
        elif normalized in writes["modified"]:
            change_type = "modified"
            after_meta = writes["modified"][normalized]
        elif normalized in deletes:
            change_type = "deleted"

        if change_type is None:
            continue

        input_candidates = [
            {"file": path, "hash": meta.get("hash"), "size": meta.get("size")}
            for path, meta in input_files.items()
            if path != normalized
        ]
        command_imports = (
            row["deps"]
            .get("command_imports", {})
            .get("modules", {})
        )
        history.append(
            {
                "run_id": row["id"],
                "command_hash": row["command_hash"],
                "command": row["command"],
                "timestamp": row["timestamp"],
                "exit_code": _exit_code(output),
                "change_type": change_type,
                "hash_before": input_files.get(normalized, {}).get("hash"),
                "hash_after": (after_meta or {}).get("hash"),
                "input_count": len(input_candidates),
                "inputs": input_candidates[:100],
                "python_imports": sorted(command_imports)[:100],
                "output_preview": _output_preview(output),
            }
        )

    created_by = next((item for item in history if item["change_type"] == "created"), None)
    last_modified_by = next(
        (
            item
            for item in reversed(history)
            if item["change_type"] in {"created", "modified"}
        ),
        None,
    )
    return {
        "file": normalized,
        "db_path": str(db_path),
        "created_by": created_by,
        "last_modified_by": last_modified_by,
        "history": history,
        "note": (
            "Inputs are derived from the pre-command file snapshot because the "
            "current recorder stores snapshots, not syscall-level read events."
        ),
    }


def _compare_time_ranges(
    time1: str,
    time2: str,
    oxide_dir: str = ".oxide",
) -> dict[str, Any]:
    """Compare known workspace state and command history at two timestamps."""

    db_path = _resolve_db_path(oxide_dir)
    rows = _load_command_rows(db_path, limit=None, ascending=True)
    first_time = _parse_time_reference(time1)
    second_time = _parse_time_reference(time2)
    first_state = _snapshot_at(rows, first_time)
    second_state = _snapshot_at(rows, second_time)
    changed_files = _compare_snapshots(first_state["files"], second_state["files"])
    commands_between = [
        _command_summary(row)
        for row in rows
        if first_time <= _parse_timestamp(row["timestamp"]) <= second_time
    ]
    if second_time < first_time:
        commands_between = [
            _command_summary(row)
            for row in rows
            if second_time <= _parse_timestamp(row["timestamp"]) <= first_time
        ]

    return {
        "db_path": str(db_path),
        "time1": first_time.isoformat(),
        "time2": second_time.isoformat(),
        "state_at_time1": {
            "source": first_state["source"],
            "file_count": len(first_state["files"]),
        },
        "state_at_time2": {
            "source": second_state["source"],
            "file_count": len(second_state["files"]),
        },
        "different_files": changed_files,
        "different_commands": commands_between,
    }


class QueryEngine:
    """Entry point for natural language queries over recorded runs."""

    def __init__(self, oxide_dir: str = ".oxide") -> None:
        self.oxide_dir = oxide_dir

    def query(self, question: str) -> str:
        return answer_question(question, self.oxide_dir)


def _resolve_db_path(oxide_dir: str | Path) -> Path:
    base = Path(oxide_dir).expanduser()
    candidates = [base] if base.suffix else [base / "oxide.db", base / "oxide.sqlite3"]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    if candidates:
        return candidates[0].resolve()
    raise QueryEngineError("no database path was provided")


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise QueryEngineError(f"Oxide database not found at {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _load_command_rows(
    db_path: Path,
    *,
    limit: int | None,
    ascending: bool = False,
) -> list[dict[str, Any]]:
    order = "ASC" if ascending else "DESC"
    query = (
        "SELECT id, command_hash, command, input_snapshot, output_snapshot, "
        f"timestamp, deps FROM command_runs ORDER BY timestamp {order}, id {order}"
    )
    params: tuple[Any, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)

    try:
        with _connect(db_path) as conn:
            records = conn.execute(query, params).fetchall()
    except sqlite3.Error as exc:
        raise QueryEngineError(f"failed to read Oxide database: {exc}") from exc

    rows = [_decode_row(record) for record in records]
    return rows


def _decode_row(record: sqlite3.Row) -> dict[str, Any]:
    output_snapshot = _loads_json(record["output_snapshot"], {})
    return {
        "id": record["id"],
        "command_hash": record["command_hash"],
        "command": _loads_json(record["command"], record["command"]),
        "input_snapshot": _loads_json(record["input_snapshot"], {}),
        "output_snapshot": output_snapshot,
        "timestamp": record["timestamp"],
        "deps": _loads_json(record["deps"], {}),
    }


def _build_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    recent_commands = [_command_summary(row) for row in rows]
    file_history = _file_change_history(rows)
    recent_cutoff = now - timedelta(hours=24)
    recent_file_changes = [
        change
        for change in file_history
        if _parse_timestamp(change["timestamp"]) >= recent_cutoff
    ][:MAX_FILE_CHANGES]
    failures = [
        command
        for command in recent_commands
        if command["exit_code"] is not None and command["exit_code"] != 0
    ]
    command_graph = [_command_graph_entry(row) for row in rows]

    return {
        "recent_commands": recent_commands,
        "file_changes": recent_file_changes,
        "failures": failures,
        "command_graph": command_graph,
    }


def _command_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    output = row["output_snapshot"]
    return {
        "run_id": row["id"],
        "command_hash": row["command_hash"],
        "command": row["command"],
        "timestamp": row["timestamp"],
        "exit_code": _exit_code(output),
        "output_preview": _output_preview(output),
    }


def _command_graph_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    input_files = _snapshot_files(row["input_snapshot"])
    output = row["output_snapshot"]
    writes = _written_files(output)
    reads = sorted(input_files)
    return {
        "run_id": row["id"],
        "command_hash": row["command_hash"],
        "timestamp": row["timestamp"],
        "command": row["command"],
        "read_count": len(reads),
        "reads": reads[:MAX_GRAPH_READS_PER_COMMAND],
        "writes": sorted(set(writes["created"]) | set(writes["modified"])),
        "deletes": sorted(output.get("deleted_files") or []),
    }


def _file_change_history(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for row in rows:
        input_files = _snapshot_files(row["input_snapshot"])
        output = row["output_snapshot"]
        writes = _written_files(output)
        for path, metadata in writes["created"].items():
            changes.append(
                _file_change_entry(row, path, "created", None, metadata.get("hash"))
            )
        for path, metadata in writes["modified"].items():
            changes.append(
                _file_change_entry(
                    row,
                    path,
                    "modified",
                    input_files.get(path, {}).get("hash"),
                    metadata.get("hash"),
                )
            )
        for path in output.get("deleted_files") or []:
            changes.append(
                _file_change_entry(
                    row,
                    path,
                    "deleted",
                    input_files.get(path, {}).get("hash"),
                    None,
                )
            )
    return sorted(changes, key=lambda item: item["timestamp"], reverse=True)


def _file_change_entry(
    row: Mapping[str, Any],
    path: str,
    change_type: str,
    old_hash: str | None,
    new_hash: str | None,
) -> dict[str, Any]:
    return {
        "file": path,
        "change_type": change_type,
        "timestamp": row["timestamp"],
        "command_hash": row["command_hash"],
        "command": row["command"],
        "old_hash": old_hash,
        "new_hash": new_hash,
    }


def _written_files(output_snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "created": dict(output_snapshot.get("new_files") or {}),
        "modified": dict(output_snapshot.get("changed_files") or {}),
    }


def _snapshot_files(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    files = snapshot.get("files") or {}
    return files if isinstance(files, dict) else {}


def _output_preview(output_snapshot: Mapping[str, Any]) -> str:
    stdout = output_snapshot.get("stdout") or ""
    stderr = output_snapshot.get("stderr") or ""
    combined = stdout if stdout else stderr
    if stdout and stderr:
        combined = stdout + "\nSTDERR:\n" + stderr
    return _truncate(combined.strip(), MAX_OUTPUT_PREVIEW)


def _exit_code(output_snapshot: Mapping[str, Any]) -> int | None:
    value = output_snapshot.get("exit_code")
    return value if isinstance(value, int) or value is None else int(value)


def _call_openai(question: str, context: Mapping[str, Any]) -> str:
    _load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise QueryEngineError("OPENAI_API_KEY is not set")

    context_json = _bounded_json(context, MAX_CONTEXT_CHARS)
    model = os.environ.get("OXIDE_OPENAI_MODEL", DEFAULT_MODEL)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Question:\n"
                f"{question}\n\n"
                "Oxide context JSON:\n"
                f"{context_json}"
            ),
        },
    ]

    last_error: Exception | None = None
    for attempt in range(4):
        try:
            return _chat_completion(api_key=api_key, model=model, messages=messages)
        except Exception as exc:
            last_error = exc
            if attempt == 3:
                break
            sleep_for = min(8.0, 0.75 * (2**attempt))
            time.sleep(sleep_for)

    raise QueryEngineError(f"OpenAI request failed: {last_error}") from last_error


def _chat_completion(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
) -> str:
    try:
        import openai
    except ImportError as exc:
        raise QueryEngineError("openai package is not installed") from exc

    if hasattr(openai, "OpenAI"):
        client = openai.OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_tokens=900,
        )
        content = response.choices[0].message.content
        return (content or "").strip()

    if hasattr(openai, "ChatCompletion"):
        openai.api_key = api_key
        response = openai.ChatCompletion.create(
            model=model,
            messages=messages,
            temperature=0.1,
            max_tokens=900,
        )
        content = response["choices"][0]["message"]["content"]
        return str(content).strip()

    raise QueryEngineError("openai package does not expose ChatCompletion")


def _fallback_answer(question: str, db_path: Path, *, error: Exception | None = None) -> str:
    try:
        rows = _load_command_rows(db_path, limit=MAX_RECENT_COMMANDS)
    except Exception as exc:
        return f"I could not read Oxide history: {exc}"

    lower = question.lower()
    prefix = (
        "OpenAI was unavailable, so I answered from SQLite directly"
        + (f" ({error})." if error else ".")
    )

    if "today" in lower and "command" in lower:
        today = datetime.now(timezone.utc).date()
        todays = [
            row
            for row in rows
            if _parse_timestamp(row["timestamp"]).date() == today
        ]
        return prefix + "\n\n" + _format_command_list(todays, "Commands today")

    if "fail" in lower or "error" in lower:
        failures = [
            row
            for row in rows
            if (_exit_code(row["output_snapshot"]) or 0) != 0
        ]
        return prefix + "\n\n" + _format_command_list(failures, "Recent failures")

    file_paths = _extract_file_paths(question)
    if file_paths:
        lineage = _get_file_lineage(file_paths[0], str(db_path))
        return prefix + "\n\n" + _format_lineage(lineage)

    return prefix + "\n\n" + _format_command_list(rows[:10], "Recent commands")


def _format_command_list(rows: list[Mapping[str, Any]], title: str) -> str:
    if not rows:
        return f"{title}: no matching commands found."
    lines = [f"{title}:"]
    for row in rows[:10]:
        command = row["command"]
        if isinstance(command, list):
            command = " ".join(str(part) for part in command)
        lines.append(
            f"- {row['timestamp']} exit={_exit_code(row['output_snapshot'])} "
            f"hash={row['command_hash'][:12]} command={command}"
        )
    return "\n".join(lines)


def _format_lineage(lineage: Mapping[str, Any]) -> str:
    history = lineage.get("history") or []
    if not history:
        return f"No recorded command created or modified {lineage.get('file')}."
    lines = [f"Lineage for {lineage.get('file')}:"]
    for item in history[-10:]:
        lines.append(
            f"- {item['timestamp']} {item['change_type']} by "
            f"{item['command_hash'][:12]} exit={item['exit_code']}"
        )
    return "\n".join(lines)


def _snapshot_at(
    rows: list[Mapping[str, Any]],
    timestamp: datetime,
) -> dict[str, Any]:
    state: dict[str, dict[str, Any]] = {}
    source = "no recorded commands before timestamp"

    for row in rows:
        row_time = _parse_timestamp(row["timestamp"])
        input_files = _snapshot_files(row["input_snapshot"])
        if not state:
            state = dict(input_files)
            source = f"input snapshot before run {row['id']}"
        if row_time > timestamp:
            break
        output = row["output_snapshot"]
        writes = _written_files(output)
        state.update(writes["created"])
        state.update(writes["modified"])
        for path in output.get("deleted_files") or []:
            state.pop(path, None)
        source = f"state after run {row['id']}"

    return {"files": state, "source": source}


def _compare_snapshots(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    created = [
        {"file": path, "new_hash": after[path].get("hash")}
        for path in sorted(set(after) - set(before))
    ]
    deleted = [
        {"file": path, "old_hash": before[path].get("hash")}
        for path in sorted(set(before) - set(after))
    ]
    modified = [
        {
            "file": path,
            "old_hash": before[path].get("hash"),
            "new_hash": after[path].get("hash"),
        }
        for path in sorted(set(before) & set(after))
        if before[path].get("hash") != after[path].get("hash")
    ]
    return {"created": created, "modified": modified, "deleted": deleted}


def _parse_time_reference(value: str) -> datetime:
    try:
        import dateparser

        parsed = dateparser.parse(value)
    except Exception:
        parsed = None

    if parsed is None:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.astimezone(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _loads_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _load_dotenv(paths: Iterable[Path] | None = None) -> None:
    candidates = list(paths or [Path.cwd() / ".env", Path.cwd() / ".oxide" / ".env"])
    for path in candidates:
        if not path.exists():
            continue
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


def _bounded_json(value: Mapping[str, Any], max_chars: int) -> str:
    text = json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2)
    if len(text) <= max_chars:
        return text

    compact = dict(value)
    compact["command_graph"] = [
        {
            **entry,
            "reads": entry.get("reads", [])[:10],
            "read_count": entry.get("read_count", 0),
        }
        for entry in compact.get("command_graph", [])
    ]
    compact["file_changes"] = compact.get("file_changes", [])[:75]
    text = json.dumps(compact, ensure_ascii=True, sort_keys=True, indent=2)
    return _truncate(text, max_chars)


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 15] + "...[truncated]"


def _extract_file_paths(question: str) -> list[str]:
    pattern = r"[\w./\\-]+\.[A-Za-z0-9_]+"
    return [_normalize_file_key(match) for match in re.findall(pattern, question)]


def _normalize_file_key(filepath: str) -> str:
    return filepath.replace("\\", "/").lstrip("./")
