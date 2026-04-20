-- 0001_init.sql — initial Panel app-state tables.
-- Studio spec §8. All `create table if not exists` so re-running is a no-op.
-- Timestamps are stored as ISO-8601 text; SQLite has no native timestamptz.

create table if not exists recent_projects (
  path text primary key,
  name text,
  last_opened_at text
);

create table if not exists custom_presets (
  id text primary key,
  kind text not null,              -- 'cutmaster' | 'deliver' | 'grade'
  name text not null,
  payload text not null,           -- JSON
  created_at text default (datetime('now'))
);

create table if not exists cutmaster_sessions (
  id text primary key,
  project_path text,
  timeline_id text,
  state text not null,             -- JSON snapshot
  updated_at text default (datetime('now'))
);

create table if not exists panel_state (
  key text primary key,
  value text not null              -- JSON
);
