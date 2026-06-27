"""
RDS Data API Client for Meridian.

Provides database access through AWS RDS Data API for Aurora Serverless v2.
This is used when the cluster is in a private VPC and not directly accessible.

AWS docs:
  - RDS Data API overview:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - ExecuteStatement (boto3):
    https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_ExecuteStatement.html
  - BeginTransaction (RLS-scoped sessions in Phase 4):
    https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_BeginTransaction.html
  - Storing Aurora credentials in Secrets Manager:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-secrets-manager.html
"""

import os
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import Optional, List, Any, Dict
from decimal import Decimal

logger = logging.getLogger(__name__)

import boto3


class RDSDataClient:
    """
    RDS Data API client for Aurora PostgreSQL.
    
    Uses the RDS Data API to execute SQL statements against Aurora Serverless v2
    clusters that are not directly accessible (e.g., in private VPCs).
    """
    
    def __init__(
        self,
        cluster_arn: Optional[str] = None,
        secret_arn: Optional[str] = None,
        database: Optional[str] = None,
        region: Optional[str] = None
    ):
        """
        Initialize RDS Data API client.
        
        Args:
            cluster_arn: Aurora cluster ARN (defaults to AURORA_CLUSTER_ARN env var)
            secret_arn: Secrets Manager ARN (defaults to AURORA_SECRET_ARN env var)
            database: Database name (defaults to AURORA_DATABASE env var)
            region: AWS region (defaults to AWS_DEFAULT_REGION env var)
        """
        self.cluster_arn = cluster_arn or os.getenv("AURORA_CLUSTER_ARN")
        self.secret_arn = secret_arn or os.getenv("AURORA_SECRET_ARN")
        self.database = database or os.getenv("AURORA_DATABASE", "meridian")
        self.region = region or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        
        self.client = boto3.client('rds-data', region_name=self.region)
    
    def _format_parameters(self, params: Optional[tuple]) -> List[Dict]:
        """Convert tuple parameters to RDS Data API format."""
        if not params:
            return []
        
        formatted = []
        for i, value in enumerate(params):
            param: Dict[str, Any] = {"name": f"p{i}"}
            
            if value is None:
                param["value"] = {"isNull": True}
            elif isinstance(value, bool):
                param["value"] = {"booleanValue": value}
            elif isinstance(value, int):
                param["value"] = {"longValue": value}
            elif isinstance(value, float):
                param["value"] = {"doubleValue": value}
            elif isinstance(value, Decimal):
                param["value"] = {"stringValue": str(value)}
                param["typeHint"] = "DECIMAL"
            elif isinstance(value, (list, dict)):
                param["value"] = {"stringValue": json.dumps(value)}
            else:
                param["value"] = {"stringValue": str(value)}
            
            formatted.append(param)
        
        return formatted
    
    def _convert_sql_placeholders(self, sql: str, param_count: int) -> str:
        """Convert %s placeholders to :pN named parameters."""
        result = sql
        for i in range(param_count):
            result = result.replace("%s", f":p{i}", 1)
        return result
    
    def _parse_value(self, field: Dict) -> Any:
        """Parse a single field value from RDS Data API response."""
        if "isNull" in field and field["isNull"]:
            return None
        if "stringValue" in field:
            return field["stringValue"]
        if "longValue" in field:
            return field["longValue"]
        if "doubleValue" in field:
            return field["doubleValue"]
        if "booleanValue" in field:
            return field["booleanValue"]
        if "arrayValue" in field:
            return self._parse_array(field["arrayValue"])
        return None
    
    def _parse_array(self, array_value: Dict) -> List:
        """Parse array value from RDS Data API response."""
        if "stringValues" in array_value:
            return array_value["stringValues"]
        if "longValues" in array_value:
            return array_value["longValues"]
        if "doubleValues" in array_value:
            return array_value["doubleValues"]
        if "booleanValues" in array_value:
            return array_value["booleanValues"]
        if "arrayValues" in array_value:
            return [self._parse_array(v) for v in array_value["arrayValues"]]
        return []
    
    def _parse_response(self, response: Dict, column_names: List[str]) -> List[Dict]:
        """Parse RDS Data API response into list of dictionaries."""
        records = response.get("records", [])
        results = []
        
        for record in records:
            row = {}
            for i, field in enumerate(record):
                if i < len(column_names):
                    value = self._parse_value(field)
                    # Try to parse JSON strings for JSONB columns
                    if isinstance(value, str) and column_names[i] in [
                        'durations', 'availability', 'highlights',
                        'loyalty_programs', 'packages_shown',
                    ]:
                        try:
                            value = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    row[column_names[i]] = value
            results.append(row)
        
        return results
    
    async def execute(
        self,
        query: str,
        params: Optional[tuple] = None,
        transaction_id: Optional[str] = None,
    ) -> List[Dict]:
        """
        Execute a query and return results.

        Args:
            query: SQL query string with %s placeholders
            params: Optional query parameters
            transaction_id: Optional RDS Data API transaction id.  When set, the
                statement runs inside an existing transaction so transaction-
                local settings (e.g. ``SET LOCAL`` / ``set_config(..., true)``)
                stay in scope across calls.

        Returns:
            List of result rows as dictionaries
        """
        # Convert placeholders
        param_count = query.count("%s")
        sql = self._convert_sql_placeholders(query, param_count)
        parameters = self._format_parameters(params)

        kwargs: Dict[str, Any] = dict(
            resourceArn=self.cluster_arn,
            secretArn=self.secret_arn,
            database=self.database,
            sql=sql,
            parameters=parameters,
            includeResultMetadata=True,
        )
        if transaction_id:
            kwargs["transactionId"] = transaction_id
        response = self.client.execute_statement(**kwargs)
        
        # Extract column names from metadata
        column_metadata = response.get("columnMetadata", [])
        column_names = [col.get("name", f"col{i}") for i, col in enumerate(column_metadata)]
        
        return self._parse_response(response, column_names)
    
    async def execute_one(
        self,
        query: str,
        params: Optional[tuple] = None,
        transaction_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """Execute a query and return a single result row, or ``None``."""
        results = await self.execute(query, params, transaction_id=transaction_id)
        return results[0] if results else None

    def begin_transaction(self) -> str:
        """Open an RDS Data API transaction and return its id."""
        response = self.client.begin_transaction(
            resourceArn=self.cluster_arn,
            secretArn=self.secret_arn,
            database=self.database,
        )
        return response["transactionId"]

    def commit_transaction(self, transaction_id: str) -> None:
        self.client.commit_transaction(
            resourceArn=self.cluster_arn,
            secretArn=self.secret_arn,
            transactionId=transaction_id,
        )

    def rollback_transaction(self, transaction_id: str) -> None:
        try:
            self.client.rollback_transaction(
                resourceArn=self.cluster_arn,
                secretArn=self.secret_arn,
                transactionId=transaction_id,
            )
        except Exception:
            pass

    @asynccontextmanager
    async def scoped_session(
        self,
        traveler_id: Optional[str] = None,
        agent_type: Optional[str] = None,
    ):
        """
        Open a transaction with RLS session variables set.

        Uses RDS Data API ``BeginTransaction`` so ``SET LOCAL`` / ``set_config``
        GUCs stay scoped to this transaction — see ``examples/rls_for_agents.sql``.

        AWS docs:
          - BeginTransaction:
            https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_BeginTransaction.html
          - CommitTransaction:
            https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_CommitTransaction.html

        Usage::

            async with client.scoped_session(traveler_id="trv_x") as tx:
                rows = await client.execute("SELECT ...", transaction_id=tx)

        Inside the block, ``app.current_traveler_id`` (and optionally
        ``app.agent_type``) are pinned for the lifetime of the transaction.
        Aurora RLS policies on ``traveler_preferences`` and friends will
        filter rows accordingly.

        IMPORTANT — why we SET LOCAL ROLE: the RDS Data API connects as the
        DB user its secret maps to. Ours is the cluster master user
        (meridian_admin), and on this Aurora cluster the master role is NOT
        subject to RLS — row_security_active() returns false for it — even
        though the tables are ENABLE + FORCE'd and the role is neither
        superuser nor BYPASSRLS. (We don't assert the exact Aurora internal;
        the observable fact is what matters: master sees 22 rows, a
        non-privileged role sees 17, same GUC and policy.) We therefore step
        down into the least-privilege ``meridian_app`` role for the lifetime of
        this transaction — AFTER setting the GUCs — so the policies engage for
        the queries that run inside the block. See examples/rls_app_role.sql.

        The role switch is required by default. If ``meridian_app`` is not
        present (e.g. the migration hasn't been applied), scoped reads/writes
        fail closed instead of continuing on the master connection. For one-off
        local admin diagnostics only, set ``RLS_ALLOW_UNSCOPED_FALLBACK=1``.
        """
        app_role = os.getenv("RLS_APP_ROLE", "meridian_app").strip()
        allow_unscoped = os.getenv("RLS_ALLOW_UNSCOPED_FALLBACK", "").lower() in {
            "1",
            "true",
            "yes",
        }
        tx = self.begin_transaction()
        try:
            # Force row_security ON for this transaction (belt-and-suspenders;
            # harmless once the role switch below does the real work).
            await self.execute("SET LOCAL row_security = on", transaction_id=tx)
            # Set the GUCs FIRST, while still on the privileged connection, so
            # set_config is guaranteed to succeed.
            if traveler_id is not None:
                await self.execute(
                    "SELECT set_config('app.current_traveler_id', %s, true)",
                    (traveler_id,),
                    transaction_id=tx,
                )
            if agent_type is not None:
                await self.execute(
                    "SELECT set_config('app.agent_type', %s, true)",
                    (agent_type,),
                    transaction_id=tx,
                )
            # Step off the privileged master role for the rest of this
            # transaction by switching to the least-privilege app role (which
            # IS subject to RLS). SET LOCAL ROLE is transaction-scoped and
            # reverts on commit/rollback.
            if not app_role:
                msg = (
                    "RLS_APP_ROLE is empty; refusing to run scoped_session "
                    "without a least-privilege role."
                )
                if allow_unscoped:
                    logger.warning("%s RLS will NOT filter.", msg)
                else:
                    raise RuntimeError(
                        f"{msg} Set RLS_APP_ROLE=meridian_app or apply "
                        "examples/rls_app_role.sql."
                    )
            else:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", app_role):
                    raise RuntimeError(
                        f"Invalid RLS_APP_ROLE {app_role!r}; expected an unquoted "
                        "PostgreSQL identifier such as meridian_app."
                    )
                try:
                    await self.execute(f"SET LOCAL ROLE {app_role}", transaction_id=tx)
                except Exception as exc:  # role missing / not granted
                    msg = (
                        f"scoped_session: could not SET LOCAL ROLE {app_role} "
                        f"({str(exc)[:120]}). Apply examples/rls_app_role.sql."
                    )
                    if allow_unscoped:
                        logger.warning("%s Continuing on master connection; RLS will NOT filter.", msg)
                    else:
                        raise RuntimeError(msg) from exc
            yield tx
            self.commit_transaction(tx)
        except Exception:
            self.rollback_transaction(tx)
            raise


# Global client instance
_client: Optional[RDSDataClient] = None


def get_rds_data_client() -> RDSDataClient:
    """Get or create the global RDS Data API client instance."""
    global _client
    if _client is None:
        _client = RDSDataClient()
    return _client
