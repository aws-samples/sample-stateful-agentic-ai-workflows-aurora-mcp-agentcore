"""Aurora-backed traveler memory for Phase 4."""

import json
import uuid
from typing import Any, Dict, List, Optional

from backend.db.rds_data_client import get_rds_data_client
from backend.db.embedding_service import get_embedding_service

DEMO_TRAVELER_ID = "trv_meridian_demo"


class MemoryStore:
    def __init__(self) -> None:
        self.db = get_rds_data_client()
        self.embeddings = get_embedding_service()

    @staticmethod
    def _vector_literal(values: List[float]) -> str:
        return "[" + ",".join(str(v) for v in values) + "]"

    async def get_or_create_conversation(
        self,
        traveler_id: str,
        conversation_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
    ) -> str:
        if conversation_id:
            rows = await self.db.execute(
                "SELECT conversation_id FROM conversations WHERE conversation_id = %s",
                (conversation_id,),
                transaction_id=transaction_id,
            )
            if rows:
                return conversation_id

        new_id = conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
        await self.db.execute(
            """
            INSERT INTO conversations (conversation_id, traveler_id, started_at, last_message_at, message_count)
            VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)
            ON CONFLICT (conversation_id) DO NOTHING
            """,
            (new_id, traveler_id),
            transaction_id=transaction_id,
        )
        return new_id

    async def recall_short_term(
        self,
        conversation_id: str,
        limit: int = 6,
        transaction_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return await self.db.execute(
            """
            SELECT role, content, created_at FROM conversation_messages
            WHERE conversation_id = %s ORDER BY created_at DESC LIMIT %s
            """,
            (conversation_id, limit),
            transaction_id=transaction_id,
        )

    async def recall_preferences(
        self,
        traveler_id: str,
        limit: int = 8,
        transaction_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = await self.db.execute(
            """
            SELECT preference_type, preference_key, preference_value, confidence, source
            FROM traveler_preferences
            WHERE traveler_id = %s
            ORDER BY confidence DESC, last_seen_at DESC
            LIMIT %s
            """,
            (traveler_id, limit),
            transaction_id=transaction_id,
        )
        return [
            {
                "key": row["preference_key"],
                "value": row["preference_value"],
                "source": row.get("source") or row["preference_type"],
                "confidence": float(row.get("confidence") or 0.5),
            }
            for row in rows
        ]

    async def recall_profile(
        self,
        traveler_id: str,
        transaction_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        rows = await self.db.execute(
            """
            SELECT t.full_name, t.home_airport,
                   p.party_size, p.budget_min, p.budget_max, p.seat_preference,
                   p.dietary_notes, p.trip_goal
            FROM travelers t
            LEFT JOIN traveler_profiles p ON t.traveler_id = p.traveler_id
            WHERE t.traveler_id = %s
            """,
            (traveler_id,),
            transaction_id=transaction_id,
        )
        return rows[0] if rows else None

    async def recall_similar_interactions(
        self,
        traveler_id: str,
        query: str,
        limit: int = 3,
        transaction_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        try:
            embedding = self.embeddings.generate_text_embedding(query, input_type="search_query")
        except Exception:
            return []
        vec = self._vector_literal(embedding)
        return await self.db.execute(
            """
            SELECT query_text, response_summary, packages_shown,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM trip_interactions
            WHERE traveler_id = %s AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vec, traveler_id, vec, limit),
            transaction_id=transaction_id,
        )

    async def append_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        with_embedding: bool = True,
        transaction_id: Optional[str] = None,
    ) -> str:
        message_id = f"msg_{uuid.uuid4().hex[:12]}"
        if with_embedding and content.strip():
            try:
                vec = self._vector_literal(
                    self.embeddings.generate_text_embedding(content, input_type="search_document")
                )
                await self.db.execute(
                    """
                    INSERT INTO conversation_messages (message_id, conversation_id, role, content, embedding)
                    VALUES (%s, %s, %s, %s, %s::vector)
                    """,
                    (message_id, conversation_id, role, content, vec),
                    transaction_id=transaction_id,
                )
            except Exception:
                await self.db.execute(
                    """
                    INSERT INTO conversation_messages (message_id, conversation_id, role, content)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (message_id, conversation_id, role, content),
                    transaction_id=transaction_id,
                )
        else:
            await self.db.execute(
                """
                INSERT INTO conversation_messages (message_id, conversation_id, role, content)
                VALUES (%s, %s, %s, %s)
                """,
                (message_id, conversation_id, role, content),
                transaction_id=transaction_id,
            )
        await self.db.execute(
            """
            UPDATE conversations SET last_message_at = CURRENT_TIMESTAMP, message_count = message_count + 1
            WHERE conversation_id = %s
            """,
            (conversation_id,),
            transaction_id=transaction_id,
        )
        return message_id

    async def persist_interaction(
        self,
        traveler_id: str,
        conversation_id: str,
        query_text: str,
        response_summary: str,
        packages_shown: Optional[List[Dict[str, Any]]] = None,
        transaction_id: Optional[str] = None,
    ) -> str:
        interaction_id = f"int_{uuid.uuid4().hex[:12]}"
        combined = f"User: {query_text}\nAssistant: {response_summary}"
        try:
            vec = self._vector_literal(
                self.embeddings.generate_text_embedding(combined, input_type="search_document")
            )
            has_vec = True
        except Exception:
            vec = None
            has_vec = False

        payload = json.dumps(packages_shown or [])
        if has_vec:
            await self.db.execute(
                """
                INSERT INTO trip_interactions (
                    interaction_id, traveler_id, conversation_id,
                    query_text, response_summary, packages_shown, embedding
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::vector)
                """,
                (interaction_id, traveler_id, conversation_id, query_text, response_summary, payload, vec),
                transaction_id=transaction_id,
            )
        else:
            await self.db.execute(
                """
                INSERT INTO trip_interactions (
                    interaction_id, traveler_id, conversation_id,
                    query_text, response_summary, packages_shown
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (interaction_id, traveler_id, conversation_id, query_text, response_summary, payload),
                transaction_id=transaction_id,
            )
        return interaction_id

    async def write_audit(
        self,
        agent_name: str,
        operation: str,
        traveler_id: Optional[str],
        rls_traveler: Optional[str],
        rls_agent_type: Optional[str],
        iam_identity: Optional[str],
        rows_returned: int,
        transaction_id: Optional[str] = None,
    ) -> str:
        audit_id = f"aud_{uuid.uuid4().hex[:12]}"
        await self.db.execute(
            """
            INSERT INTO agent_audit_log (
                audit_id, traveler_id, agent_name, operation,
                rls_traveler, rls_agent_type, iam_identity, rows_returned
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                audit_id,
                traveler_id,
                agent_name,
                operation,
                rls_traveler,
                rls_agent_type,
                iam_identity,
                rows_returned,
            ),
            transaction_id=transaction_id,
        )
        return audit_id

    def format_memory_context(
        self,
        profile: Optional[Dict[str, Any]],
        short_term: List[Dict[str, Any]],
        preferences: List[Dict[str, Any]],
        similar: List[Dict[str, Any]],
    ) -> str:
        lines: List[str] = []
        if profile:
            lines.append(f"Traveler: {profile.get('full_name')} (home: {profile.get('home_airport')})")
            if profile.get("trip_goal"):
                lines.append(f"Active goal: {profile['trip_goal']}")
            if profile.get("dietary_notes"):
                lines.append(f"Dietary: {profile['dietary_notes']}")
        if preferences:
            lines.append("Preferences:")
            for p in preferences:
                lines.append(f"- {p['value']} ({p['confidence']:.2f})")
        if short_term:
            lines.append("Recent turns:")
            for msg in reversed(short_term):
                lines.append(f"- {msg['role']}: {str(msg['content'])[:120]}")
        if similar:
            lines.append("Similar past searches:")
            for s in similar:
                lines.append(f"- {str(s.get('query_text', ''))[:80]}")
        return "\n".join(lines)


_store: Optional[MemoryStore] = None


def get_memory_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


# Legacy alias
DEMO_CUSTOMER_ID = DEMO_TRAVELER_ID
