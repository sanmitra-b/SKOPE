"""Read locally persisted end-to-end evaluation summaries for administration views."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ARTIFACT_DIRECTORY = PROJECT_ROOT / "artifacts" / "evaluation"


def _completed_at(payload: dict[str, Any], path: Path) -> str:
    value = payload.get("completed_at")
    if value:
        return str(value)
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _sort_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def load_benchmark_history(
    directory: Path = DEFAULT_ARTIFACT_DIRECTORY,
    *,
    limit: int = 10,
    minimum_questions: int = 10,
) -> list[dict[str, Any]]:
    """Return newest representative benchmark runs without loading result rows into the API."""
    if not directory.exists():
        return []

    summaries: list[dict[str, Any]] = []
    for path in directory.glob("end_to_end_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            question_count = int(payload.get("question_count") or 0)
            metrics = payload.get("metrics")
            if question_count < minimum_questions or not isinstance(metrics, dict):
                continue
            summaries.append(
                {
                    "evaluation_run_id": f"artifact:{path.stem}",
                    "version": str(payload.get("version") or path.stem),
                    "status": "COMPLETED",
                    "metrics": metrics,
                    "configuration": {
                        "collection": payload.get("collection"),
                        "top_k": payload.get("top_k"),
                        "source": "benchmark_artifact",
                        "artifact_name": path.name,
                    },
                    "question_count": question_count,
                    "completed_at": _completed_at(payload, path),
                    "source": "benchmark_artifact",
                }
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A partial/corrupt artifact should not make the oversight endpoint unavailable.
            continue

    summaries.sort(
        key=lambda item: _sort_timestamp(str(item["completed_at"])),
        reverse=True,
    )
    return summaries[:limit]


__all__ = ["DEFAULT_ARTIFACT_DIRECTORY", "load_benchmark_history"]
