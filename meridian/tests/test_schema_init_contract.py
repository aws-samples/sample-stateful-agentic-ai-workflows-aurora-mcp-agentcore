"""Fresh-schema initialization must create functions before granting them."""

from scripts.init_aurora_schema import RLS_APP_ROLE_PATH, RLS_PATH


def test_courtesy_hold_grant_runs_after_function_creation() -> None:
    role_sql = RLS_APP_ROLE_PATH.read_text()
    rls_sql = RLS_PATH.read_text()
    function_name = "create_courtesy_hold"
    grant = "GRANT EXECUTE ON FUNCTION create_courtesy_hold"

    assert function_name not in role_sql
    assert rls_sql.index("CREATE OR REPLACE FUNCTION create_courtesy_hold") < rls_sql.index(
        grant
    )
