"""Preserve AWS credential providers for trusted MCP subprocesses."""

import os


def aws_subprocess_env() -> dict[str, str]:
    """Forward provider settings without copying unrelated application secrets.

    MCP's default stdio environment omits AWS settings. Container-role and
    web-identity providers must remain available so child SDKs can obtain and
    refresh their own credentials, just as the parent SDK does.
    """
    names = (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_CONFIG_FILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_EC2_METADATA_DISABLED",
        "AWS_CA_BUNDLE",
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
    )
    return {name: value for name in names if (value := os.environ.get(name))}
