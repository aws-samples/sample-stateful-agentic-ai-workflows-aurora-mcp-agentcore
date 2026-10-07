"""Harness target: return exactly what the Gateway handed this Lambda."""


def lambda_handler(event, context):
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    return {"event": event, "custom": {key: str(value) for key, value in custom.items()}}
