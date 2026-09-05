"""Apply the curated, read-only analytics surface used by the SQL agent."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import get_settings
import psycopg


def main() -> None:
    sql = (ROOT / "database" / "analytics_views.sql").read_text(encoding="utf-8")
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        conn.execute(sql)
    print("SKOPE analytics views applied.")


if __name__ == "__main__":
    main()
