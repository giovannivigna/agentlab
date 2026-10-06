-- The pipeline's memory: one row per distinct piece of content it has
-- processed, keyed by the SHA-256 of the bytes.
--
-- Keyed by content, not by name. Rename a file and it is still a hit; change
-- one byte and it is a miss. That is what makes this a cache you can trust
-- rather than one that serves yesterday's answer for today's file.
--
-- KB_MODE=fresh drops this table on every `make up`, so the first `make run`
-- after it is all misses and the second is all hits. KB_MODE=keep loads this
-- only into an empty database, so memory survives restarts.

DROP TABLE IF EXISTS memory;

CREATE TABLE memory (
    sha256      text PRIMARY KEY CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    kind        text NOT NULL CHECK (kind IN ('text', 'image', 'binary')),
    result      jsonb NOT NULL,
    summary     text,
    first_id    text NOT NULL,                  -- the item ID it was first seen as
    created_at  timestamptz NOT NULL DEFAULT now(),
    hits        integer NOT NULL DEFAULT 0,
    last_hit_at timestamptz
);
