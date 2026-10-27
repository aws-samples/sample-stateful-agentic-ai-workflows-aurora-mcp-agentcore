"""
MCP (Model Context Protocol) servers and clients for Meridian.

The custom ``meridian-concierge`` server serves Phase 2's typed tools; the custom
``meridian-memory`` server serves traveler memory to external MCP hosts. The reference agent
in ``backend/agents/phase_02_mcp/`` starts ``awslabs.postgres-mcp-server`` itself.
"""
