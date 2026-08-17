# MeridianConcierge Runtime

This AgentCore Runtime hosts Meridian's managed concierge decision step. The
backend supplies the authenticated traveler request, authorized memory context,
and live AgentCore Gateway candidates. Runtime returns the traveler-facing
recommendation that the backend persists and displays.

# Layout

The generated application code lives at the agent root directory. At the root, there is a `.gitignore` file, an
`agentcore/` folder which represents the configurations and state associated with this project. Other `agentcore`
commands like `deploy`, `dev`, and `invoke` rely on the configuration stored here.

## Agent Root

The main entrypoint to your app is defined in `main.py`. Using the AgentCore SDK `@app.entrypoint` decorator, this
file defines a Starlette ASGI app with the chosen Agent framework SDK running within.

`model/load.py` instantiates your chosen model provider.

## Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `LOCAL_DEV` | No | Set to `1` to use `.env.local` instead of AgentCore Identity |

# Developing locally

If installation was successful, a virtual environment is already created with dependencies installed.

Run `source .venv/bin/activate` before developing.

`agentcore dev` starts the Runtime-compatible local server on port 8080.

In a new terminal, you can invoke that server with:

Invoke it with a `concierge_turn` JSON payload containing `prompt`,
`traveler_id`, `memory_context`, and `candidates`.

# Deployment

After providing credentials, `agentcore deploy` will deploy your project into Amazon Bedrock AgentCore.

After deployment, the Meridian backend invokes it through
`bedrock-agentcore:InvokeAgentRuntime` and consumes the returned JSON decision.
