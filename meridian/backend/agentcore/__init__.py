"""
Bedrock AgentCore Memory + Identity wrappers used by Phase 4.

Aurora is still the durable system of record for traveler preferences and
interaction embeddings.  AgentCore Memory handles the *session-store* slice
of memory the abstract calls out: ephemeral, multi-turn context that the
managed service summarizes and serves back.
"""
