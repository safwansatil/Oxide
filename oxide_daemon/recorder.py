"""Command recorder for Oxide.

The recorder wraps shell commands, snapshots workspace content with blake2b,
captures process output, and stores the resulting execution record in SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

from .storage import SQLiteCommandStore


DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".oxide",
        ".pytest_cache",
        ".ruff_cache",
        ".svn",
        ".tox",
        ".venv",
        "__pycache__",
        "node_modules",
        "venv",
    }
)

READ_CHUNK_SIZE = 1024 * 1024

IMPORT_TRACKER = r'''
import atexit
import hashlib
import json
import os
from pathlib import Path
import sys


def _hash_file(path):
    hasher = hashlib.blake2b(digest_size=32)
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _module_meta(module):
    spec = getattr(module, "__spec__", None)
    origin = getattr(spec, "origin", None) if spec is not None else None
    file_name = getattr(module, "__file__", None)
    meta = {"origin": origin, "file": file_name}
    if file_name:
        try:
            path = Path(file_name).resolve()
            if path.is_file():
                stat_result = path.stat()
                meta.update(
                    {
                        "path": str(path),
                        "hash": _hash_file(path),
                        "size": stat_result.st_size,
                        "mtime_ns": stat_result.st_mtime_ns,
                    }
                )
        except Exception as exc:
            meta["error"] = f"{type(exc).__name__}: {exc}"
    return meta


def _flush_imports():
    log_path = os.environ.get("OXIDE_IMPORT_LOG")
    if not log_path:
        return

    payload = {"pid": os.getpid(), "modules": {}, "errors": []}
    for name, module in list(sys.modules.items()):
        if not name or module is None:
            continue
        try:
            payload["modules"][name] = _module_meta(module)
        except Exception as exc:
            payload["errors"].append(
                {"module": name, "error": f"{type(exc).__name__}: {exc}"}
            )

    try:
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
    except Exception:
        pass


atexit.register(_flush_imports)
'''


class RecorderError(RuntimeError):
    """Raised when the recorder fails outside normal command execution."""


@dataclass(frozen=True)
class CommandResult:
    """Result returned after a command is recorded."""

    run_id: int
    command_hash: str
    command: str | list[str]
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    input_snapshot: dict[str, Any]
    output_snapshot: dict[str, Any]
    timestamp: str
    deps: dict[str, Any]
    timed_out: bool = False
    spawn_error: dict[str, str] | None = None

    def check_returncode(self) -> None:
        """Raise a subprocess-compatible exception if the command failed."""

        if self.timed_out:
            raise subprocess.TimeoutExpired(
                self.command,
                self.output_snapshot.get("timeout"),
                output=self.stdout,
                stderr=self.stderr,
            )
        if self.exit_code not in (0, None):
            raise subprocess.CalledProcessError(
                self.exit_code,
                self.command,
                output=self.stdout,
                stderr=self.stderr,
            )
        if self.spawn_error:
            raise RecorderError(self.spawn_error["message"])


class CommandRecorder:
    """Record shell command executions and their workspace effects."""

    def __init__(
        self,
        *,
        cwd: str | Path | None = None,
        db_path: str | Path | None = None,
        excluded_dirs: set[str] | frozenset[str] | None = None,
        digest_size: int = 32,
    ) -> None:
        self.cwd = Path(cwd or os.getcwd()).expanduser().resolve()
        self.digest_size = digest_size
        self.excluded_dirs = set(DEFAULT_EXCLUDED_DIRS if excluded_dirs is None else excluded_dirs)
        self.db_path = Path(db_path or self.cwd / ".oxide" / "oxide.db").expanduser().resolve()
        self._db_related_paths = {
            self.db_path,
            self.db_path.with_name(self.db_path.name + "-journal"),
            self.db_path.with_name(self.db_path.name + "-shm"),
            self.db_path.with_name(self.db_path.name + "-wal"),
        }
        self.store = SQLiteCommandStore(self.db_path)

    def run(
        self,
        command: str | Sequence[str],
        *,
        cwd: str | Path | None = None,
        shell: bool | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        check: bool = False,
    ) -> CommandResult:
        """Run, snapshot, diff, and persist a command execution."""

        workdir = Path(cwd or self.cwd).expanduser().resolve()
        use_shell = isinstance(command, str) if shell is None else shell
        normalized_command = self._normalize_command(command)
        timestamp = _utc_now()

        input_files = self.capture_file_snapshot(workdir)
        imported_modules = self.capture_imported_modules()
        input_snapshot = {
            "cwd": str(workdir),
            "captured_at": input_files["captured_at"],
            "files": input_files["files"],
            "errors": input_files["errors"],
            "imported_modules": imported_modules,
        }

        command_hash = self._hash_json(
            {
                "command": normalized_command,
                "cwd": str(workdir),
                "shell": use_shell,
            }
        )

        stdout = ""
        stderr = ""
        exit_code: int | None = None
        timed_out = False
        spawn_error: dict[str, str] | None = None
        import_log: Path | None = None

        with tempfile.TemporaryDirectory(prefix="oxide-imports-") as tracker_dir:
            try:
                command_env, import_log = self._build_command_env(env, Path(tracker_dir))
                process = self._start_process(
                    command,
                    cwd=workdir,
                    shell=use_shell,
                    env=command_env,
                )
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._terminate_process(process)
                    stdout, stderr = process.communicate()
                exit_code = process.returncode
            except Exception as exc:
                spawn_error = {
                    "type": type(exc).__name__,
                    "message": f"{type(exc).__name__}: {exc}",
                }
                stderr = spawn_error["message"]

            command_imports = self._read_import_log(import_log)

        output_files = self.capture_file_snapshot(workdir)
        changes = self._diff_file_snapshots(input_files["files"], output_files["files"])
        output_snapshot = {
            "captured_at": output_files["captured_at"],
            "exit_code": exit_code,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "timeout": timeout,
            "timed_out": timed_out,
            "spawn_error": spawn_error,
            "new_files": changes["new_files"],
            "changed_files": changes["changed_files"],
            "deleted_files": changes["deleted_files"],
            "errors": output_files["errors"],
        }
        deps = {
            "recorder_imports": imported_modules,
            "command_imports": command_imports,
        }

        run_id = self.store.insert_command_run(
            command_hash=command_hash,
            command=_json_dumps(normalized_command),
            input_snapshot=input_snapshot,
            output_snapshot=output_snapshot,
            timestamp=timestamp,
            deps=deps,
        )

        result = CommandResult(
            run_id=run_id,
            command_hash=command_hash,
            command=normalized_command,
            cwd=str(workdir),
            exit_code=exit_code,
            stdout=stdout or "",
            stderr=stderr or "",
            input_snapshot=input_snapshot,
            output_snapshot=output_snapshot,
            timestamp=timestamp,
            deps=deps,
            timed_out=timed_out,
            spawn_error=spawn_error,
        )
        if check:
            result.check_returncode()
        return result

    record = run

    def capture_file_snapshot(self, root: str | Path | None = None) -> dict[str, Any]:
        """Capture blake2b hashes for regular files below root."""

        root_path = Path(root or self.cwd).expanduser().resolve()
        files: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []

        def on_walk_error(error: OSError) -> None:
            errors.append({"path": getattr(error, "filename", ""), "error": str(error)})

        for dirpath, dirnames, filenames in os.walk(
            root_path,
            topdown=True,
            onerror=on_walk_error,
            followlinks=False,
        ):
            current_dir = Path(dirpath)
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not self._should_skip_path(current_dir / dirname, is_dir=True)
            ]

            for filename in filenames:
                path = current_dir / filename
                if self._should_skip_path(path, is_dir=False):
                    continue
                try:
                    metadata = self._path_metadata(path)
                except OSError as exc:
                    errors.append(
                        {
                            "path": self._relative_key(root_path, path),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    continue
                if metadata is None:
                    continue
                files[self._relative_key(root_path, path)] = metadata

        return {
            "root": str(root_path),
            "captured_at": _utc_now(),
            "files": files,
            "errors": errors,
        }

    def capture_imported_modules(self) -> dict[str, Any]:
        """Capture modules imported by the recorder process before a command."""

        modules: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        for name, module in list(sys.modules.items()):
            if not name or module is None:
                continue
            try:
                modules[name] = self._module_metadata(module)
            except Exception as exc:
                errors.append({"module": name, "error": f"{type(exc).__name__}: {exc}"})
        return {"captured_at": _utc_now(), "modules": modules, "errors": errors}

    def _start_process(
        self,
        command: str | Sequence[str],
        *,
        cwd: Path,
        shell: bool,
        env: Mapping[str, str],
    ) -> subprocess.Popen[str]:
        kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": dict(env),
            "shell": shell,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        command, shell = self._prepare_shell_command(command, shell, env)
        kwargs["shell"] = shell
        if os.name == "posix":
            kwargs["preexec_fn"] = self._child_preexec
        return subprocess.Popen(command, **kwargs)

    @staticmethod
    def _prepare_shell_command(
        command: str | Sequence[str],
        shell: bool,
        env: Mapping[str, str],
    ) -> tuple[str | Sequence[str], bool]:
        if (
            shell
            and os.name == "nt"
            and isinstance(command, str)
            and env.get("MSYSTEM")
        ):
            bash_path = CommandRecorder._resolve_windows_git_bash(env)
            if bash_path:
                return [bash_path, "-lc", command], False
        return command, shell

    @staticmethod
    def _resolve_windows_git_bash(env: Mapping[str, str]) -> str | None:
        override = env.get("OXIDE_SHELL")
        if override and Path(override).expanduser().exists():
            return str(Path(override).expanduser())

        path_entries = [
            entry.strip('"')
            for entry in env.get("PATH", "").split(os.pathsep)
            if entry.strip('"')
        ]
        candidates: list[Path] = []

        for entry in path_entries:
            path = Path(entry)
            candidate = path / "bash.exe"
            if _safe_exists(candidate) and _looks_like_git_bash(candidate):
                candidates.append(candidate)

            text = str(path)
            lowered = text.lower()
            if lowered.endswith("\\cmd") and "git" in lowered:
                candidates.append(path.parent / "bin" / "bash.exe")
                candidates.append(path.parent / "usr" / "bin" / "bash.exe")
            if lowered.endswith("\\bin") and "git" in lowered:
                candidates.append(path / "bash.exe")
                candidates.append(path.parent / "usr" / "bin" / "bash.exe")

        candidates.extend(
            [
                Path(r"C:\Program Files\Git\bin\bash.exe"),
                Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
                Path(r"C:\Program Files (x86)\Git\bin\bash.exe"),
                Path(r"C:\Program Files (x86)\Git\usr\bin\bash.exe"),
            ]
        )

        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            if _safe_exists(resolved) and _looks_like_git_bash(resolved):
                return str(resolved)
        return None

    @staticmethod
    def _child_preexec() -> None:
        """Set the child in its own process group before exec on POSIX."""

        if hasattr(os, "setsid"):
            os.setsid()

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            except OSError:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()

    def _path_metadata(self, path: Path) -> dict[str, Any] | None:
        stat_result = path.lstat()
        if stat.S_ISLNK(stat_result.st_mode):
            target = os.readlink(path)
            return {
                "type": "symlink",
                "target": target,
                "hash": self._hash_bytes(target.encode("utf-8", errors="replace")),
                "size": len(target),
                "mtime_ns": stat_result.st_mtime_ns,
                "mode": stat.S_IMODE(stat_result.st_mode),
            }
        if not stat.S_ISREG(stat_result.st_mode):
            return None
        return {
            "type": "file",
            "hash": self._hash_file(path),
            "size": stat_result.st_size,
            "mtime_ns": stat_result.st_mtime_ns,
            "mode": stat.S_IMODE(stat_result.st_mode),
        }

    def _module_metadata(self, module: Any) -> dict[str, Any]:
        spec = getattr(module, "__spec__", None)
        origin = getattr(spec, "origin", None) if spec is not None else None
        file_name = getattr(module, "__file__", None)
        metadata: dict[str, Any] = {"origin": origin, "file": file_name}
        if not file_name:
            return metadata

        try:
            path = Path(file_name).expanduser().resolve()
            if path.is_file():
                stat_result = path.stat()
                metadata.update(
                    {
                        "path": str(path),
                        "hash": self._hash_file(path),
                        "size": stat_result.st_size,
                        "mtime_ns": stat_result.st_mtime_ns,
                    }
                )
        except OSError as exc:
            metadata["error"] = f"{type(exc).__name__}: {exc}"
        return metadata

    def _hash_file(self, path: Path) -> str:
        hasher = hashlib.blake2b(digest_size=self.digest_size)
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()

    def _hash_bytes(self, value: bytes) -> str:
        hasher = hashlib.blake2b(digest_size=self.digest_size)
        hasher.update(value)
        return hasher.hexdigest()

    def _hash_json(self, value: Any) -> str:
        return self._hash_bytes(_json_dumps(value).encode("utf-8"))

    def _build_command_env(
        self,
        env: Mapping[str, str] | None,
        tracker_dir: Path,
    ) -> tuple[dict[str, str], Path]:
        sitecustomize = tracker_dir / "sitecustomize.py"
        sitecustomize.write_text(IMPORT_TRACKER, encoding="utf-8")
        import_log = tracker_dir / "imports.jsonl"

        command_env = os.environ.copy()
        if env:
            command_env.update({str(key): str(value) for key, value in env.items()})

        self._prepend_runtime_paths(command_env)

        existing_pythonpath = command_env.get("PYTHONPATH")
        command_env["PYTHONPATH"] = (
            str(tracker_dir)
            if not existing_pythonpath
            else str(tracker_dir) + os.pathsep + existing_pythonpath
        )
        command_env["OXIDE_IMPORT_LOG"] = str(import_log)
        return command_env, import_log

    @staticmethod
    def _prepend_runtime_paths(command_env: dict[str, str]) -> None:
        python_dir = Path(sys.executable).resolve().parent
        candidates = [python_dir, python_dir / "Scripts"]
        existing = command_env.get("PATH", "")
        existing_parts = existing.split(os.pathsep) if existing else []
        existing_lower = {part.lower() for part in existing_parts}
        additions = [
            str(candidate)
            for candidate in candidates
            if candidate.exists() and str(candidate).lower() not in existing_lower
        ]
        if additions:
            command_env["PATH"] = os.pathsep.join(additions + existing_parts)

    @staticmethod
    def _read_import_log(import_log: Path | None) -> dict[str, Any]:
        if import_log is None or not import_log.exists():
            return {"processes": [], "modules": {}, "errors": []}

        processes: list[dict[str, Any]] = []
        modules: dict[str, Any] = {}
        errors: list[dict[str, str]] = []
        try:
            for line_number, line in enumerate(import_log.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as exc:
                    errors.append({"line": str(line_number), "error": str(exc)})
                    continue
                processes.append({"pid": payload.get("pid")})
                for name, metadata in payload.get("modules", {}).items():
                    modules[name] = metadata
                errors.extend(payload.get("errors", []))
        except OSError as exc:
            errors.append({"path": str(import_log), "error": f"{type(exc).__name__}: {exc}"})
        return {"processes": processes, "modules": modules, "errors": errors}

    @staticmethod
    def _diff_file_snapshots(
        before: Mapping[str, Mapping[str, Any]],
        after: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any]:
        new_files = {path: metadata for path, metadata in after.items() if path not in before}
        changed_files = {
            path: metadata
            for path, metadata in after.items()
            if path in before and metadata.get("hash") != before[path].get("hash")
        }
        deleted_files = sorted(path for path in before if path not in after)
        return {
            "new_files": new_files,
            "changed_files": changed_files,
            "deleted_files": deleted_files,
        }

    def _should_skip_path(self, path: Path, *, is_dir: bool) -> bool:
        if is_dir and path.name in self.excluded_dirs:
            return True
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            resolved = path.expanduser().absolute()
        return resolved in self._db_related_paths

    @staticmethod
    def _relative_key(root: Path, path: Path) -> str:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            return path.as_posix()

    @staticmethod
    def _normalize_command(command: str | Sequence[str]) -> str | list[str]:
        if isinstance(command, str):
            return command
        return [str(part) for part in command]


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _looks_like_git_bash(path: Path) -> bool:
    lowered = str(path).lower()
    if "\\windows\\system32\\" in lowered or lowered.endswith("\\windows\\system32\\bash.exe"):
        return False
    return "git" in lowered or "msys" in lowered


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
