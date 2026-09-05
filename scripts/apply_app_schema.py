"""Apply SKOPE's additive RAG and application schemas."""

from __future__ import annotations

import sys
from pathlib import Path


BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from backend.app.db import connection  # noqa: E402


def main() -> None:
    sql = (BASE / "database" / "app_schema.sql").read_text(encoding="utf-8")
    with connection() as conn:
        conn.execute(sql)
    print("Applied database/app_schema.sql")


if __name__ == "__main__":
    main()
