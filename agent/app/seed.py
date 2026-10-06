"""Create the knowledge base's schema.

    python -m app.seed

Runs every .sql file in kb/seed/, in filename order. The knowledge base is the
pipeline's memory, so "seeding" it means creating the empty table the
pipeline fills - there is no data to load.

KB_MODE:
    fresh   drop and recreate every time    (each `make up` forgets)
    keep    only if the schema is missing   (memory survives restarts)
"""

import logging
import os
import sys

import psycopg

from app import config

log = logging.getLogger("seed")


def _has_schema(conn: psycopg.Connection) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.memory') IS NOT NULL")
        return cur.fetchone()[0]


def main() -> int:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S")

    mode = os.environ.get("KB_MODE", "fresh").lower()
    if mode not in {"keep", "fresh"}:
        raise SystemExit(f"KB_MODE must be 'fresh' or 'keep', not {mode!r}")

    with psycopg.connect(config.KB_DSN, autocommit=True, connect_timeout=30) as conn:
        if mode == "keep" and _has_schema(conn):
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM memory")
                log.info("KB_MODE=keep: memory exists with %d result(s) "
                         "- leaving it alone", cur.fetchone()[0])
            return 0

        files = sorted(p for p in config.SEED_DIR.iterdir() if p.suffix == ".sql")
        if not files:
            raise SystemExit(f"no .sql files in {config.SEED_DIR}")
        for path in files:
            log.info("KB_MODE=%s: loading %s", mode, path.name)
            with conn.cursor() as cur:
                cur.execute(path.read_text())
    log.info("memory is empty and ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
