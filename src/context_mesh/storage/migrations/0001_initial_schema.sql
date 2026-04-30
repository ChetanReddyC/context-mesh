-- context-mesh v1 schema. JSON-typed columns are stored as TEXT in SQLite;
-- shape is enforced at the application layer. See docs/SCHEMA.md.

CREATE TABLE scopes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  level TEXT NOT NULL CHECK (level IN ('private','team','org')),
  team_id TEXT,
  org_id TEXT,
  created_at INTEGER NOT NULL,
  policy_json JSON
);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  repo TEXT NOT NULL,
  branch TEXT,
  agent TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  ended_at INTEGER,
  turn_count INTEGER,
  token_usage INTEGER,
  commit_sha TEXT,
  transcript_uri TEXT
);

CREATE INDEX idx_sessions_repo ON sessions(repo);
CREATE INDEX idx_sessions_agent ON sessions(agent);

CREATE TABLE nodes (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('episodic','semantic','procedural')),
  body TEXT NOT NULL,
  headline TEXT NOT NULL,
  summary TEXT,
  decisions JSON,
  failed_approaches JSON,
  warnings JSON,
  error_signatures JSON,
  cause_chain JSON,
  file_dependencies JSON,
  key_insight TEXT,
  tags JSON,
  scope_id TEXT NOT NULL REFERENCES scopes(id),
  source_session_id TEXT NOT NULL REFERENCES sessions(id),
  source_repo TEXT NOT NULL,
  source_branch TEXT,
  source_commit TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  decayed_at INTEGER,
  importance REAL NOT NULL DEFAULT 0,
  usage_count INTEGER NOT NULL DEFAULT 0,
  helpful_count INTEGER NOT NULL DEFAULT 0,
  superseded_by TEXT REFERENCES nodes(id),
  content_hash TEXT NOT NULL UNIQUE
);

CREATE INDEX idx_nodes_kind ON nodes(kind);
CREATE INDEX idx_nodes_scope ON nodes(scope_id);
CREATE INDEX idx_nodes_repo ON nodes(source_repo);
CREATE INDEX idx_nodes_decayed ON nodes(decayed_at) WHERE decayed_at IS NOT NULL;

CREATE TABLE edges (
  id TEXT PRIMARY KEY,
  from_node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  to_node_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
  relation TEXT NOT NULL CHECK (relation IN (
    'caused_by','applies_to','contradicts','generalizes','supersedes','co_occurs_with'
  )),
  confidence REAL NOT NULL DEFAULT 1.0,
  created_at INTEGER NOT NULL,
  created_by TEXT NOT NULL CHECK (created_by IN ('auto','manual','agent')),
  metadata JSON,
  UNIQUE(from_node_id, to_node_id, relation)
);

CREATE INDEX idx_edges_from ON edges(from_node_id);
CREATE INDEX idx_edges_to ON edges(to_node_id);
CREATE INDEX idx_edges_relation ON edges(relation);

CREATE VIRTUAL TABLE vec_nodes USING vec0(
  node_id TEXT PRIMARY KEY,
  embedding FLOAT[384]
);

CREATE TABLE vector_meta (
  node_id TEXT PRIMARY KEY REFERENCES nodes(id) ON DELETE CASCADE,
  model TEXT NOT NULL,
  dimensions INTEGER NOT NULL,
  embedded_at INTEGER NOT NULL
);

CREATE TABLE audit (
  id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  node_ids JSON,
  query TEXT,
  result_count INTEGER,
  metadata JSON,
  timestamp INTEGER NOT NULL
);

CREATE INDEX idx_audit_timestamp ON audit(timestamp);
CREATE INDEX idx_audit_actor ON audit(actor);
CREATE INDEX idx_audit_event ON audit(event_type);
