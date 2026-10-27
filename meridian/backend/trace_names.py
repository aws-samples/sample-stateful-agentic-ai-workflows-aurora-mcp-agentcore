"""Names for trace steps that run as code.

A step is called an agent only when a model runs its loop. Everything named here is
ordinary Python: keyword routing, a typed tool call, a SQL read. The trace says so.
The frontend recognizes the SQL and retrieval names in ``spanTitles.ts``.
"""

SQL_CODE = "SQL search (code)"
MCP_CODE = "MCP client (code)"
RETRIEVAL_CODE = "Hybrid retrieval (code)"
AVAILABILITY_CODE = "Availability check (code)"
MEMORY_CODE = "Memory recall (code)"
PRODUCTION_CODE = "Production orchestrator (code)"
WORKFLOW_CODE = "Workflow graph (code)"
BOUNDARY_CODE = "Boundary check (code)"
