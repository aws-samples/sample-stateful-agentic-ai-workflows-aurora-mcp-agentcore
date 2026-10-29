"""Phase 5's memory branch: saved context, then catalog matches."""

import asyncio
from typing import List

from backend.activity import ActivityEntry, Product, create_activity
from backend.agentcore.identity import get_agentcore_identity
from backend.retrieval.hybrid import retrieval_search
from backend.trace_names import MEMORY_CODE


async def workflow_memory_recall(
    query: str,
    *,
    traveler_id: str,
    conversation_id: str,
) -> tuple[List[Product], List[ActivityEntry]]:
    """
    Workflow memory_recall branch: Aurora reads without a Strands loop.

    Reads the same MemoryStore tables Phase 4 reads. Phase 4's ProductionAgent calls
    the MemoryAgent recall methods directly in code before the Runtime turn; no model
    chooses the recall in either phase. Phase 5 does it in an explicit graph node.
    """
    from backend.memory.store import get_memory_store, DEMO_TRAVELER_ID

    tid = traveler_id or DEMO_TRAVELER_ID
    store = get_memory_store()
    authorization = get_agentcore_identity().authorization_context()
    activities: List[ActivityEntry] = []
    query_vector = await asyncio.to_thread(
        store.prepare_embedding_vector, query, input_type="search_query"
    )

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
            agent_name=MEMORY_CODE,
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
                agent_name=MEMORY_CODE,
                agent_file="agents/phase_04_production/memory_agent.py",
            ))

        similar = await store.recall_similar_interactions(
            tid,
            query,
            limit=3,
            transaction_id=tx,
            query_vector=query_vector,
            embedding_prepared=True,
        )
        activities.append(create_activity(
            activity_type="tool_call",
            title="Aurora recall: trip_interactions (pgvector)",
            details=f"{len(similar)} semantically similar past interactions",
            agent_name=MEMORY_CODE,
            agent_file="agents/phase_04_production/memory_agent.py",
        ))

    products, search_activities = await retrieval_search(query, limit=5)
    activities.extend(search_activities)
    return products, activities
