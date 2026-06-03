"""Execution graph analysis primitives."""

from __future__ import annotations

from typing import Any, Iterable


class ExecutionGraphAnalyzer:
    """Build lightweight dependency summaries from recorded command runs."""

    def summarize(self, runs: Iterable[dict[str, Any]]) -> dict[str, Any]:
        runs_list = list(runs)
        return {
            "run_count": len(runs_list),
            "command_hashes": [run.get("command_hash") for run in runs_list],
        }
