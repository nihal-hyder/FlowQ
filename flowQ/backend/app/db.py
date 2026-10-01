from contextlib import contextmanager
from typing import Iterator

from psycopg import Cursor
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

_pool: ConnectionPool | None = None


def open_pool() -> None:
    global _pool
    if _pool is not None:
        return
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set. Copy backend/.env.example to backend/.env and fill it in.")
    _pool = ConnectionPool(
        settings.database_url,
        min_size=1,
        max_size=settings.db_pool_size,
        # prepare_threshold=None keeps us compatible with Supabase's pgbouncer pooler
        kwargs={"row_factory": dict_row, "prepare_threshold": None},
        check=ConnectionPool.check_connection,
        open=True,
        timeout=20,
    )


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def tx() -> Iterator[Cursor]:
    """One database transaction: commits when the block ends, rolls back on any exception."""
    if _pool is None:
        open_pool()
    with _pool.connection() as conn:
        with conn.cursor() as cur:
            yield cur
