"""A throwaway AgentCore Gateway that answers the questions the documentation leaves open.

``scripts/run_gateway_harness.py`` creates a separate Gateway named ``meridian-throwaway-<id>``,
runs a fixed set of probes with real Cognito tokens, prints a verdict table and deletes everything
it created. It never touches the real ``meridian-aurora`` Gateway.
"""
