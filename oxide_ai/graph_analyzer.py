"""Execution graph analysis over recorded Oxide command runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .query_engine import (
    _exit_code,
    _load_command_rows,
    _normalize_file_key,
    _output_preview,
    _parse_timestamp,
    _resolve_db_path,
    _snapshot_files,
    _written_files,
)

try:
    import networkx as nx
except ImportError:  # pragma: no cover - exercised only without dependency.
    nx = None  # type: ignore[assignment]


class ExecutionGraph:
    """Build and query a command/file execution graph."""

    def __init__(self, db_path: str) -> None:
        self.db_path = _resolve_db_path(db_path)
        self.graph: Any | None = None

    def build_graph(self) -> Any:
        """Build a DiGraph with command and file nodes."""

        if nx is None:
            raise ImportError("networkx is required; install dependencies from requirements.txt")

        graph = nx.DiGraph()
        rows = _load_command_rows(Path(self.db_path), limit=None, ascending=True)

        for row in rows:
            command_node = self._command_node(row)
            graph.add_node(
                command_node,
                kind="command",
                run_id=row["id"],
                command_hash=row["command_hash"],
                command=row["command"],
                timestamp=row["timestamp"],
                exit_code=_exit_code(row["output_snapshot"]),
                output_preview=_output_preview(row["output_snapshot"]),
            )

            input_files = _snapshot_files(row["input_snapshot"])
            for filepath, metadata in input_files.items():
                file_node = self._file_node(filepath)
                graph.add_node(
                    file_node,
                    kind="file",
                    filepath=filepath,
                    hash=metadata.get("hash"),
                    size=metadata.get("size"),
                )
                graph.add_edge(file_node, command_node, kind="reads")

            output = row["output_snapshot"]
            writes = _written_files(output)
            for change_type, files in (
                ("created", writes["created"]),
                ("modified", writes["modified"]),
            ):
                for filepath, metadata in files.items():
                    file_node = self._file_node(filepath)
                    graph.add_node(
                        file_node,
                        kind="file",
                        filepath=filepath,
                        hash=metadata.get("hash"),
                        size=metadata.get("size"),
                    )
                    graph.add_edge(command_node, file_node, kind="writes", change_type=change_type)

            for filepath in output.get("deleted_files") or []:
                file_node = self._file_node(filepath)
                graph.add_node(file_node, kind="file", filepath=filepath, deleted=True)
                graph.add_edge(command_node, file_node, kind="writes", change_type="deleted")

        graph.graph["has_cycles"] = bool(self.detect_cycles(graph))
        self.graph = graph
        return graph

    def find_shortest_path(self, from_node: str, to_node: str) -> list[dict[str, Any]]:
        """Return the shortest graph path between two files or command nodes."""

        graph = self._ensure_graph()
        start = self._resolve_node(from_node)
        end = self._resolve_node(to_node)
        try:
            path = nx.shortest_path(graph, source=start, target=end)
        except nx.NetworkXNoPath:
            return []
        return [self._describe_node(node) for node in path]

    def get_upstream_deps(self, filepath: str) -> list[dict[str, Any]]:
        """Return all commands and files that contributed to filepath."""

        graph = self._ensure_graph()
        node = self._file_node(filepath)
        if node not in graph:
            return []
        ancestors = nx.ancestors(graph, node)
        return self._ordered_descriptions(ancestors)

    def get_downstream_impact(self, filepath: str, timestamp: str) -> list[dict[str, Any]]:
        """Return commands and files downstream of filepath after timestamp."""

        graph = self._ensure_graph()
        node = self._file_node(filepath)
        if node not in graph:
            return []
        cutoff = _parse_timestamp(timestamp)
        descendants = nx.descendants(graph, node)
        impacted = []
        for descendant in descendants:
            attrs = graph.nodes[descendant]
            if attrs.get("kind") == "command":
                command_time = _parse_timestamp(attrs["timestamp"])
                if command_time >= cutoff:
                    impacted.append(descendant)
            elif attrs.get("kind") == "file":
                impacted.append(descendant)
        return self._ordered_descriptions(set(impacted))

    def topological_sort(self) -> list[dict[str, Any]]:
        """Return a topological ordering, raising if cycles are present."""

        graph = self._ensure_graph()
        cycles = self.detect_cycles(graph)
        if cycles:
            raise ValueError(f"execution graph contains cycles: {cycles[:5]}")
        return [self._describe_node(node) for node in nx.topological_sort(graph)]

    def detect_cycles(self, graph: Any | None = None) -> list[list[str]]:
        """Return simple cycles in the graph."""

        if nx is None:
            raise ImportError("networkx is required; install dependencies from requirements.txt")
        target = graph if graph is not None else self._ensure_graph()
        return [list(cycle) for cycle in nx.simple_cycles(target)]

    def ascii_recent(self, limit: int = 10) -> str:
        """Render a compact ASCII tree of recent commands and file writes."""

        rows = _load_command_rows(Path(self.db_path), limit=limit, ascending=False)
        if not rows:
            return "No recorded commands found."

        lines: list[str] = []
        for row in rows:
            output = row["output_snapshot"]
            writes = _written_files(output)
            changed = sorted(set(writes["created"]) | set(writes["modified"]))
            deleted = sorted(output.get("deleted_files") or [])
            command = row["command"]
            if isinstance(command, list):
                command = " ".join(str(part) for part in command)
            state = "OK" if _exit_code(output) == 0 else "FAIL"
            lines.append(
                f"[{row['id']:>3}] {state:<4} {row['timestamp']}  {command}"
            )
            lines.append(f"  |-- hash {row['command_hash'][:16]}")
            for filepath in changed[:8]:
                lines.append(f"  |-- writes {filepath}")
            for filepath in deleted[:8]:
                lines.append(f"  |-- deletes {filepath}")
            if not changed and not deleted:
                lines.append("  `-- writes none")
        return "\n".join(lines)

    def _ensure_graph(self) -> Any:
        return self.graph if self.graph is not None else self.build_graph()

    def _resolve_node(self, value: str) -> str:
        graph = self._ensure_graph()
        if value in graph:
            return value
        file_node = self._file_node(value)
        if file_node in graph:
            return file_node
        for node, attrs in graph.nodes(data=True):
            if attrs.get("command_hash") == value or str(attrs.get("run_id")) == value:
                return node
        return value

    def _ordered_descriptions(self, nodes: set[str]) -> list[dict[str, Any]]:
        graph = self._ensure_graph()
        subgraph = graph.subgraph(nodes).copy()
        try:
            ordered = list(nx.topological_sort(subgraph))
        except nx.NetworkXUnfeasible:
            ordered = sorted(nodes)
        return [self._describe_node(node) for node in ordered]

    def _describe_node(self, node: str) -> dict[str, Any]:
        graph = self._ensure_graph()
        attrs = dict(graph.nodes[node])
        attrs["node"] = node
        return attrs

    @staticmethod
    def _command_node(row: Mapping[str, Any]) -> str:
        return f"cmd:{row['id']}:{row['command_hash'][:12]}"

    @staticmethod
    def _file_node(filepath: str) -> str:
        return "file:" + _normalize_file_key(filepath)


class ExecutionGraphAnalyzer:
    """Compatibility wrapper around ExecutionGraph."""

    def __init__(self, db_path: str = ".oxide/oxide.db") -> None:
        self.execution_graph = ExecutionGraph(db_path)

    def summarize(self) -> dict[str, Any]:
        graph = self.execution_graph.build_graph()
        return {
            "nodes": graph.number_of_nodes(),
            "edges": graph.number_of_edges(),
            "cycles": self.execution_graph.detect_cycles(graph),
        }
