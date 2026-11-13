"""Wiring for the native-SDK memory used by the HTTP service.

Builds a Supabase client, a ``RepositorySessionManager`` over
``SupabaseSessionRepository``, and a native conversation manager
(``SlidingWindowConversationManager`` or ``SummarizingConversationManager``).

The CLI/educational path keeps using the Ep.2 ``MemoryStore`` facade; the
service path uses this native contract. Both coexist on purpose (see README).
"""

from __future__ import annotations

from typing import Any

from code_copilot.config import (
    CONVERSATION_MANAGER_SUMMARIZING,
    ServiceConfig,
)
from code_copilot.memory.supabase_repository import SupabaseSessionRepository


def buildSupabaseClient(config: ServiceConfig) -> Any:
    """Create a ``supabase.Client`` from the service configuration.

    Raises:
        ValueError: When ``SUPABASE_URL`` / ``SUPABASE_KEY`` are not configured.
    """
    if not config.supabaseUrl or not config.supabaseKey:
        raise ValueError(
            "Supabase is not configured: set SUPABASE_URL and SUPABASE_KEY."
        )
    from supabase import create_client

    return create_client(config.supabaseUrl, config.supabaseKey)


def buildConversationManager(config: ServiceConfig):
    """Build the native conversation manager selected by configuration.

    ``sliding_window`` is cheap and needs no model call; ``summarizing``
    preserves older context at the cost of an extra model call. Pruning is the
    conversation manager's job (not a hand-rolled ``prune`` function anymore).
    """
    if config.conversationManager == CONVERSATION_MANAGER_SUMMARIZING:
        from strands.agent.conversation_manager import (
            SummarizingConversationManager,
        )

        return SummarizingConversationManager()

    from strands.agent.conversation_manager import (
        SlidingWindowConversationManager,
    )

    return SlidingWindowConversationManager(window_size=config.conversationWindowSize)


def buildSessionManager(sessionId: str, client: Any):
    """Build a ``RepositorySessionManager`` bound to Supabase for ``sessionId``.

    The agent then loads and saves on its own: no manual ``loadHistory`` /
    ``appendExchange`` like Ep.2.
    """
    from strands.session.repository_session_manager import (
        RepositorySessionManager,
    )

    repository = SupabaseSessionRepository(client)
    return RepositorySessionManager(
        session_id=sessionId, session_repository=repository
    )
