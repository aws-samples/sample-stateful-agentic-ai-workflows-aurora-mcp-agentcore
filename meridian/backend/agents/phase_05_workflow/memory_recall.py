"""Phase 5's memory branch: saved context, then catalog matches."""

from typing import List

from backend.activity import ActivityEntry, Product, create_activity
from backend.agentcore.identity import get_agentcore_identity
from backend.retrieval.hybrid import retrieval_search


async def workflow_memory_recall(
    query: str,
    *,
    traveler_id: str,
    conversation_id: str,
) -> tuple[List[Product], List[ActivityEntry]]:
    """
    Workflow memory_recall branch: Aurora reads without a Strands loop.

    Reuses the same MemoryStore tables as Phase 4 @tools so the graph node
    story stays consistent: Phase 4 = Strands-driven recall; Phase 5 = explicit
    workflow node calling the same data plane.
    """
    from backend.memory.store import get_memory_store, DEMO_TRAVELER_ID

    tid = traveler_id or DEMO_TRAVELER_ID
    store = get_memory_store()
    authorization = get_agentcore_identity().authorization_context()
    activities: List[ActivityEntry] = []

    async with store.db.scoped_session(
        traveler_id=tid,
        agent_type="workflow_agent",
        authorization=authorization,
    ) as tx:
        prefs = await store.recall_preferences(tid, transaction_id=tx)
        activities.append(create_activity(
            activity_type="tool_call",
            title="Aurora recall: traveler_preferences",
            details=f"{len(prefs)} durable preference facts",
            agent_name="MemoryAgent",
            agent_file="agents/phase_04_production/memory_agent.py",
        ))

        if conversation_id:
            session = await store.recall_short_term(
                conversation_id, limit=6, transaction_id=tx
            )
            activities.append(create_activity(
                activity_type="tool_call",
                title="Aurora recall: conversation_messages",
                details=f"{len(session)} recent session turns",
                agent_name="MemoryAgent",
                agent_file="agents/phase_04_production/memory_agent.py",
            ))

        similar = await store.recall_similar_interactions(
            tid, query, limit=3, transaction_id=tx
        )
        activities.append(create_activity(
            activity_type="tool_call",
            title="Aurora recall: trip_interactions (pgvector)",
            details=f"{len(similar)} semantically similar past interactions",
            agent_name="MemoryAgent",
            agent_file="agents/phase_04_production/memory_agent.py",
        ))

    products, search_activities = await retrieval_search(query, limit=5)
    activities.extend(search_activities)
    return products, activities
