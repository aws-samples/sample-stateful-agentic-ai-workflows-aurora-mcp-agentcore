-- 021: record every tool call a turn made, how each one ended, and how a failed turn failed.
--
-- agent_audit_log recorded who asked and under which traveler scope, but not which tools the
-- turn called or how they ended, and authorization_decision was written as 'allow' whatever
-- happened. One summary word cannot describe a turn with several calls, so two nullable columns
-- describe it. Every Phase 4 turn that calls the Runtime writes one row, including a turn whose
-- call fails.
--   tool_calls  a JSON array with one element per tool call, in the order the calls started;
--               [] for a turn that called no tool; NULL when the calls are not known. NULL
--               covers rows written before this migration, turns from a Runtime whose spans
--               carry no call ids, and every failed Runtime call that has no Runtime error event
--               after a span: a transport error, a timeout, a broken stream, a stream that ends
--               without a result, a cancellation during the call, a failure before the request
--               is sent, or a Runtime error before any span. Each element is an object:
--     tool             the contract name, without the Gateway target prefix; a non-empty string
--     result           allowed, refused or error
--     refused_by       only when result is refused: identity_check (the Gateway request
--                      interceptor), policy (Cedar), workload_grant (the holds Lambda's traveler
--                      grant), database_guard (an Aurora function or the Lambda rejected
--                      arguments outside the authorized scope or the stored record),
--                      business_rule (an authorized request a business rule declined) or
--                      runtime_guard (the Runtime declined a second governed write on a turn)
--     refusal_code     the named code the evidence carries as a non-empty string, else null
--                      or absent
--     access_audit_id  the traveler_access_audit row of a workload_grant refusal, as a
--                      non-empty string; null or absent on every other element
--   turn_error  the error that ended the turn before its row was written: its Runtime call
--               failed, or the backend failed after the Runtime returned (for example in the
--               trace forwarding or the catalog refresh). The error's class, then ": " and its
--               message with account ids masked, cut at a word boundary to at most 300
--               characters ending with "..."; the class alone when the message is empty or
--               cannot be read. A non-empty string of at most 400 characters. For example:
--                 ToolSetupError: Tool MeridianHolds___confirm_booking could not run
--                 (BotoCoreError): An unspecified error occurred
--               turn_error stays NULL only for a turn that completed, or one that failed after
--               its row was committed (the memory step and the trace entries that follow it).
--               It is also NULL on rows written before this migration.
-- authorization_decision for a row with tool_calls is deny when any call was refused by
-- identity_check, policy, workload_grant or database_guard; otherwise error when any call ended
-- in error or turn_error is set; otherwise allow, which includes business_rule and
-- runtime_guard refusals and a turn with no calls. agent_audit_log_tool_calls_decision enforces
-- it; a row with tool_calls NULL keeps whatever decision it had, except that a row with a
-- turn_error is never allow (agent_audit_log_turn_error_decision). So a failed turn whose
-- reported calls all succeeded is error. turn_error stays NULL only for a turn that completed,
-- or one that failed after its row was committed (the memory step and the trace entries that
-- follow it). These are the rules backend/memory/store.py write_audit applies before it writes.
-- The element checks use jsonb_path_exists, which is immutable, and test each element's type
-- explicitly, because a lax path comparison with a missing or mistyped member is not true.
-- agent_audit_log_tool_calls_object rejects an element that is not an object, which a lax member
-- accessor would otherwise read through when the element is an array.
-- PostgreSQL tests CHECK constraints in name order, so a row that breaks several rules names the
-- first of them.
-- The decision CHECK on tool_calls reads turn_error, which the same statement adds. PostgreSQL
-- adds every column of an ALTER TABLE before it adds any CHECK, so the column exists by then.
-- Run again, the statement adds nothing: IF NOT EXISTS skips a column that exists together
-- with its CHECKs, and the view is replaced by the same definition.
-- The audit view appends the columns after the existing ones, which CREATE OR REPLACE VIEW
-- allows.
ALTER TABLE agent_audit_log
    ADD COLUMN IF NOT EXISTS tool_calls JSONB
        CONSTRAINT agent_audit_log_tool_calls_array
            CHECK (jsonb_typeof(tool_calls) = 'array')
        CONSTRAINT agent_audit_log_tool_calls_object
            CHECK (NOT jsonb_path_exists(tool_calls,
                '$[*].type() ? (@ != "object")'))
        CONSTRAINT agent_audit_log_tool_calls_result
            CHECK (NOT jsonb_path_exists(tool_calls,
                '$[*] ? (!(@.result.type() == "string" && '
                '(@.result == "allowed" || @.result == "refused" || @.result == "error")))'))
        CONSTRAINT agent_audit_log_tool_calls_refused_by
            CHECK (NOT jsonb_path_exists(tool_calls,
                '$[*] ? (!((@.result == "refused" && @.refused_by.type() == "string" && '
                '(@.refused_by == "identity_check" || @.refused_by == "policy" || '
                '@.refused_by == "workload_grant" || @.refused_by == "database_guard" || '
                '@.refused_by == "business_rule" || @.refused_by == "runtime_guard")) || '
                '(@.result != "refused" && '
                '(!exists(@.refused_by) || @.refused_by.type() == "null"))))'))
        CONSTRAINT agent_audit_log_tool_calls_tool
            CHECK (NOT jsonb_path_exists(tool_calls,
                '$[*] ? (!(@.tool.type() == "string" && @.tool != ""))'))
        CONSTRAINT agent_audit_log_tool_calls_refusal_code
            CHECK (NOT jsonb_path_exists(tool_calls,
                '$[*] ? (exists(@.refusal_code) && !((@.refusal_code.type() == "string" && '
                '@.refusal_code != "") || @.refusal_code.type() == "null"))'))
        CONSTRAINT agent_audit_log_tool_calls_access_audit_id
            CHECK (NOT jsonb_path_exists(tool_calls,
                '$[*] ? (!((@.refused_by == "workload_grant" && '
                '@.access_audit_id.type() == "string" && @.access_audit_id != "") || '
                '(!(@.refused_by == "workload_grant") && '
                '(!exists(@.access_audit_id) || @.access_audit_id.type() == "null"))))'))
        CONSTRAINT agent_audit_log_tool_calls_decision
            CHECK (tool_calls IS NULL OR authorization_decision IS NOT DISTINCT FROM (CASE
                WHEN jsonb_path_exists(tool_calls,
                    '$[*] ? (@.refused_by == "identity_check" || @.refused_by == "policy" || '
                    '@.refused_by == "workload_grant" || @.refused_by == "database_guard")')
                    THEN 'deny'
                WHEN jsonb_path_exists(tool_calls, '$[*] ? (@.result == "error")')
                    OR turn_error IS NOT NULL THEN 'error'
                ELSE 'allow' END)),
    ADD COLUMN IF NOT EXISTS turn_error TEXT
        CONSTRAINT agent_audit_log_turn_error_text
            CHECK (turn_error IS NULL OR
                (turn_error <> '' AND char_length(turn_error) <= 400))
        CONSTRAINT agent_audit_log_turn_error_decision
            CHECK (turn_error IS NULL OR authorization_decision IS DISTINCT FROM 'allow');

CREATE OR REPLACE VIEW agent_iam_audit AS
SELECT audit_id,
    ran_at,
    agent_name,
    operation,
    traveler_id,
    authorization_provider,
    authorization_subject,
    authorization_decision,
    rls_traveler,
    rls_agent_type,
    iam_identity,
    rows_returned,
    tool_calls,
    turn_error
FROM agent_audit_log
ORDER BY ran_at DESC;
