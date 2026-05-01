-- Per-source-adapter durable sync state. One row per adapter (keyed by name).
-- `cursor` is the human-readable last-known position (e.g., a git SHA, a
-- filename, an ISO timestamp); `state_json` is opaque adapter-specific JSON
-- (e.g., a set of seen content hashes for the agent-memory adapter). See
-- docs/EXTENSIBILITY.md section 4 (Source Adapters).

CREATE TABLE adapter_sync_state (
  adapter_name TEXT PRIMARY KEY,
  cursor TEXT NOT NULL DEFAULT '',
  last_synced_at INTEGER,
  state_json TEXT NOT NULL DEFAULT '{}'
);
