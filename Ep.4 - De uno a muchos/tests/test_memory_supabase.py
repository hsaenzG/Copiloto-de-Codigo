"""Round-trip tests for the native-SDK memory backend on Supabase.

Exercises ALL nine SessionRepository methods (not just the central ones)
against an in-memory Supabase double, and verifies that a fresh repository
built over the same store recovers the full history by session_id. No network,
no Supabase, no Bedrock.
"""

from __future__ import annotations

from strands.types.session import Session, SessionAgent, SessionMessage, SessionType

from code_copilot.memory.supabase_repository import SupabaseSessionRepository
from fakes import FakeSupabaseClient

AGENT_ID = "agent-1"
SESSION_ID = "session-round-trip"


def _message(index: int, role: str, text: str) -> SessionMessage:
    return SessionMessage(
        message={"role": role, "content": [{"text": text}]},
        message_id=index,
    )


def test_session_create_and_read_round_trip():
    client = FakeSupabaseClient()
    repo = SupabaseSessionRepository(client)

    created = repo.create_session(Session(session_id=SESSION_ID, session_type=SessionType.AGENT))
    assert created.session_id == SESSION_ID

    # A fresh repository over the same store still reads it back.
    other = SupabaseSessionRepository(client)
    read = other.read_session(SESSION_ID)
    assert read is not None
    assert read.session_id == SESSION_ID
    assert other.read_session("does-not-exist") is None


def test_agent_create_read_update():
    client = FakeSupabaseClient()
    repo = SupabaseSessionRepository(client)
    repo.create_session(Session(session_id=SESSION_ID, session_type=SessionType.AGENT))

    agent = SessionAgent(agent_id=AGENT_ID, state={"turns": 1}, conversation_manager_state={})
    repo.create_agent(SESSION_ID, agent)

    readAgent = repo.read_agent(SESSION_ID, AGENT_ID)
    assert readAgent is not None
    assert readAgent.agent_id == AGENT_ID
    assert readAgent.state == {"turns": 1}
    assert repo.read_agent(SESSION_ID, "missing") is None

    agent.state = {"turns": 2}
    repo.update_agent(SESSION_ID, agent)
    assert repo.read_agent(SESSION_ID, AGENT_ID).state == {"turns": 2}


def test_message_create_read_list_pagination():
    client = FakeSupabaseClient()
    repo = SupabaseSessionRepository(client)
    repo.create_session(Session(session_id=SESSION_ID, session_type=SessionType.AGENT))
    repo.create_agent(
        SESSION_ID, SessionAgent(agent_id=AGENT_ID, state={}, conversation_manager_state={})
    )

    for index in range(5):
        role = "user" if index % 2 == 0 else "assistant"
        repo.create_message(SESSION_ID, AGENT_ID, _message(index, role, f"msg {index}"))

    # read_message by composite key
    single = repo.read_message(SESSION_ID, AGENT_ID, 2)
    assert single is not None
    assert single.to_message()["content"][0]["text"] == "msg 2"
    assert repo.read_message(SESSION_ID, AGENT_ID, 99) is None

    # list_messages ordered by message_id
    allMessages = repo.list_messages(SESSION_ID, AGENT_ID)
    assert [m.message_id for m in allMessages] == [0, 1, 2, 3, 4]

    # pagination: limit + offset (inclusive range)
    page = repo.list_messages(SESSION_ID, AGENT_ID, limit=2, offset=1)
    assert [m.message_id for m in page] == [1, 2]


def test_update_message_redaction_upsert():
    client = FakeSupabaseClient()
    repo = SupabaseSessionRepository(client)
    repo.create_session(Session(session_id=SESSION_ID, session_type=SessionType.AGENT))
    repo.create_agent(
        SESSION_ID, SessionAgent(agent_id=AGENT_ID, state={}, conversation_manager_state={})
    )
    repo.create_message(SESSION_ID, AGENT_ID, _message(0, "user", "my secret is 12345"))

    # A guardrail redacts the message: update_message upserts the same PK.
    redacted = SessionMessage(
        message={"role": "user", "content": [{"text": "my secret is 12345"}]},
        message_id=0,
        redact_message={"role": "user", "content": [{"text": "[redacted]"}]},
    )
    repo.update_message(SESSION_ID, AGENT_ID, redacted)

    # Still one row (upsert, not insert), and it returns the redacted content.
    listed = repo.list_messages(SESSION_ID, AGENT_ID)
    assert len(listed) == 1
    assert listed[0].to_message()["content"][0]["text"] == "[redacted]"


def test_full_history_recovered_by_fresh_repository():
    """The core promise: persist with one repo, recover with another by id."""
    client = FakeSupabaseClient()
    writer = SupabaseSessionRepository(client)
    writer.create_session(Session(session_id=SESSION_ID, session_type=SessionType.AGENT))
    writer.create_agent(
        SESSION_ID, SessionAgent(agent_id=AGENT_ID, state={}, conversation_manager_state={})
    )
    writer.create_message(SESSION_ID, AGENT_ID, _message(0, "user", "where is auth handled?"))
    writer.create_message(SESSION_ID, AGENT_ID, _message(1, "assistant", "in auth.py"))

    reader = SupabaseSessionRepository(client)
    recovered = reader.list_messages(SESSION_ID, AGENT_ID)
    assert len(recovered) == 2
    assert recovered[0].to_message()["content"][0]["text"] == "where is auth handled?"
    assert recovered[1].to_message()["content"][0]["text"] == "in auth.py"
