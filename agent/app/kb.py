"""The knowledge base, as the pipeline's memory of results it has produced.

Two questions, both by content hash:

    recall(sha256)          have I processed these exact bytes before?
    remember(item, ...)     record what I found, for next time

A hit skips the specialist entirely - no model call, no tool call, no cost -
which is the whole economic argument for agent memory. The security argument
runs the other way: whoever can write this table decides what the pipeline
will say about any input it has "seen before". The agent writes it, so a
compromised agent can poison its own future runs. Note that the SQL here is
fixed and the arguments are bound parameters; nothing the model produces is
ever spliced into a query.
"""

import json
import logging
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import config

log = logging.getLogger(__name__)

_conn: psycopg.AsyncConnection | None = None


async def connection() -> psycopg.AsyncConnection:
    """One connection per process; reconnect if the server went away."""
    global _conn
    if _conn is None or _conn.closed:
        _conn = await psycopg.AsyncConnection.connect(
            config.KB_DSN, row_factory=dict_row, autocommit=True, connect_timeout=10)
        log.info("knowledge base: connected")
    return _conn


async def close() -> None:
    global _conn
    if _conn is not None and not _conn.closed:
        await _conn.close()
    _conn = None


async def health() -> dict[str, Any]:
    """Used by run.py to fail loudly at startup rather than mid-run."""
    conn = await connection()
    cur = await conn.execute("SELECT count(*) AS remembered, "
                             "coalesce(sum(hits), 0) AS hits FROM memory")
    row = await cur.fetchone()
    return {k: int(v) for k, v in row.items()}


async def recall(sha256: str) -> dict[str, Any] | None:
    """The stored result for these bytes, or None. Counts the hit."""
    conn = await connection()
    cur = await conn.execute(
        """
        UPDATE memory SET hits = hits + 1, last_hit_at = now()
        WHERE sha256 = %s
        RETURNING kind, result, summary, first_id, hits
        """,
        (sha256,))
    return await cur.fetchone()


async def remember(sha256: str, kind: str, result: dict, summary: str,
                   item_id: str) -> None:
    """Store a result. The first answer for a given content hash wins."""
    conn = await connection()
    await conn.execute(
        """
        INSERT INTO memory (sha256, kind, result, summary, first_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (sha256) DO NOTHING
        """,
        (sha256, kind, Jsonb(result), summary, item_id))
    log.debug("memory: stored %s as %s", item_id, json.dumps(result)[:80])
