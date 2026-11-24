-- Additive: outcome reports that weight search ranking, and typed links between
-- memories. Neither table can change a memory's status or approve anything.

CREATE TABLE IF NOT EXISTS memory_outcomes (
  outcome_id  TEXT PRIMARY KEY,
  memory_id   TEXT NOT NULL,
  outcome     TEXT NOT NULL CHECK(outcome IN ('helped', 'misled')),
  session_id  TEXT NOT NULL,
  actor       TEXT NOT NULL,
  note        TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY(memory_id) REFERENCES memories(id),
  UNIQUE(memory_id, session_id, actor)
);

CREATE INDEX IF NOT EXISTS idx_memory_outcomes_memory
ON memory_outcomes(memory_id, outcome);

CREATE TABLE IF NOT EXISTS memory_links (
  from_memory_id TEXT NOT NULL,
  to_memory_id   TEXT NOT NULL,
  relation       TEXT NOT NULL
                 CHECK(relation IN ('supersedes', 'requires', 'conflicts_with', 'refines')),
  reason         TEXT NOT NULL,
  actor          TEXT NOT NULL,
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(from_memory_id, to_memory_id, relation),
  CHECK(from_memory_id <> to_memory_id),
  FOREIGN KEY(from_memory_id) REFERENCES memories(id),
  FOREIGN KEY(to_memory_id) REFERENCES memories(id)
);

CREATE INDEX IF NOT EXISTS idx_memory_links_to
ON memory_links(to_memory_id, relation);

-- A Superseded memory points at its replacement through related_memory_id.
-- The link reads replacement -> supersedes -> old memory.
INSERT OR IGNORE INTO memory_links
  (from_memory_id, to_memory_id, relation, reason, actor)
SELECT related_memory_id, id, 'supersedes',
       'Backfilled from the Superseded status and its related_memory_id.',
       'system:migration-0004'
FROM memories
WHERE status = 'Superseded'
  AND related_memory_id IS NOT NULL
  AND related_memory_id <> id
  AND related_memory_id IN (SELECT id FROM memories);
