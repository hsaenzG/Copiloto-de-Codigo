"""Native-SDK session memory backed by Supabase (PostgreSQL).

Episode 2 kept memory behind the project's own ``MemoryStore`` interface on
purpose, to *see* the mechanism. Now that the copilot becomes a service with
real users, memory moves to the Strands SDK's native contract: this module
extends the SDK's ``SessionRepository`` (the same base ``FileSessionManager``
and ``S3SessionManager`` build on) and plugs Supabase in as the backend --
managed PostgreSQL, concurrent, shared across service instances.

We do NOT hand-roll another interface, and we do NOT invent a serialization
format: SDK objects (``Session``, ``SessionAgent``, ``SessionMessage``) are
stored through their own ``to_dict()`` / ``from_dict()`` in ``jsonb`` columns.

Reference open-source implementation: ``supabase-strands``.

Strands separates the two memory responsibilities:
  - the *session manager* persists (``FileSessionManager``, ``S3SessionManager``,
    and here ``RepositorySessionManager`` + this repository);
  - the *conversation manager* prunes (``SlidingWindowConversationManager``,
    ``SummarizingConversationManager``).

Schema (see ``schema.sql`` next to this module)::

    create table sessions (
        session_id text primary key,
        data       jsonb not null
    );
    create table messages (
        session_id text    not null,
        agent_id   text    not null,
        message_id integer not null,
        data       jsonb   not null,
        primary key (session_id, agent_id, message_id)
    );

The ``SessionAgent`` rows live inside the ``sessions.data`` jsonb under an
``agents`` map keyed by ``agent_id`` (one row per session, no extra table).
"""

from __future__ import annotations

from typing import Any

from strands.session.session_repository import SessionRepository
from strands.types.session import Session, SessionAgent, SessionMessage

_SESSIONS_TABLE = "sessions"
_MESSAGES_TABLE = "messages"


class SupabaseSessionRepository(SessionRepository):
    """A ``SessionRepository`` whose backend is a Supabase PostgreSQL project.

    Receives a ready ``supabase.Client`` (built with
    ``create_client(SUPABASE_URL, SUPABASE_KEY)``) so credentials never live in
    this class. All nine methods are fully implemented and executable.
    """

    def __init__(self, client: Any) -> None:
        """Store the injected Supabase client.

        Args:
            client: A ``supabase.Client`` instance.
        """
        self._client = client

    # --- helpers ------------------------------------------------------------

    def _sessionsTable(self):
        return self._client.table(_SESSIONS_TABLE)

    def _messagesTable(self):
        return self._client.table(_MESSAGES_TABLE)

    @staticmethod
    def _sessionRow(session: Session) -> dict:
        """Build the ``sessions`` row payload for a ``Session`` (no agents yet)."""
        return {
            "session_id": session.session_id,
            "data": {"session": session.to_dict(), "agents": {}},
        }

    def _readSessionRow(self, session_id: str) -> dict | None:
        """Return the raw ``sessions.data`` jsonb for a session, or ``None``."""
        response = (
            self._sessionsTable()
            .select("data")
            .eq("session_id", session_id)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return rows[0]["data"]

    # --- sessions -----------------------------------------------------------

    def create_session(self, session: Session, **kwargs: Any) -> Session:
        """Create a new Session row (idempotent via upsert on the PK)."""
        self._sessionsTable().upsert(self._sessionRow(session)).execute()
        return session

    def read_session(self, session_id: str, **kwargs: Any) -> Session | None:
        """Read a Session by id, or ``None`` when it does not exist."""
        data = self._readSessionRow(session_id)
        if data is None:
            return None
        return Session.from_dict(data["session"])

    # --- agents (stored inside sessions.data['agents']) ---------------------

    def create_agent(
        self, session_id: str, session_agent: SessionAgent, **kwargs: Any
    ) -> None:
        """Create/store a SessionAgent inside the session's jsonb."""
        data = self._readSessionRow(session_id)
        if data is None:
            # No session row yet: create a minimal one so the agent has a home.
            data = {"session": {"session_id": session_id}, "agents": {}}
        data.setdefault("agents", {})[session_agent.agent_id] = session_agent.to_dict()
        self._sessionsTable().upsert(
            {"session_id": session_id, "data": data}
        ).execute()

    def read_agent(
        self, session_id: str, agent_id: str, **kwargs: Any
    ) -> SessionAgent | None:
        """Read a SessionAgent from the session's jsonb, or ``None``."""
        data = self._readSessionRow(session_id)
        if data is None:
            return None
        agentDict = (data.get("agents") or {}).get(agent_id)
        if agentDict is None:
            return None
        return SessionAgent.from_dict(agentDict)

    def update_agent(
        self, session_id: str, session_agent: SessionAgent, **kwargs: Any
    ) -> None:
        """Update an existing SessionAgent in the session's jsonb."""
        data = self._readSessionRow(session_id)
        if data is None:
            raise ValueError(f"Session {session_id!r} does not exist")
        data.setdefault("agents", {})[session_agent.agent_id] = session_agent.to_dict()
        self._sessionsTable().upsert(
            {"session_id": session_id, "data": data}
        ).execute()

    # --- messages -----------------------------------------------------------

    @staticmethod
    def _messageRow(
        session_id: str, agent_id: str, session_message: SessionMessage
    ) -> dict:
        """Build the composite-PK ``messages`` row for a SessionMessage."""
        return {
            "session_id": session_id,
            "agent_id": agent_id,
            "message_id": session_message.message_id,
            "data": session_message.to_dict(),
        }

    def create_message(
        self,
        session_id: str,
        agent_id: str,
        session_message: SessionMessage,
        **kwargs: Any,
    ) -> None:
        """Insert a new Message for the agent."""
        self._messagesTable().insert(
            self._messageRow(session_id, agent_id, session_message)
        ).execute()

    def read_message(
        self, session_id: str, agent_id: str, message_id: int, **kwargs: Any
    ) -> SessionMessage | None:
        """Read a single Message by its composite key, or ``None``."""
        response = (
            self._messagesTable()
            .select("data")
            .eq("session_id", session_id)
            .eq("agent_id", agent_id)
            .eq("message_id", message_id)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        return SessionMessage.from_dict(rows[0]["data"])

    def update_message(
        self,
        session_id: str,
        agent_id: str,
        session_message: SessionMessage,
        **kwargs: Any,
    ) -> None:
        """Upsert a Message by its composite PK.

        Used mainly when a guardrail redacts message content: the row for the
        existing ``message_id`` is overwritten with the redacted version.
        """
        self._messagesTable().upsert(
            self._messageRow(session_id, agent_id, session_message)
        ).execute()

    def list_messages(
        self,
        session_id: str,
        agent_id: str,
        limit: int | None = None,
        offset: int = 0,
        **kwargs: Any,
    ) -> list[SessionMessage]:
        """List an agent's Messages ordered by ``message_id``, with pagination."""
        query = (
            self._messagesTable()
            .select("data")
            .eq("session_id", session_id)
            .eq("agent_id", agent_id)
            .order("message_id")
        )
        if limit is not None:
            # Supabase range is inclusive on both ends.
            query = query.range(offset, offset + limit - 1)
        elif offset:
            # No limit but a starting offset: use a very large upper bound.
            query = query.range(offset, offset + 1_000_000)
        response = query.execute()
        rows = response.data or []
        return [SessionMessage.from_dict(row["data"]) for row in rows]
