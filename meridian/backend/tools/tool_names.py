"""Tool names as the tool contracts define them, without the Gateway target prefix.

AgentCore Gateway lists each tool as ``<Target>___<tool>``, for example
``MeridianHolds___create_courtesy_hold``. Audit rows record the contract name alone, so a
row reads the same whichever Gateway target served the tool.

AWS docs:
  - Gateway tool naming:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-tool-naming.html
"""

TARGET_SEPARATOR = "___"


def bare_tool_name(name: str) -> str:
    """The contract name of a Gateway tool: ``name`` with one ``<Target>___`` prefix removed.

    Only the first separator is a prefix, because a target name cannot contain an underscore
    while a tool name can. A name with no target before the separator, or no tool after it,
    is returned unchanged rather than guessed at.

    Args:
        name: A tool name as the Gateway or the Runtime reported it.

    Returns:
        The name without its target prefix, or ``name`` itself when it carries none.
    """
    target, separator, bare = name.partition(TARGET_SEPARATOR)
    return bare if separator and target and bare else name
