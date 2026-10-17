"""PostgreSQL store backend for snapdelta: `--store postgresql://USER@HOST:PORT/DATABASE`."""

from .store import PostgresSettings, PostgresStore

__all__ = ["PostgresSettings", "PostgresStore"]
