-- Supabase / PostgreSQL schema for the native-SDK session memory backend
-- (Episode 3). Run this once in the Supabase SQL editor.
--
-- Strands SDK objects are stored serialized in the `data` jsonb columns via
-- their own to_dict()/from_dict(); we do not invent a serialization format.

-- One row per session. The Session object and its SessionAgent map live in the
-- `data` jsonb: { "session": {...}, "agents": { "<agent_id>": {...} } }.
create table if not exists sessions (
    session_id text primary key,
    data       jsonb not null
);

-- One row per message, keyed by the composite (session_id, agent_id,
-- message_id). `data` holds the serialized SessionMessage.
create table if not exists messages (
    session_id text    not null,
    agent_id   text    not null,
    message_id integer not null,
    data       jsonb   not null,
    primary key (session_id, agent_id, message_id)
);

-- Helps list_messages(), which orders by message_id per (session, agent).
create index if not exists messages_session_agent_idx
    on messages (session_id, agent_id, message_id);
