from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from .config import get_settings


@contextmanager
def connection(*, readonly: bool = False) -> Iterator[psycopg.Connection]:
    settings = get_settings()
    with psycopg.connect(
        settings.database_url,
        row_factory=dict_row,
        connect_timeout=5,
    ) as conn:
        if readonly:
            conn.execute("SET TRANSACTION READ ONLY")
        yield conn


def database_health() -> dict[str, object]:
    with connection(readonly=True) as conn:
        row = conn.execute(
            "SELECT current_database() AS database, CURRENT_TIMESTAMP AS checked_at"
        ).fetchone()
    return dict(row)
